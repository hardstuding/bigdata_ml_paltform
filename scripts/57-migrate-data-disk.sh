#!/usr/bin/env bash
# 把云主机的数据盘换成更便宜的一块 —— 建新盘、拷数据、切挂载,旧盘留着不动。
#
# **为什么要换而不是"改类型"。** 当前数据盘是 ESSD AutoPL(`cloud_auto`),
# 200G 按 0.42 元/小时算,约 302 元/月。阿里云的 `ModifyDiskSpec` **不支持
# 把 AutoPL 转成别的类型**(实测三种目标全被拒:ESSD Entry 和高效云盘报
# `InstanceTypeUnsupported`——这代规格只认 ESSD 系列;普通 ESSD 报
# `DiskCategory not valid`)。所以只剩"新建 + 迁移"这一条路。
#
# 换成 150G 的 ESSD PL0:0.1575 元/小时,约 113 元/月,**每月省约 189 元**。
# 150 这个数是按实测用量定的(86G 已用,其中绝大部分是在用的容器镜像,
# 清不掉多少),留 60G 出头的余量。
#
# **旧盘不自动删。** 脚本跑完只打印删除命令,让人自己确认过新盘工作正常
# 之后再删 —— 删盘不可逆,而"看起来正常"和"真的正常"之间隔着一次重启。
#
# **可以中断重跑。** 每一步都先看目标状态再决定做不做:盘建过就复用、
# 格式化过就不再格式化、rsync 本身是增量的。中途断了直接重跑这个脚本,
# 已经拷过的不会重来。要跳过建盘直接用某块盘:NEW_DISK_ID=d-xxx 跑。
#
# 用法:
#   ./scripts/57-migrate-data-disk.sh                    # 默认 150G ESSD PL0
#   NEW_DISK_SIZE=200 ./scripts/57-migrate-data-disk.sh  # 换个大小
#   NEW_DISK_ID=d-xxx ./scripts/57-migrate-data-disk.sh  # 复用已经建好的盘
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG="logs/migrate-data-disk-$(date -u +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1
echo "=== 数据盘迁移 $(date -u +%FT%TZ) ==="

_VM_ENV="environments/cloud-full/vm.env"
# shellcheck disable=SC1090
[ -f "$_VM_ENV" ] && . "$_VM_ENV"
INSTANCE_ID="${CLOUD_VM_INSTANCE_ID:?vm.env 里没有 CLOUD_VM_INSTANCE_ID}"
REGION="${CLOUD_VM_REGION:?vm.env 里没有 CLOUD_VM_REGION}"
NEW_DISK_SIZE="${NEW_DISK_SIZE:-150}"
NEW_DISK_NAME="cloud-full-data-essd-pl0"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/cloud-full-key.pem}"

sshvm() { ssh -o ConnectTimeout=20 -o StrictHostKeyChecking=no -i "$SSH_KEY" "root@${VM_IP}" "$@"; }

step() { echo; echo "==> $*"; }

step "确认实例在运行,取公网 IP 和可用区"
INFO=$(aliyun ecs DescribeInstances --RegionId "$REGION" --InstanceIds "[\"$INSTANCE_ID\"]")
STATUS=$(echo "$INFO" | python3 -c "import json,sys;print(json.load(sys.stdin)['Instances']['Instance'][0]['Status'])")
VM_IP=$(echo "$INFO" | python3 -c "import json,sys;print((json.load(sys.stdin)['Instances']['Instance'][0].get('PublicIpAddress',{}).get('IpAddress') or [''])[0])")
ZONE=$(echo "$INFO" | python3 -c "import json,sys;print(json.load(sys.stdin)['Instances']['Instance'][0]['ZoneId'])")
echo "    状态=$STATUS IP=$VM_IP 可用区=$ZONE"
[ "$STATUS" = "Running" ] || { echo "!! 实例不是 Running,先 ./scripts/32-start-cloud-vm.sh"; exit 1; }

step "**先把自动关机看门狗停掉** —— 迁移中途被关机是最糟的中断方式"
sshvm "systemctl stop idle-shutdown-watchdog.timer 2>/dev/null || true; systemctl is-active idle-shutdown-watchdog.timer || true"

