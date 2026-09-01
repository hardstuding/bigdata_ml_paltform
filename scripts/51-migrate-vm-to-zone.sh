#!/usr/bin/env bash
# 把 cloud-full 那台云主机迁到另一个可用区。
#
# **为什么需要**:2026-09-01 撞到 cn-wulanchabu-a 的 g9i/r9i/c9i 竞价库存
# 连续 6 小时以上全部售罄,开不了机(`OperationDenied.NoStock`),而 b/c 区
# 一直有货、竞价价格还便宜一半(a 区 ¥1.396/时,b/c 区 ¥0.716/时)。
# 可用区不能改参数切换 —— 磁盘是绑定可用区的,必须走"整机镜像 → 新可用区
# 建实例"这条路。
#
# **这个脚本刻意不删任何东西。** 跑完之后旧实例和旧磁盘都还在,新实例验证
# 通过之前不动它们。删除是单独一步,由人确认后手动做(脚本最后会打印命令)。
# 理由:迁移失败时唯一能回去的路就是旧实例还在。
#
# 用法:
#   ./scripts/51-migrate-vm-to-zone.sh cn-wulanchabu-b
#   DRY_RUN=1 ./scripts/51-migrate-vm-to-zone.sh cn-wulanchabu-b   # 只看要做什么
#
# 前置:实例必须是 Stopped(整机镜像要求关机,否则镜像里的磁盘状态不一致)。
set -euo pipefail

TARGET_ZONE="${1:-}"
[ -n "$TARGET_ZONE" ] || { echo "用法: $0 <目标可用区>  例如 cn-wulanchabu-b"; exit 1; }

REGION="${CLOUD_VM_REGION:-cn-wulanchabu}"
INSTANCE_ID="${CLOUD_VM_INSTANCE_ID:-i-0jlbped4h1959tp591pe}"
DRY_RUN="${DRY_RUN:-}"

mkdir -p logs
LOG_FILE="logs/51-migrate-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== 迁移到 $TARGET_ZONE  $(date -u +%FT%TZ) ==="

# **dry-run 不能用"打印代替执行"的通用包装。** 第一版那么写,结果下游要
# 解析这条命令的 JSON 输出,拿到的是 "[dry-run] aliyun ..." 这行文字,
# dry-run 在第一步之后就崩了 —— 一个走不完的 dry-run 等于没有 dry-run。
# 所以凡是要拿返回值的地方都写显式分支,给一个一眼能看出是假的占位 ID。

jq_py() { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }

# ---------------------------------------------------------------- 0. 前置检查
echo "==> 0/7 前置检查"
INFO=$(aliyun ecs DescribeInstances --InstanceIds "[\"$INSTANCE_ID\"]" --RegionId "$REGION")
STATUS=$(echo "$INFO" | jq_py "print(d['Instances']['Instance'][0]['Status'])")
SRC_ZONE=$(echo "$INFO" | jq_py "print(d['Instances']['Instance'][0]['ZoneId'])")
ITYPE=$(echo "$INFO" | jq_py "print(d['Instances']['Instance'][0]['InstanceType'])")
SGID=$(echo "$INFO" | jq_py "print(d['Instances']['Instance'][0]['SecurityGroupIds']['SecurityGroupId'][0])")
VPCID=$(echo "$INFO" | jq_py "print(d['Instances']['Instance'][0]['VpcAttributes']['VpcId'])")
KEYPAIR=$(echo "$INFO" | jq_py "print(d['Instances']['Instance'][0].get('KeyPairName',''))")
INAME=$(echo "$INFO" | jq_py "print(d['Instances']['Instance'][0].get('InstanceName',''))")
BW=$(echo "$INFO" | jq_py "print(d['Instances']['Instance'][0].get('InternetMaxBandwidthOut',100))")

echo "  实例 $INSTANCE_ID($INAME) 状态=$STATUS 规格=$ITYPE"
echo "  当前区=$SRC_ZONE -> 目标区=$TARGET_ZONE"
[ "$SRC_ZONE" != "$TARGET_ZONE" ] || { echo "!! 已经在目标可用区了,不用迁"; exit 1; }
[ "$STATUS" = "Stopped" ] || {
  echo "!! 实例必须先停机(现在是 $STATUS)。整机镜像要求关机,否则镜像里的"
  echo "   磁盘状态不一致 —— 那种损坏不会在建镜像时报错,只会在新实例起来"
  echo "   之后表现为文件系统异常。"
  exit 1; }

