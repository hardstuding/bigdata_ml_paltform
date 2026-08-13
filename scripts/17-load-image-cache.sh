#!/usr/bin/env bash
# 把 image-cache/ 里导出的镜像备份重新灌回这台机器,让 Kubernetes 真的用得上,
# 不用每次都重新连国外源拉。
#
# 背景(2026-08-13 实测确认):这台机器上 k3s 是通过 cri-dockerd 这个 CRI 垫片
# 接的 dockerd(`cat /etc/rancher/k3s/... 里 crictl 的 runtime-endpoint 指向
# /run/k3s/cri-dockerd/cri-dockerd.sock`),也就是说 kubelet 实际读的镜像仓库
# 就是这台机器的 docker 本地存储,和 `docker images` 是同一份——不是分开的两套
# (一开始怀疑过是分开的,实测证伪:`docker load` 灌进去的镜像,kubelet 立刻
# 就能直接用,不需要额外导入到别的 containerd namespace)。
#
# 真正的问题是:image-cache/ 里的 .tar.gz 从来没人写脚本把它们载回来过
# (export-image-cache.sh 只导出,没有对应的导入脚本),这些备份就一直放在磁盘
# 上没起作用。今天验证 OpenMetadata 时实测过一次对比:同一个镜像,`docker pull`
# 网络拉取一次到一半还没完(5 分钟以上,手动掐掉了),`docker load` 从本地这份
# 缓存灌回去只要 5.7 秒——这就是这个脚本要解决的事。
#
# 用法:
#   ./scripts/17-load-image-cache.sh [缓存目录,默认 image-cache/]
#
# 幂等:某个镜像本地已经有(docker images 能查到)就跳过,不重复加载。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/load-image-cache.log"
CACHE_DIR="${1:-image-cache}"
MANIFEST="${CACHE_DIR}/manifest.txt"

if [ ! -f "$MANIFEST" ]; then
  echo "找不到 ${MANIFEST},先跑 scripts/export-image-cache.sh 生成缓存" >&2
  exit 1
fi

echo "=== load-image-cache $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

LOADED=0
SKIPPED=0
MISSING=0

# manifest.txt 每行: "<镜像名> <tar.gz 文件名>",第一行是注释(# 开头)跳过。
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
    echo "!! 缓存文件不存在,跳过: ${fpath}" | tee -a "$LOG_FILE"
    MISSING=$((MISSING + 1))
    continue
  fi

  echo "加载: ${fpath} -> ${img}"
  if gunzip -c "$fpath" | docker load >> "$LOG_FILE" 2>&1; then
    LOADED=$((LOADED + 1))
  else
    echo "!! 加载失败: ${img}" | tee -a "$LOG_FILE"
  fi
done < "$MANIFEST"

echo
echo "完成:新加载 ${LOADED} 个,本来就有跳过 ${SKIPPED} 个,缓存文件缺失 ${MISSING} 个"
echo "日志: ${LOG_FILE}"