step "找当前挂在 /data 上的盘"
OLD_DISK_ID=$(aliyun ecs DescribeDisks --RegionId "$REGION" --InstanceId "$INSTANCE_ID" \
  | python3 -c "
import json,sys
for d in json.load(sys.stdin)['Disks']['Disk']:
    if d['Type'] == 'data':
        print(d['DiskId']); break")
[ -n "$OLD_DISK_ID" ] || { echo "!! 没找到数据盘"; exit 1; }
echo "    旧盘: $OLD_DISK_ID"
USED=$(sshvm "df -BG --output=used /data | tail -1 | tr -dc '0-9'")
echo "    /data 已用 ${USED}G,新盘 ${NEW_DISK_SIZE}G"
[ "$USED" -lt "$((NEW_DISK_SIZE - 10))" ] || { echo "!! 用量离新盘容量太近,加大 NEW_DISK_SIZE"; exit 1; }

step "建新盘(已经建过就复用)"
if [ -z "${NEW_DISK_ID:-}" ]; then
  NEW_DISK_ID=$(aliyun ecs DescribeDisks --RegionId "$REGION" --DiskName "$NEW_DISK_NAME" \
    | python3 -c "
import json,sys
ds=json.load(sys.stdin)['Disks']['Disk']
print(ds[0]['DiskId'] if ds else '')")
fi
if [ -z "$NEW_DISK_ID" ]; then
  NEW_DISK_ID=$(aliyun ecs CreateDisk --RegionId "$REGION" --ZoneId "$ZONE" \
    --DiskCategory cloud_essd --PerformanceLevel PL0 --Size "$NEW_DISK_SIZE" \
    --DiskName "$NEW_DISK_NAME" --Description "cloud-full 数据盘(从 AutoPL 换过来,见 scripts/57)" \
    | python3 -c "import json,sys;print(json.load(sys.stdin)['DiskId'])")
  echo "    新建: $NEW_DISK_ID"
else
  echo "    复用已有的: $NEW_DISK_ID"
fi

step "挂载新盘到实例(已挂就跳过)"
ATTACHED=$(aliyun ecs DescribeDisks --RegionId "$REGION" --DiskIds "[\"$NEW_DISK_ID\"]" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['Disks']['Disk'][0]['Status'])")
if [ "$ATTACHED" != "In_use" ]; then
  aliyun ecs AttachDisk --InstanceId "$INSTANCE_ID" --DiskId "$NEW_DISK_ID" >/dev/null
  for _ in $(seq 1 30); do
    sleep 5
    ATTACHED=$(aliyun ecs DescribeDisks --RegionId "$REGION" --DiskIds "[\"$NEW_DISK_ID\"]" \
      | python3 -c "import json,sys;print(json.load(sys.stdin)['Disks']['Disk'][0]['Status'])")
    [ "$ATTACHED" = "In_use" ] && break
  done
fi
echo "    状态: $ATTACHED"

# 设备名(nvme0n1/nvme1n1)在每次开机后可能对调,**不能写死**。
# 阿里云会把 DiskId 放进 /dev/disk/by-id 的链接名里,按它定位才稳。
NEW_DEV="/dev/disk/by-id/nvme-Alibaba_Cloud_Elastic_Block_Storage_${NEW_DISK_ID#d-}"
sshvm "test -e $NEW_DEV" || { echo "!! 云上显示已挂载,但机器里看不到 $NEW_DEV"; exit 1; }
echo "    设备: $NEW_DEV -> $(sshvm "readlink -f $NEW_DEV")"

step "格式化新盘(已经是 ext4 就跳过)"
FSTYPE=$(sshvm "blkid -o value -s TYPE $NEW_DEV 2>/dev/null || true")
if [ "$FSTYPE" != "ext4" ]; then
  sshvm "mkfs.ext4 -q -L platform-data $NEW_DEV"
  echo "    已格式化"
else
  echo "    已经是 ext4,跳过"
fi
NEW_UUID=$(sshvm "blkid -o value -s UUID $NEW_DEV")
echo "    新盘 UUID: $NEW_UUID"

step "停 k3s 和 docker(拷贝期间必须停,否则拷到的是撕裂的状态)"
sshvm "systemctl stop k3s 2>/dev/null || true; sleep 5; systemctl stop docker docker.socket 2>/dev/null || true; sleep 3; systemctl is-active k3s docker || true"

step "拷数据(rsync,增量,断了重跑不会从头来)"
sshvm "mkdir -p /mnt/newdata && mountpoint -q /mnt/newdata || mount $NEW_DEV /mnt/newdata"
# 在远端后台跑,本地按**产物**判断完成(不是判断进程 —— pgrep -f 会匹配到
# 等待循环自己,这个坑这个仓库栽过 5 次)。
# **退出码写进文件时的转义层数很容易错。** 第一版写的是 `echo \\\$?`,
# 穿过本地 shell → ssh → 远端 sh -c 三层之后,落进文件的是字面量 `$?`
# 而不是数字 —— 于是"rsync 明明拷完了(to-chk=0、两边 du 一样大),脚本却
# 报没有正常结束"。改成把那段脚本写成远端的一个文件再执行,不再让退出码
# 穿过多层引号。
sshvm "rm -f /tmp/rsync-data.done /tmp/rsync-data.log
       cat > /tmp/rsync-data.sh <<'RSH'
#!/bin/sh
rsync -aHAX --numeric-ids --delete --info=progress2 /data/ /mnt/newdata/ > /tmp/rsync-data.log 2>&1
echo \$? > /tmp/rsync-data.done
RSH
       chmod +x /tmp/rsync-data.sh
       nohup /tmp/rsync-data.sh >/dev/null 2>&1 &" || true
echo "    拷贝已在后台开始,等它写出 /tmp/rsync-data.done"
for i in $(seq 1 240); do   # 最多等 2 小时
  sleep 30
  if sshvm "test -f /tmp/rsync-data.done" 2>/dev/null; then break; fi
  [ $((i % 4)) -eq 0 ] && echo "    ... $(sshvm "tail -1 /tmp/rsync-data.log 2>/dev/null | tr -d '\r'" 2>/dev/null | tail -c 80)"
done
RC=$(sshvm "cat /tmp/rsync-data.done 2>/dev/null || echo timeout")
[ "$RC" = "0" ] || { echo "!! rsync 没有正常结束(rc=$RC),/data 原样没动,可以直接重跑这个脚本"; exit 1; }
echo "    拷完了"

step "核对两边的大小"
sshvm "du -sh /data /mnt/newdata 2>/dev/null | sed 's/^/    /'"

step "切换挂载点(fstab 用 UUID,带 nofail —— 万一盘出问题机器还能起来)"
sshvm "umount /mnt/newdata && umount /data
       cp /etc/fstab /etc/fstab.bak-\$(date +%s)
       sed -i '/[[:space:]]\\/data[[:space:]]/d' /etc/fstab
       echo 'UUID=$NEW_UUID /data ext4 defaults,nofail 0 2' >> /etc/fstab
       mount /data
       df -h /data | tail -1"

step "启回 docker 和 k3s"
sshvm "systemctl start docker && sleep 10 && systemctl start k3s && sleep 20; systemctl is-active docker k3s"

step "等集群恢复"
for _ in $(seq 1 40); do
  sleep 15
  READY=$(sshvm "k3s kubectl get nodes --no-headers 2>/dev/null | grep -c ' Ready '" 2>/dev/null || echo 0)
  [ "$READY" = "1" ] && break
done
sshvm "k3s kubectl get nodes 2>/dev/null | sed 's/^/    /'"

step "把看门狗启回来"
sshvm "systemctl start idle-shutdown-watchdog.timer 2>/dev/null || true; systemctl is-active idle-shutdown-watchdog.timer || true"

cat <<EOF

=== 迁移完成 ===
新盘 $NEW_DISK_ID(${NEW_DISK_SIZE}G ESSD PL0)已经挂在 /data 上。
旧盘 $OLD_DISK_ID **还挂在实例上、还在计费**,故意没动它。

确认平台一切正常(建议重启一次实例再看)之后,自己执行这两条删掉它:

  aliyun ecs DetachDisk --InstanceId $INSTANCE_ID --DiskId $OLD_DISK_ID
  aliyun ecs DeleteDisk --DiskId $OLD_DISK_ID

**删盘不可逆。** 在那之前旧盘是完整的回退路径:把 fstab 那行的 UUID 换回
去(备份在 /etc/fstab.bak-*)就回到迁移前的状态。

日志:$LOG
EOF
