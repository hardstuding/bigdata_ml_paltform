#!/usr/bin/env bash
# 把一个镜像从本机送到云主机——**不需要本机 docker/colima 在跑**。
#
# 用法:
#   CLOUD_VM_IP=1.2.3.4 CLOUD_VM_KEY=~/.ssh/xxx.pem \
#     ./scripts/38-ship-image-to-cloud.sh <目标镜像引用> [下载源镜像引用]
#
#   # 例:云主机拉不到 Docker Hub,从 daocloud 下、落地成官方名字
#   ./scripts/38-ship-image-to-cloud.sh \
#       docker.getcollate.io/openmetadata/server:2.0.0 \
#       docker.m.daocloud.io/openmetadata/server:2.0.0
#
# **为什么又多一条镜像搬运路径**(已经有 scripts/22 + export-image-cache*):
# 2026-08-26 升级 OpenMetadata 时撞到一个新情况——云主机**同时**失去了到
# Docker Hub 的所有通路:直连超时、daocloud 卡在 blob 不动、另外试的 5 个
# 镜像站(1ms/xuanyuan/dockerpull/1panel/rat.dev)全部超时。而本机到
# Docker Hub 和 daocloud 都是通的。
#
# 现有的两条路都不好用:
#   - `export-image-cache-amd64.sh` 是**全量**导出(按 list-project-images
#     的 70+ 个镜像),为了两个镜像跑全量不合理;
#   - 它和 `docker save` 都要求**本机 docker 守护进程在跑**,而本机 colima
#     经常是停的(停着省内存),为搬一个镜像专门起一个 6G 的虚拟机也不合理。
#
# 这个脚本用 `crane`(go-containerregistry 的 CLI,单个二进制,
# `brew install crane`):**直接和 registry 说话,不需要任何守护进程**,
# 产出的 tar 可以直接 `docker load`。
#
# 长期解法仍然是给 k3s 配 registry mirror(见 docs/BACKLOG.md),那条做完
# 之后这个脚本和 scripts/22 都可以退休。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/ship-image-to-cloud.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

TARGET_REF="${1:?第一个参数:镜像在集群里被引用的名字(比如 docker.getcollate.io/openmetadata/server:2.0.0)}"
SOURCE_REF="${2:-$TARGET_REF}"
: "${CLOUD_VM_IP:?必须设置 CLOUD_VM_IP}"
: "${CLOUD_VM_KEY:?必须设置 CLOUD_VM_KEY(私钥路径)}"
CLOUD_VM_USER="${CLOUD_VM_USER:-root}"
PLATFORM="${PLATFORM:-linux/amd64}"

command -v crane >/dev/null || { log "本机没有 crane,先 brew install crane。"; exit 1; }

SAFE="$(echo "$TARGET_REF" | tr '/:@' '___')"
TAR="/tmp/${SAFE}.tar"

if [ -s "$TAR" ]; then
  log "本地已有 $TAR($(du -h "$TAR" | cut -f1)),跳过下载"
else
  log "1/3 从 ${SOURCE_REF} 下载(${PLATFORM},不经过 docker 守护进程)"
  crane pull --platform "$PLATFORM" "$SOURCE_REF" "$TAR" 2>&1 | tee -a "$LOG_FILE"
fi
log "    大小 $(du -h "$TAR" | cut -f1)"

log "2/3 rsync 到云主机(断点续传,断了重跑这个脚本即可)"
rsync -a --partial --info=progress2 -e "ssh -i ${CLOUD_VM_KEY} -o StrictHostKeyChecking=accept-new" \
  "$TAR" "${CLOUD_VM_USER}@${CLOUD_VM_IP}:/data/${SAFE}.tar" 2>&1 | tail -2 | tee -a "$LOG_FILE"

log "3/3 在云主机上 docker load + 打成目标名字"
ssh -i "$CLOUD_VM_KEY" -o StrictHostKeyChecking=accept-new "${CLOUD_VM_USER}@${CLOUD_VM_IP}" \
  "set -e
   LOADED=\$(docker load -i /data/${SAFE}.tar | tail -1 | sed 's/^Loaded image: //; s/^Loaded image ID: //')
   echo \"load 出来的是: \$LOADED\"
   # crane 产出的 tar 里带的是**源镜像**的名字,要改成集群里引用的那个。
   # 用 tag 而不是重新 pull:内容一样,只是换个引用名。
   docker tag \"\$LOADED\" '${TARGET_REF}' 2>/dev/null || docker tag \"\$LOADED\" '${TARGET_REF}'
   docker image inspect '${TARGET_REF}' --format '就位: {{.Id}} {{.Size}} bytes'
   rm -f /data/${SAFE}.tar" 2>&1 | tee -a "$LOG_FILE"

log "完成。${TARGET_REF} 已经在云主机上,kubelet 用 IfNotPresent 就能直接用。"
