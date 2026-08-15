#!/usr/bin/env bash
# 导出这个项目用到的镜像的 amd64(x86_64)版本——2026-08-15 确认生产环境是
# x86_64,cloud-full 云上验证环境也按 x86_64 配,和 scripts/export-image-
# cache.sh(这台 Mac 本机 arm64 原生缓存,给本地 colima 用)分开存放,不要
# 搞混、也不要互相覆盖。
#
# 关键风险(这是这个脚本存在的核心原因,不是可以跳过的细节):这台机器
# 本身是 Apple Silicon(arm64)。`docker pull --platform linux/amd64
# <repo>:<tag>` 拉下来的镜像,本地会先用**同一个 <repo>:<tag> 这个引用**
# 落地——这台机器上正在跑的 k3s 集群和 docker 是同一份本地存储(见
# scripts/17-load-image-cache.sh 的说明),如果这个 tag 短暂指向了 amd64
# 版本,期间刚好有 pod 需要重新拉/引用这个镜像,会直接失败(exec format
# error 或者更糟),打断这台机器本地正在跑的服务。
#
# 应对:`docker save` 支持 `--platform` 显式指定要导出哪个架构(这台机器
# docker 29.7.2 实测确认支持),导出完立刻 `docker rmi` 清掉本地这个危险
# 引用,把风险窗口压缩到"拉取完到 rmi 完成"这一段,不长期停留。
#
# 中间踩过一个坑,记录一下:一开始想的是"拉完先转存到一个无关的临时
# tag、马上删原名",实测这个思路本身会坏——`docker tag` + 立刻
# `docker rmi` 原名之后,再对临时 tag 跑 `docker save` 会报
# `unable to create manifests file: NotFound: content digest ... not
# found`,连不加转存直接对原名 `docker save`(不删除)都会报同样的错。
# 根因是这台机器的 docker(containerd 镜像存储后端)在 `--platform` 拉取
# 之后,本地 tag 指向的是一份多架构 manifest 列表,`docker save` 不加
# `--platform` 会试图导出列表里全部平台的内容,但本机只真的下载了 amd64
# 那一份,其它平台内容本地根本不存在,直接报错——不是转存/删除顺序的
# 问题,是 save 命令本身默认行为的问题。加上 `--platform linux/amd64`
# 显式告诉 save 只导出这一个平台就解决了,不需要转存这一步。
#
# 用法:
#   ./scripts/export-image-cache-amd64.sh [输出目录,默认 image-cache-amd64/]
#
# 幂等:某个镜像已经导出过(tar.gz 已存在)会跳过。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/export-image-cache-amd64.log"
OUT_DIR="${1:-image-cache-amd64}"
mkdir -p "$OUT_DIR"

echo "=== export-image-cache-amd64 $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

IMAGE_LIST_FILE="$(mktemp)"
trap 'rm -f "$IMAGE_LIST_FILE"' EXIT

echo "==> 静态扫描 Application 配置(含 pending-definitions)"
python3 scripts/list-project-images.py --include-pending 2>>"$LOG_FILE" >> "$IMAGE_LIST_FILE"

# 故意不像 export-image-cache.sh 那样再加一份"本地 docker 缓存已有的镜像"
# 兜底——本地缓存现在是 arm64 的,混进来会让这个脚本误判"本地已经有 amd64
# 版了"而跳过拉取,实际上是同名不同架构,必须每一个都显式按 amd64 重新拉。

sort -u "$IMAGE_LIST_FILE" -o "$IMAGE_LIST_FILE"

TOTAL=$(wc -l < "$IMAGE_LIST_FILE" | tr -d ' ')
echo "共 ${TOTAL} 个镜像,开始按 amd64 架构导出到 ${OUT_DIR}/"
MANIFEST="${OUT_DIR}/manifest.txt"
echo "# $(date -u +%FT%TZ) 导出清单(amd64/x86_64,给生产/cloud-full 用)" > "$MANIFEST"

