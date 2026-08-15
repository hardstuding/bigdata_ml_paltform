#!/usr/bin/env bash
# 把本机打包好的镜像缓存(默认 image-cache-amd64/,给 x86_64 云主机/生产用;
# 也可以传 image-cache/ 传 arm64 版本)传到远程主机,再在远程主机上
# `docker load` 灌进去——和 scripts/17-load-image-cache.sh 的逻辑等价,
# 但那个脚本假设在同一台机器上跑(本机 colima 场景),这个脚本是给
# "本机准备缓存、云主机/远程主机实际使用"这个场景用的。
#
# 传输用 rsync(不是 scp)——这批文件几十 GB,rsync 支持断点续传,网络
# 抖动/连接中断之后重跑一次会跳过已经传完的部分,不用整个重传。
#
# 用法:
#   CLOUD_VM_IP=<公网IP> CLOUD_VM_KEY=<私钥路径> ./scripts/22-load-image-cache-remote.sh [本地缓存目录,默认 image-cache-amd64]
#
# 幂等:远程主机上已经有的镜像(docker image inspect 能查到)会跳过,不重复加载。
set -euo pipefail

: "${CLOUD_VM_IP:?必须设置 CLOUD_VM_IP(远程主机公网 IP)}"
: "${CLOUD_VM_KEY:?必须设置 CLOUD_VM_KEY(SSH 私钥路径)}"
CLOUD_VM_USER="${CLOUD_VM_USER:-root}"
LOCAL_CACHE_DIR="${1:-image-cache-amd64}"
REMOTE_CACHE_DIR="${REMOTE_CACHE_DIR:-/data/image-cache}"

cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/load-image-cache-remote.log"

if [ ! -f "${LOCAL_CACHE_DIR}/manifest.txt" ]; then
  echo "找不到 ${LOCAL_CACHE_DIR}/manifest.txt,先跑对应的 export-image-cache*.sh 生成缓存" >&2
  exit 1
fi

echo "=== load-image-cache-remote $(date -u +%FT%TZ) target=${CLOUD_VM_IP} src=${LOCAL_CACHE_DIR} ===" >> "$LOG_FILE"

echo "==> 1. rsync 传输 ${LOCAL_CACHE_DIR}/ -> ${CLOUD_VM_USER}@${CLOUD_VM_IP}:${REMOTE_CACHE_DIR}/"
ssh -i "$CLOUD_VM_KEY" -o StrictHostKeyChecking=accept-new "${CLOUD_VM_USER}@${CLOUD_VM_IP}" "mkdir -p ${REMOTE_CACHE_DIR}"
rsync -avz --progress -e "ssh -i ${CLOUD_VM_KEY} -o StrictHostKeyChecking=accept-new" \
  "${LOCAL_CACHE_DIR}/" "${CLOUD_VM_USER}@${CLOUD_VM_IP}:${REMOTE_CACHE_DIR}/" 2>&1 | tee -a "$LOG_FILE"

echo "==> 2. 远程主机上 docker load(和 scripts/17-load-image-cache.sh 同一套逻辑)"
ssh -i "$CLOUD_VM_KEY" -o StrictHostKeyChecking=accept-new "${CLOUD_VM_USER}@${CLOUD_VM_IP}" bash -s "$REMOTE_CACHE_DIR" <<'REMOTE_SCRIPT' 2>&1 | tee -a "$LOG_FILE"
set -euo pipefail
CACHE_DIR="$1"
MANIFEST="${CACHE_DIR}/manifest.txt"
LOADED=0
SKIPPED=0
MISSING=0
while IFS= read -r line; do
  case "$line" in
    \#*|"") continue ;;
  esac
  img="${line% *}"
  fname="${line##* }"
  fpath="${CACHE_DIR}/${fname}"

  if docker image inspect "$img" >/dev/null 2>&1; then
    echo "已存在,跳过: ${img}"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi
  if [ ! -f "$fpath" ]; then
    echo "!! 缓存文件不存在,跳过: ${fpath}" >&2
    MISSING=$((MISSING + 1))
    continue
  fi
  echo "加载: ${fpath} -> ${img}"
  if gunzip -c "$fpath" | docker load; then
    LOADED=$((LOADED + 1))
  else
    echo "!! 加载失败: ${img}" >&2
  fi
done < "$MANIFEST"
echo
echo "完成:新加载 ${LOADED} 个,本来就有跳过 ${SKIPPED} 个,缓存文件缺失 ${MISSING} 个"
REMOTE_SCRIPT

echo
echo "完成。日志: ${LOG_FILE}"
