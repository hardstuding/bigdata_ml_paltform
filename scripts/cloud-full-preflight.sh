#!/usr/bin/env bash
# cloud-full 云主机的开机/继续操作前置检查(响应 2026-08-15 Codex review
# 的 P0-1,见 ADR-055)。只读,不改任何本地/远程状态——这个脚本的作用是
# 在花钱之前(或者继续让计费实例空转之前)给一个明确的 READY/NOT READY
# 判断,不是自动帮你做决定要不要开机/停机,那个决定始终是人来做。
#
# 故意没做的事(如实说明,不是遗漏):不估算云主机预计费用——没有稳定、
# 免费的阿里云计价 API,做了也是编数字,不如不做,老实告诉你"自己看
# ECS 控制台的计费页面"。
#
# 用法:
#   CLOUD_VM_IP=<公网IP> CLOUD_VM_KEY=<私钥路径> ./scripts/cloud-full-preflight.sh
#   不设置 CLOUD_VM_IP/CLOUD_VM_KEY 时,只做本地镜像缓存的检查,跳过远程部分。
set -uo pipefail  # 故意不用 -e:这个脚本要检查完所有项再汇总结果,单项失败不能让脚本提前退出

cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/cloud-full-preflight.log"
echo "=== cloud-full-preflight $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

FAIL=0
WARN=0
note_fail() { echo "  [FAIL] $1" | tee -a "$LOG_FILE"; FAIL=$((FAIL+1)); }
note_warn() { echo "  [WARN] $1" | tee -a "$LOG_FILE"; WARN=$((WARN+1)); }
note_ok()   { echo "  [OK]   $1" | tee -a "$LOG_FILE"; }

CACHE_DIR="${CACHE_DIR:-image-cache-amd64}"

echo "==> 1. 本地镜像缓存完整性"
if [ ! -f "${CACHE_DIR}/manifest.txt" ]; then
  note_fail "${CACHE_DIR}/manifest.txt 不存在,还没导出过任何镜像"
else
  EXPECTED=$(python3 scripts/list-project-images.py --include-pending 2>/dev/null | wc -l | tr -d ' ')
  ACTUAL=$(grep -vc '^#' "${CACHE_DIR}/manifest.txt" 2>/dev/null || echo 0)
  echo "  期望镜像数(静态扫描 Application 配置): ${EXPECTED}"
  echo "  manifest 里已记录: ${ACTUAL}"
  if [ "$ACTUAL" -lt "$EXPECTED" ]; then
    note_warn "还差 $((EXPECTED - ACTUAL)) 个镜像没导出完(不是失败,是进度,继续往下检查已导出部分是否完整)"
  else
    note_ok "manifest 条目数已经达到/超过静态扫描的期望数"
  fi

  BAD=0
  CHECKED=0
  while IFS= read -r line; do
    case "$line" in \#*|"") continue ;; esac
    fname="${line##* }"
    fpath="${CACHE_DIR}/${fname}"
    CHECKED=$((CHECKED+1))
    if [ ! -f "$fpath" ]; then
      note_fail "manifest 里有记录但文件不存在: ${fpath}"
      BAD=$((BAD+1))
      continue
    fi
    if ! gzip -t "$fpath" 2>>"$LOG_FILE"; then
      note_fail "gzip 完整性校验失败(文件可能损坏/截断): ${fpath}"
      BAD=$((BAD+1))
    fi
  done < "${CACHE_DIR}/manifest.txt"
  if [ "$BAD" -eq 0 ] && [ "$CHECKED" -gt 0 ]; then
    note_ok "已导出的 ${CHECKED} 个文件全部通过 gzip 完整性校验"
  fi
fi

if [ -z "${CLOUD_VM_IP:-}" ]; then
  echo
  echo "==> 未设置 CLOUD_VM_IP,跳过远程检查(只做了本地缓存检查)"