N=0
while IFS= read -r img; do
  [ -z "$img" ] && continue
  N=$((N + 1))
  fname="$(echo "$img" | tr '/:@' '___').tar.gz"
  fpath="${OUT_DIR}/${fname}"

  if [ -f "$fpath" ]; then
    echo "[$N/$TOTAL] 已存在,跳过: ${img}"
    echo "${img} ${fname}" >> "$MANIFEST"
    continue
  fi

  # 2026-08-15 真实撞到两层坑,记录下来不要重新踩:
  #
  # 第一层:直接 `docker pull --platform linux/amd64 $img` 按 tag 拉,
  # 如果这台机器本地已经缓存过这个 tag(这台 Mac 本身在跑这个项目的
  # 本地集群,很多组件镜像本来就有 arm64 原生缓存),Docker 有时会认为
  # 这个 tag 已经"满足",不会真的重新拉取换成 amd64,`docker image
  # inspect` 之后还是原来的架构——不一定报错,不能只看 pull 命令有没有
  # 报错就认为拿到了正确架构,必须显式校验 `.Architecture`。
  #
  # 第二层(试过、被否掉的方案,记录原因):一开始想用 `docker manifest
  # inspect` 解析出 amd64 digest、按 digest 拉、临时打 tag 再 save——
  # 实测这条路径在这次真实撞到的案例(quay.io/jetstack/
  # cert-manager-cainjector)上导出的 tar 只有 10KB,`tar -tf` 确认只有
  # manifest 元数据,真正的层内容没有被写进去,是**静默产出损坏文件**,
  # 比直接失败更危险。原因没有深挖清楚(疑似这台机器本地 containerd
  # content store 对这个具体 repo 有某种损坏/混淆的缓存状态),放弃这条
  # 路径,改成更保守的"检测到问题就跳过,不试图强修"。
  echo "[$N/$TOTAL] 拉取(amd64): ${img}"
  PREV_ID="$(docker image inspect "$img" --format '{{.Id}}' 2>/dev/null || true)"
  if ! docker pull --platform linux/amd64 "$img" >> "$LOG_FILE" 2>&1; then
    echo "  !! 拉取失败(可能没有发布 amd64 版本,或者这次网络问题),跳过: ${img}" | tee -a "$LOG_FILE"
    continue
  fi

  ACTUAL_ARCH="$(docker image inspect "$img" --format '{{.Architecture}}' 2>/dev/null || true)"
  if [ "$ACTUAL_ARCH" != "amd64" ]; then
    echo "  !! 拉取后架构是 '${ACTUAL_ARCH}',不是 amd64(本地缓存冲突,见上面注释),跳过并恢复原状态: ${img}" | tee -a "$LOG_FILE"
    if [ -n "$PREV_ID" ]; then
      docker tag "$PREV_ID" "$img" >> "$LOG_FILE" 2>&1 || true
    fi
    continue
  fi

  echo "  导出: ${img} -> ${fpath}"
  if ! docker save --platform linux/amd64 "$img" 2>>"$LOG_FILE" | gzip > "$fpath"; then
    echo "  !! docker save 失败,跳过并清理残留文件: ${img}" | tee -a "$LOG_FILE"
    rm -f "$fpath"
    if [ -n "$PREV_ID" ]; then
      docker tag "$PREV_ID" "$img" >> "$LOG_FILE" 2>&1 || true
    else
      docker rmi "$img" >> "$LOG_FILE" 2>&1 || true
    fi
    continue
  fi
  echo "${img} ${fname}" >> "$MANIFEST"

  # 立刻恢复这个 tag 到导出之前的状态(如果之前有指向别的内容,原样
  # 恢复;之前没有就删掉)——不让这台机器本地正在用这个 tag 的东西
  # (不管是别的架构还是别的版本)被这个导出脚本留下的 amd64 内容影响到。
  if [ -n "$PREV_ID" ]; then
    docker tag "$PREV_ID" "$img" >> "$LOG_FILE" 2>&1 || true
  else
    docker rmi "$img" >> "$LOG_FILE" 2>&1 || true
  fi
done < "$IMAGE_LIST_FILE"

echo
echo "完成。导出目录: ${OUT_DIR}/(已加进 .gitignore,不进 git 历史)"
du -sh "$OUT_DIR" 2>/dev/null || true
echo
echo "==> 收尾保险:确认本机 arm64 镜像缓存没有被这次操作影响"
echo "如果不放心,重新跑一次 ./scripts/17-load-image-cache.sh(幂等,本地已有的会跳过,"
echo "能确认所有这个项目用到的镜像本地都是 arm64 原生版本)。"
echo
echo "以后要在 x86_64 生产/cloud-full 环境用:"
echo "  gunzip -c <文件>.tar.gz | docker load"
echo "  docker tag <原名> <公司内部仓库地址>/<原名>"
echo "  docker push <公司内部仓库地址>/<原名>"
