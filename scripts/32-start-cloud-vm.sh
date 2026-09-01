#!/usr/bin/env bash
# 开云主机,并把"每次开机都要重做一遍"的三件杂活一起做掉:
#   1. 拿到新的公网 IP —— **这台是抢占式实例,IP 不是固定 EIP,每次开机
#      都可能变**(2026-08-22 一天之内就变过);
#   2. 重建 SSH 隧道(6443 故意不对公网开放,只能走隧道);
#   3. 刷新本机 kubeconfig。
#
# 为什么值得单独写个脚本:有 26-(停机)一直没有对应的开机脚本,每次开机
# 都是人工查 IP → 手敲 ssh -f -N -L → 有时还要重取 kubeconfig,而且这三步
# 里任何一步忘了,后面所有 kubectl 都会以很难懂的方式失败(connection
# refused / TLS handshake timeout / x509 证书对不上)。这类"每次都要做、
# 做错了报错还不直观"的操作就该脚本化。
#
# **kubeconfig 什么时候必须重取**:集群被重装过(k3s 重新安装会生成全新的
# CA),旧 kubeconfig 会报 x509 证书错误。脚本默认自动比对,不一致就重取。
#
# 用法:
#   ./scripts/32-start-cloud-vm.sh
#   CLOUD_VM_INSTANCE_ID=<实例ID> CLOUD_VM_REGION=<地域> ./scripts/32-start-cloud-vm.sh
#
# ⚠️ 这台机器上还跑着另一个项目(见 CLAUDE.md "cloud-full 那台云主机不是
# 我们独占的"),开机本身无害,但停机前必须先确认。
set -uo pipefail

# 实例身份来自 environments/cloud-full/vm.env(开机/停机/迁移共用一份,
# 见那个文件顶部的说明)。环境变量优先,方便临时指向别的实例。
_VM_ENV="$(dirname "$0")/../environments/cloud-full/vm.env"
[ -f "$_VM_ENV" ] && . "$_VM_ENV"
CLOUD_VM_INSTANCE_ID="${CLOUD_VM_INSTANCE_ID:-}"
# **读不到就停,不退回写死的值。** 兜底一个硬编码的实例 ID,意味着 vm.env
# 缺失时脚本会静默地对另一台(可能已经删掉的)实例动手 —— 停机脚本对着不
# 存在的实例"成功"返回,而真正在跑的机器一直烧钱。宁可报错。
if [ -z "${CLOUD_VM_INSTANCE_ID:-}" ]; then
  echo "!! 读不到实例 ID。应该来自 environments/cloud-full/vm.env," >&2
  echo "   或者用 CLOUD_VM_INSTANCE_ID=<id> 显式指定。" >&2
  exit 1
fi

CLOUD_VM_REGION="${CLOUD_VM_REGION:-cn-wulanchabu}"
ALIYUN_PROFILE="${ALIYUN_PROFILE:-cloud-full}"
CLOUD_VM_USER="${CLOUD_VM_USER:-root}"
CLOUD_VM_KEY="${CLOUD_VM_KEY:-$HOME/.ssh/cloud-full-key.pem}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-$HOME/.kube/cloud-full-config}"
LOCAL_PORT="${LOCAL_PORT:-16443}"

cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/start-cloud-vm.log"
log() { echo "$*"; echo "$(date -u +%FT%TZ) $*" >> "$LOG_FILE"; }

describe() {
  aliyun ecs DescribeInstances --profile "$ALIYUN_PROFILE" \
    --InstanceIds "[\"${CLOUD_VM_INSTANCE_ID}\"]" 2>>"$LOG_FILE"
}

status_of() { describe | python3 -c "import json,sys; print(json.load(sys.stdin)['Instances']['Instance'][0]['Status'])"; }
ip_of() {
  describe | python3 -c "
import json,sys
i=json.load(sys.stdin)['Instances']['Instance'][0]
ips = i.get('PublicIpAddress',{}).get('IpAddress') or []
eip = i.get('EipAddress',{}).get('IpAddress')
print(ips[0] if ips else (eip or ''))"
}

log "=== 开机 ${CLOUD_VM_INSTANCE_ID}(${CLOUD_VM_REGION})==="
st="$(status_of)"
log "--> 当前状态:${st}"