else
  CLOUD_VM_KEY="${CLOUD_VM_KEY:?设置了 CLOUD_VM_IP 就必须同时设置 CLOUD_VM_KEY}"
  SSH="ssh -i ${CLOUD_VM_KEY} -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new root@${CLOUD_VM_IP}"

  echo
  echo "==> 2. SSH 可达性"
  if $SSH "echo ok" >/dev/null 2>>"$LOG_FILE"; then
    note_ok "SSH 连接正常: ${CLOUD_VM_IP}"
  else
    note_fail "SSH 连不上: ${CLOUD_VM_IP}(检查实例是不是已经停机/安全组是否放行)"
  fi

  echo
  echo "==> 3. 远端磁盘空间"
  DISK_INFO=$($SSH "df -h /data 2>/dev/null | tail -1" 2>>"$LOG_FILE")
  if [ -n "$DISK_INFO" ]; then
    echo "  /data: ${DISK_INFO}"
    AVAIL_PCT=$(echo "$DISK_INFO" | awk '{print $5}' | tr -d '%')
    if [ -n "$AVAIL_PCT" ] && [ "$AVAIL_PCT" -ge 90 ] 2>/dev/null; then
      note_fail "/data 已用 ${AVAIL_PCT}%,剩余空间可能不够继续传输"
    else
      note_ok "/data 磁盘空间充足"
    fi
  else
    note_warn "拿不到远端磁盘信息(SSH 本身如果已经失败,这条也会跟着失败,看上面那条)"
  fi

  echo
  echo "==> 4. 远端架构 / Docker / k3s 状态"
  ARCH=$($SSH "uname -m" 2>>"$LOG_FILE")
  if [ "$ARCH" = "x86_64" ]; then
    note_ok "远端架构: x86_64(和本地导出的 amd64 镜像缓存匹配)"
  else
    note_fail "远端架构是 '${ARCH}',和 amd64 镜像缓存不匹配,不能直接 docker load 使用"
  fi
  if $SSH "docker info >/dev/null 2>&1"; then
    note_ok "远端 Docker daemon 正常"
  else
    note_fail "远端 Docker 没有正常运行"
  fi
  NODE_STATUS=$($SSH "k3s kubectl get nodes --no-headers 2>/dev/null | awk '{print \$2}'" 2>>"$LOG_FILE")
  if [ "$NODE_STATUS" = "Ready" ]; then
    note_ok "远端 k3s 节点 Ready"
  else
    note_fail "远端 k3s 节点状态异常: '${NODE_STATUS:-拿不到}'"
  fi

  echo
  echo "==> 5. 当前是否有能立刻在云端执行的工作"
  if [ -f "${CACHE_DIR}/manifest.txt" ]; then
    LOADED=$($SSH "docker images -q 2>/dev/null | wc -l" 2>>"$LOG_FILE" | tr -d ' ')
    TOTAL=$(grep -vc '^#' "${CACHE_DIR}/manifest.txt" 2>/dev/null || echo 0)
    echo "  远端已加载镜像数(粗略,含 k3s/系统组件自身用到的镜像,不是精确对应 manifest): ${LOADED}"
    echo "  本地 manifest 总条目: ${TOTAL}"
    if [ "$ACTUAL" -gt "$LOADED" ] 2>/dev/null; then
      note_ok "本地已导出但远端还没灌入的镜像还有得传,可以继续跑 scripts/22-load-image-cache-remote.sh"
    else
      note_warn "看起来本地已导出的都已经传完了,当前没有'继续传镜像'这类立即可执行的云端任务——如果也没有其它队列中的任务,考虑停机等下一批本地导出完成"
    fi
  fi
fi

echo
echo "================================"
if [ "$FAIL" -gt 0 ]; then
  echo "结论: NOT READY(${FAIL} 项失败,${WARN} 项警告)—— 不建议现在开机/继续在云主机上花钱做下一步,先处理上面标 [FAIL] 的项" | tee -a "$LOG_FILE"
  exit 1
else
  echo "结论: READY(${WARN} 项警告,0 项失败)—— 可以继续,警告项自行判断是否需要处理" | tee -a "$LOG_FILE"
  exit 0
fi
