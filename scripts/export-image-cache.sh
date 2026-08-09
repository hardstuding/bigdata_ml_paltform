#!/usr/bin/env bash
# 把这台 Mac(colima)本地已经拉过的、这个项目用得到的镜像,导出成一份本地
# 备份(tar.gz + 清单),给"公司内网出不去国外"这个约束用的——以后可以把这份
# 备份搬到能连公司内网的机器上,`docker load` 回来再 push 到公司内部的镜像仓库
# (比如 Harbor,见 docs/architecture.md 里 Phase 4 的规划),不用每个组件重新
# 连国外源拉一遍。
#
# 镜像清单来源两部分取并集:
#   1. scripts/list-project-images.py --include-pending 静态扫描 ArgoCD
#      Application 配置算出来的"理论上需要哪些镜像"
#   2. 这台机器 `docker images` 里已经缓存的、且不在已知无关名单里的镜像
#      (兜底:有些镜像是运行时才注入的,比如 Kafka broker 的具体镜像由
#      Strimzi operator 根据 Kafka CR 的 version 字段动态决定,静态扫描
#      Application YAML 扫不出来,但只要真的跑起来过、镜像就已经缓存在本地了)
#
# 用法:
#   ./scripts/export-image-cache.sh [输出目录,默认 image-cache/]
#
# 幂等:某个镜像已经导出过(tar.gz 已存在)会跳过,不会重新导出。想强制刷新
# 某一个,先手动删掉对应的 .tar.gz 再重新跑。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/export-image-cache.log"
OUT_DIR="${1:-image-cache}"
mkdir -p "$OUT_DIR"

echo "=== export-image-cache $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

# macOS 自带的 /bin/bash 是 3.2(license 原因,苹果不带 GPLv3 的新版本),
# 没有 mapfile/readarray(bash 4+ 才有),这里全用 while read 循环 + 临时文件,
# 不用数组批量赋值,保证在这台 Mac 上不装 Homebrew bash 也能跑。
KNOWN_UNRELATED="eipwork/etcd-host eipwork/kuboard"
IMAGE_LIST_FILE="$(mktemp)"
trap 'rm -f "$IMAGE_LIST_FILE"' EXIT

echo "==> 静态扫描 Application 配置(含 pending-definitions)"
python3 scripts/list-project-images.py --include-pending 2>>"$LOG_FILE" >> "$IMAGE_LIST_FILE"

echo "==> 加上本地 docker 缓存里已有的镜像"
docker images --format '{{.Repository}}:{{.Tag}}' | grep -v '<none>' >> "$IMAGE_LIST_FILE"

sort -u "$IMAGE_LIST_FILE" -o "$IMAGE_LIST_FILE"
for unrelated in $KNOWN_UNRELATED; do
  grep -v "^${unrelated}" "$IMAGE_LIST_FILE" > "${IMAGE_LIST_FILE}.tmp" && mv "${IMAGE_LIST_FILE}.tmp" "$IMAGE_LIST_FILE"
done

echo "共 $(wc -l < "$IMAGE_LIST_FILE" | tr -d ' ') 个镜像,开始导出到 ${OUT_DIR}/"
MANIFEST="${OUT_DIR}/manifest.txt"
echo "# $(date -u +%FT%TZ) 导出清单" > "$MANIFEST"

while IFS= read -r img; do
  [ -z "$img" ] && continue
  # 镜像名转文件名:斜杠、冒号、@ 都换成 _,避免嵌套目录/非法字符
  fname="$(echo "$img" | tr '/:@' '___').tar.gz"
  fpath="${OUT_DIR}/${fname}"

  if [ -f "$fpath" ]; then
    echo "已存在,跳过: ${img}"
    echo "${img} ${fname}" >> "$MANIFEST"
    continue
  fi

  if ! docker image inspect "$img" >/dev/null 2>&1; then
    echo "本地没有,先拉取: ${img}"
    if ! docker pull "$img" >> "$LOG_FILE" 2>&1; then
      echo "  !! 拉取失败(可能是这次网络到不了),跳过: ${img}" | tee -a "$LOG_FILE"
      continue
    fi
  fi

  echo "导出: ${img} -> ${fpath}"
  docker save "$img" | gzip > "$fpath"
  echo "${img} ${fname}" >> "$MANIFEST"
done < "$IMAGE_LIST_FILE"

echo
echo "完成。导出目录: ${OUT_DIR}/(已加进 .gitignore,不会被提交到 git——"
echo "镜像是二进制大文件,不适合放 git 历史)"
echo "总大小:"
du -sh "$OUT_DIR"
echo
echo "以后要在别的机器上用:"
echo "  gunzip -c <文件>.tar.gz | docker load"
echo "  docker tag <原名> <公司内部仓库地址>/<原名>"
echo "  docker push <公司内部仓库地址>/<原名>"