if [ "$st" = "Stopped" ]; then
  # **抢占式实例开不起来是常态,不是异常。** 2026-08-30 实测撞到:
  # `OperationDenied.NoStock` —— 这台机器所在可用区(cn-wulanchabu-a)的
  # g9i 售罄,而且**同代族的 r9i/c9i 一起售罄**(改规格也解决不了,阿里云
  # 只允许在同代族之间换)。库存是分钟级波动的,所以这里支持重试等待:
  #
  #   WAIT_FOR_STOCK_MIN=30 ./scripts/32-start-cloud-vm.sh
  #
  # 默认不等(WAIT_FOR_STOCK_MIN=0),因为大多数时候是有库存的,静默等
  # 半小时不如立刻告诉人"现在开不了"。
  WAIT_MIN="${WAIT_FOR_STOCK_MIN:-0}"
  deadline=$(( $(date +%s) + WAIT_MIN * 60 ))
  attempt=0
  while :; do
    attempt=$((attempt + 1))
    if aliyun ecs StartInstance --profile "$ALIYUN_PROFILE" \
         --InstanceId "$CLOUD_VM_INSTANCE_ID" >>"$LOG_FILE" 2>&1; then
      break
    fi
    if ! grep -q "NoStock" "$LOG_FILE"; then
      log "!! StartInstance 调用失败(不是库存问题),详见 ${LOG_FILE}"
      exit 1
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      log "!! 开不了机:可用区库存售罄(OperationDenied.NoStock),已试 ${attempt} 次"
      log "   这是抢占式实例的固有风险,不是配置问题。可以:"
      log "     1) 等一会儿再试,或者 WAIT_FOR_STOCK_MIN=30 ./scripts/32-start-cloud-vm.sh"
      log "     2) 查同可用区还有什么规格可用:"
      log "        aliyun ecs DescribeAvailableResource --RegionId <region> \\"
      log "          --DestinationResource InstanceType --ZoneId <zone> \\"
      log "          --InstanceChargeType PostPaid --SpotStrategy SpotAsPriceGo"
      log "        **注意改规格只能在同代族之间换**(g9i↔r9i↔c9i),"
      log "        跨族会报 InvalidInstanceType.ValueNotSupported"
      log "     3) 换可用区要走「快照→建新盘→挂载」那条完整路径,不是改个参数"
      exit 1
    fi
    log "--> 库存售罄,${attempt} 次尝试;还剩 $(( (deadline - $(date +%s)) / 60 )) 分钟继续重试"
    sleep 60
  done
  log "--> 已发起开机,等实例变 Running"
elif [ "$st" = "Running" ]; then
  log "--> 已经在运行,跳过开机"
else
  log "--> 状态是 ${st}(Starting/Stopping 之类),不重复发指令,直接等"
fi

for _ in $(seq 1 60); do
  [ "$(status_of)" = "Running" ] && break
  sleep 10
done
[ "$(status_of)" = "Running" ] || { log "!! 等了 10 分钟还没 Running,自己去控制台看看"; exit 1; }

IP="$(ip_of)"
[ -n "$IP" ] || { log "!! 拿不到公网 IP"; exit 1; }
log "--> Running,公网 IP:${IP}"

# ---- /etc/hosts 里的域名指向对不对 ----
#
# **2026-08-27 真实踩到,而且不只是"打不开"这么简单。** zhenghe 打开
# http://portal.local-lite.test:32460/ 报 500,第一反应是自己 VPN 的问题。
# 实际是 /etc/hosts 里还写着上上次开机的 IP(8.130.69.252),而这台是抢占式
# 实例、每次开机 IP 都变——那个 IP 早就被回收、多半已经分给别人的实例了。
#
# 危害不止是打不开:**浏览器会把 *.local-lite.test 的 cookie 一起发给那个
# 陌生 IP**。所以这不是"体验问题",是每次开机后都存在的一个小信息泄露面。
#
# 这里只检测 + 给出可直接粘贴的命令,不默认动 /etc/hosts:改它要 sudo,
# 会弹密码,脚本在无人值守时跑不下去。要自动改就带 UPDATE_HOSTS=1。
HOSTS_FILE="${HOSTS_FILE:-/etc/hosts}"
PLATFORM_HOST_LINE="$(grep -nE "^[0-9.]+ .*local-lite\.test" "$HOSTS_FILE" 2>/dev/null | head -1 || true)"
if [ -n "$PLATFORM_HOST_LINE" ]; then
  OLD_IP="$(echo "$PLATFORM_HOST_LINE" | sed -E 's/^[0-9]+:([0-9.]+) .*/\1/')"
  if [ "$OLD_IP" != "$IP" ]; then
    log "!! ${HOSTS_FILE} 里 *.local-lite.test 还指着 ${OLD_IP},而这次的 IP 是 ${IP}"
    log "   不更新的话浏览器打开任何 *.local-lite.test 都会连到**别人的机器**"
    log "   (那个 IP 已经被回收重分配),而且会把 cookie 一起发过去。"
    if [ "${UPDATE_HOSTS:-0}" = "1" ]; then
      log "   UPDATE_HOSTS=1,正在更新(需要 sudo)..."
      sudo sed -i '' -E "s/^${OLD_IP}( .*local-lite\.test)/${IP}\1/" "$HOSTS_FILE" \
        && log "   已更新为 ${IP}"
    else
      log "   要更新,跑这一条(或者给这个脚本加 UPDATE_HOSTS=1):"
      log "     sudo sed -i '' -E 's/^${OLD_IP}( .*local-lite\\.test)/${IP}\\1/' ${HOSTS_FILE}"
    fi
  else
    log "--> ${HOSTS_FILE} 里的 *.local-lite.test 已经指向 ${IP},不用改"
  fi
