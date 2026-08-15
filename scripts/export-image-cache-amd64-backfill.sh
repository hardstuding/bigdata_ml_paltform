#!/usr/bin/env bash
# 补拉那些被 export-image-cache-amd64.sh 主流程跳过的镜像(2026-08-15
# 真实发现:这台机器本地已经缓存过的 tag,`docker pull --platform
# linux/amd64` 经常不会真的切换架构——不分注册中心,quay.io/
# registry.k8s.io/docker.io 都撞到过,根因判断是"本地已有这个 tag 的
# 缓存"这一类,不是某个具体镜像仓库的问题)。
#
# 做法:显式解析 amd64 平台对应的具体 digest(`docker manifest
# inspect`),按 digest 拉(和原 tag 完全独立的引用,不受本地缓存的 tag
# 状态影响),验证架构,临时打 tag 导出,导出完立刻把 tag 恢复到导出前
# 的状态——这条路径已经用真实案例(quay.io/oauth2-proxy/oauth2-proxy)
# 验证过,导出的内容是完整的、架构正确的。
#
# 用法:
#   ./scripts/export-image-cache-amd64-backfill.sh <镜像清单文件,每行一个镜像引用> [输出目录,默认 image-cache-amd64]
set -uo pipefail

IMAGE_LIST="${1:?用法: $0 <镜像清单文件> [输出目录]}"
OUT_DIR="${2:-image-cache-amd64}"
mkdir -p "$OUT_DIR" logs
LOG_FILE="logs/export-image-cache-amd64-backfill.log"
MANIFEST="${OUT_DIR}/manifest.txt"

echo "=== export-image-cache-amd64-backfill $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

TOTAL=$(wc -l < "$IMAGE_LIST" | tr -d ' ')
N=0
OK=0
FAIL=0
while IFS= read -r img; do
  [ -z "$img" ] && continue
  N=$((N + 1))
  fname="$(echo "$img" | tr '/:@' '___').tar.gz"
  fpath="${OUT_DIR}/${fname}"

  if [ -f "$fpath" ]; then
    echo "[$N/$TOTAL] 已存在,跳过: ${img}"
    grep -qF "$img " "$MANIFEST" 2>/dev/null || echo "${img} ${fname}" >> "$MANIFEST"
    OK=$((OK+1))
    continue
  fi

  echo "[$N/$TOTAL] 解析 amd64 digest: ${img}"
  REPO="${img%%@*}"; REPO="${REPO%%:*}"
  DIGEST=$(docker manifest inspect "$img" 2>>"$LOG_FILE" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
# 有的引用本身已经是具体 manifest(没有 manifests 列表),这种直接读顶层 architecture
if 'manifests' in d:
    for m in d['manifests']:
        p = m.get('platform', {})
        if p.get('architecture') == 'amd64' and p.get('os') == 'linux':
            print(m['digest']); break
elif d.get('architecture') == 'amd64':
    pass  # 顶层就是 amd64 具体 manifest,但拿不到它自己的 digest,交给调用方按原引用处理
" 2>>"$LOG_FILE")

  if [ -z "$DIGEST" ]; then
    echo "  !! 解析不出 amd64 digest,跳过: ${img}" | tee -a "$LOG_FILE"
    FAIL=$((FAIL+1))
    continue
  fi

  DIGEST_REF="${REPO}@${DIGEST}"
  if ! docker pull "$DIGEST_REF" >> "$LOG_FILE" 2>&1; then
    echo "  !! 按 digest 拉取失败,跳过: ${img}" | tee -a "$LOG_FILE"
    FAIL=$((FAIL+1))
    continue
  fi

  ARCH="$(docker image inspect "$DIGEST_REF" --format '{{.Architecture}}' 2>/dev/null || true)"
  if [ "$ARCH" != "amd64" ]; then
    echo "  !! 按 digest 拉到的内容架构是 '${ARCH}',不是 amd64,跳过: ${img}" | tee -a "$LOG_FILE"
    docker rmi "$DIGEST_REF" >> "$LOG_FILE" 2>&1 || true
    FAIL=$((FAIL+1))
    continue
  fi

  PREV_ID="$(docker image inspect "$img" --format '{{.Id}}' 2>/dev/null || true)"
  docker tag "$DIGEST_REF" "$img"

  echo "  导出: ${img} -> ${fpath}"
  if docker save --platform linux/amd64 "$img" 2>>"$LOG_FILE" | gzip > "$fpath"; then
    echo "${img} ${fname}" >> "$MANIFEST"
    OK=$((OK+1))
  else
    echo "  !! docker save 失败: ${img}" | tee -a "$LOG_FILE"
    rm -f "$fpath"
    FAIL=$((FAIL+1))
  fi

  if [ -n "$PREV_ID" ]; then
    docker tag "$PREV_ID" "$img" >> "$LOG_FILE" 2>&1 || true
  else
    docker rmi "$img" >> "$LOG_FILE" 2>&1 || true
  fi
  docker rmi "$DIGEST_REF" >> "$LOG_FILE" 2>&1 || true
done < "$IMAGE_LIST"

echo
echo "完成: 成功/已存在 ${OK},失败 ${FAIL}(共 ${TOTAL})"
echo "日志: ${LOG_FILE}"
