#!/usr/bin/env bash
# cloud-full 云主机的基础环境搭建:格式化挂载数据盘、装 Docker、装 k3s。
#
# 这台云主机是裸 Ubuntu VM,不像本机有 colima 帮你把这些都封装好——这个
# 脚本做的事,就是本机 colima 内部隐式帮你做的那些事(起一个能跑 k3s 的
# Linux 环境),搬到裸机上要显式做一遍。
#
# k3s 用 `--docker` 作为容器运行时(cri-dockerd),不是默认的 containerd
# ——和本机 colima+k3s+cri-dockerd 保持同一套(见
# docs/operations/troubleshooting.md/README.md 里"k3s 走 cri-dockerd,和
# docker 是同一份存储"的说明,这样这台机器也能直接用
# scripts/17-load-image-cache.sh 灌镜像,不用改这个脚本的逻辑)。
#
# 故意不给 k3s 的 API server(6443 端口)在安全组里放行公网访问——K8s API
# server 是高价值攻击目标,不该直接暴露公网,哪怕只是 IP 白名单也不够
# 稳妥。管理这台集群走 SSH 隧道(见脚本跑完之后的提示),不是把 6443
# 开给公网。
#
# 用法:
#   CLOUD_VM_IP=<公网IP> CLOUD_VM_KEY=<私钥路径> ./scripts/21-bootstrap-cloud-vm.sh
#
# 幂等:每一步都先检查目标状态是否已经达成,已经做过的会跳过。
set -euo pipefail

: "${CLOUD_VM_IP:?必须设置 CLOUD_VM_IP(云主机公网 IP)}"
: "${CLOUD_VM_KEY:?必须设置 CLOUD_VM_KEY(SSH 私钥路径)}"
CLOUD_VM_USER="${CLOUD_VM_USER:-root}"

cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/bootstrap-cloud-vm.log"

ssh_run() {
  ssh -i "$CLOUD_VM_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "${CLOUD_VM_USER}@${CLOUD_VM_IP}" "$@"
}

echo "=== bootstrap-cloud-vm $(date -u +%FT%TZ) target=${CLOUD_VM_IP} ===" >> "$LOG_FILE"

echo "==> 1. 格式化+挂载数据盘到 /data(整块盘不分区,单一用途数据盘的常见做法)"
ssh_run bash -s <<'REMOTE_SCRIPT' 2>&1 | tee -a "$LOG_FILE"
set -euo pipefail
# 2026-08-15 修复真实 bug(Codex review 指出的):这里的注释曾经写"找
# 容量最大的那块",但代码实际逻辑是"遍历 nvme* 设备,取第一个没有挂载点
# /没有文件系统的",根本没比较过容量——注释和代码不一致,如果一台机器
# 同时有 2 块空盘,会格式化 `lsblk` 恰好列在前面的那一块,不一定是想要
# 的那块。mkfs.ext4 -F 是不可逆操作,这条逻辑不能只靠注释"看起来对"。
#
# 修法:枚举所有候选(没有挂载点、没有文件系统的 nvme 设备),把候选列表
# 连同容量打印出来;只有**恰好一个候选**时才自动继续(这是目前实际遇到
# 的场景,单数据盘云主机);2 个及以上候选时直接拒绝执行,打印列表,要求
# 显式设置 DATA_DISK_DEVICE 环境变量指定具体设备名,不自己猜——这样"两块
# 空盘时自动选错一块"这种事故在设计上就不可能发生,不是靠人记得住去防。
CANDIDATES=""
for dev in $(lsblk -dno NAME | grep '^nvme'); do
  if [ -z "$(lsblk -no MOUNTPOINT "/dev/$dev" | tr -d '\n')" ] && [ -z "$(lsblk -no FSTYPE "/dev/$dev")" ]; then
    SIZE=$(lsblk -dno SIZE "/dev/$dev")
    CANDIDATES="${CANDIDATES}/dev/$dev(${SIZE}) "
  fi