fi

log "--> 等 SSH 起来(最多 5 分钟)"
ssh_ok=0
for _ in $(seq 1 30); do
  if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 -o BatchMode=yes \
       -i "$CLOUD_VM_KEY" "${CLOUD_VM_USER}@${IP}" true 2>>"$LOG_FILE"; then
    ssh_ok=1; break
  fi
  sleep 10
done
if [ "$ssh_ok" != "1" ]; then
  # IP 复用是真实发生过的:换了机器但 IP 是别人用过的,本机 known_hosts
  # 里存着旧指纹,SSH 直接拒绝连接。这里不自动清——清 known_hosts 等于
  # 放弃中间人防护,应该由人确认 IP 归属之后手动执行下面这条。
  log "!! SSH 连不上。如果报的是 host key 不匹配,先确认这个 IP 确实是你的实例,再执行:"
  log "   ssh-keygen -R ${IP}"
  exit 1
fi

log "--> 重建 SSH 隧道 localhost:${LOCAL_PORT} -> 127.0.0.1:6443"
pkill -f "ssh -f -N -L ${LOCAL_PORT}:127.0.0.1:6443" 2>/dev/null
sleep 1
ssh -f -N -L "${LOCAL_PORT}:127.0.0.1:6443" -o StrictHostKeyChecking=no \
  -o ServerAliveInterval=30 -i "$CLOUD_VM_KEY" "${CLOUD_VM_USER}@${IP}" \
  || { log "!! 建隧道失败"; exit 1; }

log "--> 核对 kubeconfig(集群重装过的话 CA 会变,旧的会报 x509)"
TMP_KC="$(mktemp)"
scp -q -o StrictHostKeyChecking=no -i "$CLOUD_VM_KEY" \
  "${CLOUD_VM_USER}@${IP}:/etc/rancher/k3s/k3s.yaml" "$TMP_KC" 2>>"$LOG_FILE" \
  || { log "!! 取不到远端 kubeconfig"; rm -f "$TMP_KC"; exit 1; }
sed -i '' "s#https://127.0.0.1:6443#https://127.0.0.1:${LOCAL_PORT}#" "$TMP_KC" 2>/dev/null \
  || sed -i "s#https://127.0.0.1:6443#https://127.0.0.1:${LOCAL_PORT}#" "$TMP_KC"

if [ -f "$KUBECONFIG_PATH" ] && cmp -s "$TMP_KC" "$KUBECONFIG_PATH"; then
  log "--> kubeconfig 没变,不动它"
  rm -f "$TMP_KC"
else
  [ -f "$KUBECONFIG_PATH" ] && cp "$KUBECONFIG_PATH" "${KUBECONFIG_PATH}.bak"
  mkdir -p "$(dirname "$KUBECONFIG_PATH")"
  mv "$TMP_KC" "$KUBECONFIG_PATH"
  chmod 600 "$KUBECONFIG_PATH"
  log "--> kubeconfig 已更新(旧的备份成 ${KUBECONFIG_PATH}.bak)"
fi

if KUBECONFIG="$KUBECONFIG_PATH" kubectl get nodes >>"$LOG_FILE" 2>&1; then
  log "--> 连通性 OK"
  KUBECONFIG="$KUBECONFIG_PATH" kubectl get nodes
else
  log "!! kubectl 连不上,详见 ${LOG_FILE}"
  exit 1
fi

log ""
log "=== 好了。记得用完停机:./scripts/26-stop-cloud-vm-economical.sh ==="
log "    export KUBECONFIG=${KUBECONFIG_PATH}"