# 目标区有没有货。**先查再做** —— 镜像建完才发现目标区没货,等于白等半小时。
STOCK=$(aliyun ecs DescribeAvailableResource --RegionId "$REGION" \
  --DestinationResource InstanceType --ZoneId "$TARGET_ZONE" \
  --InstanceChargeType PostPaid --SpotStrategy SpotAsPriceGo 2>/dev/null \
  | python3 -c "
import json,sys
d=json.load(sys.stdin); t='$ITYPE'
for z in d.get('AvailableZones',{}).get('AvailableZone',[]):
    for r in z.get('AvailableResources',{}).get('AvailableResource',[]):
        for s in r.get('SupportedResources',{}).get('SupportedResource',[]):
            if s.get('Value')==t: print(s['Status']); raise SystemExit
print('Unknown')")
echo "  目标区 $ITYPE 竞价库存:$STOCK"
[ "$STOCK" = "Available" ] || { echo "!! 目标区没货,换一个区再来"; exit 1; }

# ---------------------------------------------------------------- 1. 交换机
echo "==> 1/7 目标可用区的交换机"
VSW=$(aliyun vpc DescribeVSwitches --RegionId "$REGION" --VpcId "$VPCID" --ZoneId "$TARGET_ZONE" \
  | jq_py "v=d['VSwitches']['VSwitch']; print(v[0]['VSwitchId'] if v else '')")
if [ -n "$VSW" ]; then
  echo "  已存在:$VSW"
else
  # 网段要在 VPC 网段内、且不和现有交换机重叠。现有的是 172.22.0.0/20,
  # 这里用 172.23.0.0/20。**不自动算网段** —— 算错了会建出一个和别人重叠
  # 的子网,而报错发生在很久以后。
  CIDR="${MIGRATE_VSW_CIDR:-172.23.0.0/20}"
  echo "  新建交换机 $CIDR"
  if [ -n "$DRY_RUN" ]; then
    VSW="vsw-DRYRUN"
  else
    VSW=$(aliyun vpc CreateVSwitch --RegionId "$REGION" --VpcId "$VPCID" \
          --ZoneId "$TARGET_ZONE" --CidrBlock "$CIDR" --VSwitchName "cloud-full-$TARGET_ZONE" \
          | jq_py "print(d['VSwitchId'])")
  fi
  echo "  已建:$VSW"
fi

# ---------------------------------------------------------------- 2. 整机镜像
echo "==> 2/7 用当前实例做整机镜像(含系统盘 + 数据盘)"
# CreateImage --InstanceId 会把实例的**所有**磁盘一起打进镜像(系统盘 +
# 数据盘各自出一个快照)。比"分别快照再分别建盘"少一半步骤,而且新实例
# RunInstances 时数据盘会自动按镜像里的规格建出来。
IMG_NAME="cloud-full-migrate-$(date +%Y%m%d-%H%M%S)"
if [ -n "$DRY_RUN" ]; then
  IMAGE_ID="m-DRYRUN"
  echo "  [dry-run] 会建整机镜像 $IMG_NAME"
else
  IMAGE_ID=$(aliyun ecs CreateImage --RegionId "$REGION" --InstanceId "$INSTANCE_ID" \
    --ImageName "$IMG_NAME" --Description "迁可用区 $SRC_ZONE -> $TARGET_ZONE(scripts/51)" \
    | jq_py "print(d['ImageId'])")
fi
echo "  镜像 $IMAGE_ID($IMG_NAME),等它就绪(240G 的盘通常十几到几十分钟)"

if [ -z "$DRY_RUN" ]; then
  for i in $(seq 1 180); do
    IST=$(aliyun ecs DescribeImages --RegionId "$REGION" --ImageId "$IMAGE_ID" \
      | jq_py "im=d['Images']['Image']; print(im[0]['Status'] if im else 'Missing')")
    PROG=$(aliyun ecs DescribeImages --RegionId "$REGION" --ImageId "$IMAGE_ID" \
      | jq_py "im=d['Images']['Image']; print(im[0].get('Progress','') if im else '')")
    [ "$IST" = "Available" ] && { echo "  镜像就绪"; break; }
    [ "$IST" = "CreateFailed" ] && { echo "!! 镜像创建失败"; exit 1; }
    [ "$i" = 180 ] && { echo "!! 等了 90 分钟镜像还没好(状态 $IST),人工查"; exit 1; }
    [ $((i % 6)) -eq 0 ] && echo "    还在做:$IST $PROG"
    sleep 30
  done
fi

# ---------------------------------------------------------------- 3. 新实例
echo "==> 3/7 在 $TARGET_ZONE 建新实例(竞价,和原来同规格)"
# 竞价策略、带宽、安全组、密钥对都沿用原来的。**不加 --SpotPriceLimit** ——
# 原实例用的是 SpotAsPriceGo(随市场价),上限 0 表示不设上限,加了反而会
# 在价格上涨时被释放。
if [ -n "$DRY_RUN" ]; then
  NEW_ID="i-DRYRUN"
  echo "  [dry-run] 会建实例:$ITYPE / 竞价 / $VSW / 安全组 $SGID / 密钥 $KEYPAIR / 带宽 ${BW}M"
else
  NEW_ID=$(aliyun ecs RunInstances --RegionId "$REGION" --ZoneId "$TARGET_ZONE" \
    --ImageId "$IMAGE_ID" --InstanceType "$ITYPE" --SecurityGroupId "$SGID" \
    --VSwitchId "$VSW" --KeyPairName "$KEYPAIR" \
    --InstanceName "${INAME}-${TARGET_ZONE##*-}" \
    --InstanceChargeType PostPaid --SpotStrategy SpotAsPriceGo \
    --InternetMaxBandwidthOut "$BW" --InternetChargeType PayByTraffic \
    --Amount 1 \
    | jq_py "print(d['InstanceIdSets']['InstanceIdSet'][0])")
fi
echo "  新实例:$NEW_ID"

# ---------------------------------------------------------------- 4. 等起来
echo "==> 4/7 等新实例 Running 并拿到公网 IP"
NEW_IP=""; NEW_PRIVATE=""
if [ -z "$DRY_RUN" ]; then
  for i in $(seq 1 60); do
    NI=$(aliyun ecs DescribeInstances --InstanceIds "[\"$NEW_ID\"]" --RegionId "$REGION")
    ST=$(echo "$NI" | jq_py "print(d['Instances']['Instance'][0]['Status'])")
    if [ "$ST" = "Running" ]; then
      NEW_IP=$(echo "$NI" | jq_py "ip=d['Instances']['Instance'][0].get('PublicIpAddress',{}).get('IpAddress') or ['']; print(ip[0])")
      NEW_PRIVATE=$(echo "$NI" | jq_py "print(d['Instances']['Instance'][0]['VpcAttributes']['PrivateIpAddress']['IpAddress'][0])")
      [ -n "$NEW_IP" ] && break
    fi
    [ "$i" = 60 ] && { echo "!! 等了 10 分钟还没拿到公网 IP(状态 $ST)"; exit 1; }
    sleep 10
  done
  echo "  Running,公网 $NEW_IP,私网 $NEW_PRIVATE"
fi

# ---------------------------------------------------------------- 5. 改配置
echo "==> 5/7 更新仓库里跟着私网 IP 走的配置"
# **这一步是这个脚本存在的一半意义。** JupyterHub 的 singleuser NetworkPolicy
# 里有一条 egress 指向"节点私网IP:6443"(k3s 上 kubernetes.default.svc 会
# DNAT 成这个地址),换实例之后不改这里,notebook 里 submit_job() 会超时,
# 而人会去查 RBAC —— 2026-09-01 之前这个 IP 是硬编码的,旁边只有一句
# "以后换实例记得改"的注释。
if [ -n "$NEW_PRIVATE" ] && [ -z "$DRY_RUN" ]; then
  python3 - "$NEW_PRIVATE" <<'PY'
import pathlib, re, sys
ip = sys.argv[1]
p = pathlib.Path("environments/cloud-full/config.yaml")
s = p.read_text(encoding="utf-8")
s2 = re.sub(r"^node_private_ip: .*$", f"node_private_ip: {ip}", s, flags=re.M)
assert s2 != s, "没找到 node_private_ip 这一行"
p.write_text(s2, encoding="utf-8")
print(f"  environments/cloud-full/config.yaml: node_private_ip -> {ip}")
PY
  python3 scripts/render-environment-config.py cloud-full >/dev/null
  echo "  已重新渲染 apps/definitions/"
fi

# ---------------------------------------------------------------- 6. 更新本地
echo "==> 6/7 本地记录"
echo "  新实例 ID:$NEW_ID"
echo "  **scripts/32-start-cloud-vm.sh 里的实例 ID 要改成它**,否则下次开机"
echo "  开的还是旧那台(而旧那台正是开不了的原因)。"

# ---------------------------------------------------------------- 7. 收尾
echo
echo "==> 7/7 完成。**旧实例和旧磁盘都还在,一个都没删。**"
cat <<EOF

接下来(按顺序):

  1. 提交仓库改动(node_private_ip + 渲染结果 + scripts/32 的实例 ID)
  2. 建 SSH 隧道验证集群:
       ssh -f -N -L 16443:127.0.0.1:6443 -i ~/.ssh/cloud-full-key.pem root@$NEW_IP
       kubectl get nodes -o wide      # InternalIP 应该是 $NEW_PRIVATE
       kubectl get pods -A | grep -v Running
  3. **验证通过之后**再删旧的(这一步不可逆,脚本不做):
       aliyun ecs DeleteInstance --InstanceId $INSTANCE_ID --Force true --RegionId $REGION
       # 磁盘设了「不随实例释放」,要单独删:
       aliyun ecs DeleteDisk --DiskId d-0jl10n6tpnvg6p1pk9dz
       aliyun ecs DeleteDisk --DiskId d-0jlbped4h1959tp2szqu
       # 迁移镜像验证完也可以删(它占快照存储费):
       aliyun ecs DeleteImage --RegionId $REGION --ImageId $IMAGE_ID --Force true

日志:$LOG_FILE
EOF