done

DISK="${DATA_DISK_DEVICE:-}"
if [ -n "$DISK" ]; then
  echo "使用显式指定的数据盘: $DISK"
elif [ -z "$CANDIDATES" ]; then
  echo "!! 没找到未格式化的候选数据盘,可能已经格式化过了,检查 /data 是否已挂载" >&2
  mountpoint /data || exit 1
elif [ "$(echo "$CANDIDATES" | wc -w)" -eq 1 ]; then
  DISK="$(echo "$CANDIDATES" | sed 's/(.*//')"
  echo "唯一候选,自动选用: $DISK ($CANDIDATES)"
else
  echo "!! 发现多个未格式化的候选盘,不自动选择,避免格式化错盘:" >&2
  echo "   $CANDIDATES" >&2
  echo "   请显式设置 DATA_DISK_DEVICE=/dev/xxx 重新运行这个脚本" >&2
  exit 1
fi

if [ -n "$DISK" ] && [ -z "$(lsblk -no MOUNTPOINT "$DISK" | tr -d '\n')" ] && [ -z "$(lsblk -no FSTYPE "$DISK")" ]; then
  ROOT_DEV="$(findmnt -no SOURCE / | sed 's/p\?[0-9]*$//')"
  if [ "$DISK" = "$ROOT_DEV" ]; then
    echo "!! 拒绝执行:检测到的目标盘 $DISK 和根分区所在设备 $ROOT_DEV 是同一块,格式化会破坏系统盘" >&2
    exit 1
  fi
  echo "即将格式化: $DISK(容量 $(lsblk -dno SIZE "$DISK"),确认无挂载点、无文件系统、不是根盘)"
  mkfs.ext4 -F "$DISK"
  mkdir -p /data
  mount "$DISK" /data
  UUID=$(blkid -s UUID -o value "$DISK")
  if ! grep -q "$UUID" /etc/fstab; then
    echo "UUID=$UUID /data ext4 defaults,nofail 0 2" >> /etc/fstab
  fi
fi
df -h /data
REMOTE_SCRIPT

echo "==> 2. 安装 Docker(官方安装脚本,get.docker.com)"
ssh_run bash -s <<'REMOTE_SCRIPT' 2>&1 | tee -a "$LOG_FILE"
set -euo pipefail
if command -v docker >/dev/null 2>&1; then
  echo "docker 已安装,跳过: $(docker --version)"
else
  curl -fsSL https://get.docker.com | sh
  mkdir -p /data/docker /etc/docker
  cat > /etc/docker/daemon.json <<'EOF'
{"data-root": "/data/docker"}
EOF
  systemctl enable docker
  systemctl restart docker
fi
docker info >/dev/null && echo "docker 正常运行"
REMOTE_SCRIPT

echo "==> 3. 安装 k3s(--docker 运行时,数据目录指到 /data,和本机保持一致)"
ssh_run bash -s <<REMOTE_SCRIPT 2>&1 | tee -a "$LOG_FILE"
set -euo pipefail
if command -v k3s >/dev/null 2>&1; then
  echo "k3s 已安装,跳过: \$(k3s --version | head -1)"
else
  mkdir -p /data/k3s
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--docker --data-dir /data/k3s --write-kubeconfig-mode 644 --tls-san ${CLOUD_VM_IP}" sh -
fi
sleep 8
k3s kubectl get nodes
REMOTE_SCRIPT

echo
echo "完成。管理这台集群,建议走 SSH 隧道(不把 6443 暴露公网):"
echo "  ssh -f -N -L 16443:127.0.0.1:6443 -i ${CLOUD_VM_KEY} ${CLOUD_VM_USER}@${CLOUD_VM_IP}"
echo "  然后本机 kubeconfig 的 server 地址指向 https://127.0.0.1:16443"
echo "日志见 ${LOG_FILE}"
