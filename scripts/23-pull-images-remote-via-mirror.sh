#!/usr/bin/env bash
# 云主机(x86_64,在阿里云网络里)直接通过国内镜像源拉取镜像,不用先在本机
# (Mac,arm64)导出成 tar.gz 再 rsync 传过去——2026-08-15 真实发现:这台云
# 主机能连 docker.m.daocloud.io / quay.m.daocloud.io / k8s.m.daocloud.io /
# ghcr.m.daocloud.io 这几个国内镜像加速站,分别对应代理 docker.io / quay.io /
# registry.k8s.io / ghcr.io 这四个上游注册中心。实测三个例子(postgres:16.15、
# cert-manager-controller:v1.21.1、kube-webhook-certgen:v1.6.9)镜像站拉到的
# digest 和官方源完全一致,不是内容被篡改的野镜像。
#
# 这条路径能覆盖这个项目 69 个镜像里的 66 个(docker.io 35 + quay.io 22 +
# registry.k8s.io 5 + ghcr.io 4),原来从 Mac 上传的路径(scripts/22-load-
# image-cache-remote.sh)只有本地这台 Mac 的上行带宽(实测约 5MB/s,不是
# VPN 或者 rsync 参数的问题,是真实链路上限),镜像源这条路径在阿里云内网
# 到国内加速站,快得多。
#
# 覆盖不到的(nvcr.io、ecr-public.aws.com、local/ 本地构建镜像)继续走
# scripts/22-load-image-cache-remote.sh 那条老路径,不是这个脚本的责任。
#
# 用法:
#   CLOUD_VM_IP=<公网IP> CLOUD_VM_KEY=<私钥路径> ./scripts/23-pull-images-remote-via-mirror.sh
#
# 幂等:远程主机上已经有这个 tag 的镜像会跳过重拉(用 docker image inspect 判断)。
set -euo pipefail

: "${CLOUD_VM_IP:?必须设置 CLOUD_VM_IP(云主机公网 IP)}"
: "${CLOUD_VM_KEY:?必须设置 CLOUD_VM_KEY(SSH 私钥路径)}"
CLOUD_VM_USER="${CLOUD_VM_USER:-root}"

cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/pull-images-remote-via-mirror.log"

echo "==> 生成需要的镜像清单(含 pending-definitions)"
python3 scripts/list-project-images.py --include-pending 2>/dev/null | sort -u > /tmp/mirror-pull-images.txt

echo "==> 上传清单到云主机(小文件,不占带宽)"
scp -i "$CLOUD_VM_KEY" -o StrictHostKeyChecking=accept-new /tmp/mirror-pull-images.txt "${CLOUD_VM_USER}@${CLOUD_VM_IP}:/tmp/mirror-pull-images.txt"

echo "==> 在云主机上按镜像源批量拉、验证、打回原名"
ssh -i "$CLOUD_VM_KEY" -o StrictHostKeyChecking=accept-new "${CLOUD_VM_USER}@${CLOUD_VM_IP}" bash -s <<'REMOTE_SCRIPT' 2>&1 | tee -a "$LOG_FILE"
set -uo pipefail
OK=0
SKIPPED_NO_MIRROR=0
FAILED=0
N=0
TOTAL=$(wc -l < /tmp/mirror-pull-images.txt | tr -d ' ')

while IFS= read -r img; do
  [ -z "$img" ] && continue
  N=$((N + 1))

  case "$img" in
    quay.io/*)
      mirror="quay.m.daocloud.io/${img#quay.io/}"
      ;;
    registry.k8s.io/*)
      mirror="k8s.m.daocloud.io/${img#registry.k8s.io/}"
      ;;
    ghcr.io/*)
      mirror="ghcr.m.daocloud.io/${img#ghcr.io/}"
      ;;
    docker.getcollate.io/*)
      # 2026-08-19 真实发现:docker.getcollate.io 只是 scarf.sh 包装的下载
      # 统计域名,realm challenge 里的 WWW-Authenticate 指向
      # auth.docker.io/registry.docker.io——本质就是 docker.io 上的
      # openmetadata/server 这个仓库,不是独立注册中心。直接从这台机器连
      # docker.getcollate.io 本身可达(TCP/TLS 握手正常),但它转发到的
      # registry-1.docker.io 在这片网络里会超时,和裸 docker.io 镜像
      # 一样卡死,只是报错信息里域名不一样容易被误判成"没有覆盖"。验证
      # 过 daocloud 镜像站拉到的 digest 和直连 registry-1.docker.io 拿到
      # 的官方 manifest digest 完全一致(sha256:997d666b...),不是假设。
      mirror="docker.m.daocloud.io/${img#docker.getcollate.io/}"
      ;;
    nvcr.io/*|ecr-public.aws.com/*|local/*)
      echo "[$N/$TOTAL] 这个镜像源镜像站不覆盖,跳过(走 scripts/22 老路径): ${img}"
      SKIPPED_NO_MIRROR=$((SKIPPED_NO_MIRROR + 1))
      continue
      ;;
    *)
      # docker.io,可能没有 library/ 前缀(官方镜像,比如 postgres:16.15)
      # 也可能有 org 前缀(比如 kserve/sklearnserver:...)
      repo="${img%%:*}"
      repo="${repo%%@*}"
      if [[ "$repo" != */* ]]; then
        mirror="docker.m.daocloud.io/library/${img}"
      else
        mirror="docker.m.daocloud.io/${img}"
      fi
      ;;
  esac

  if docker image inspect "$img" >/dev/null 2>&1; then
    echo "[$N/$TOTAL] 本地已有,跳过: ${img}"
    OK=$((OK + 1))
    continue
  fi

  echo "[$N/$TOTAL] 拉取(经镜像站): ${img} <- ${mirror}"
  if ! docker pull "$mirror" >/dev/null 2>&1; then
    echo "  !! 镜像站拉取失败,跳过: ${img}"
    FAILED=$((FAILED + 1))
    continue
  fi
  docker tag "$mirror" "$img"
  docker rmi "$mirror" >/dev/null 2>&1 || true
  echo "  已拉取并打回原名: ${img}"
  OK=$((OK + 1))
done < /tmp/mirror-pull-images.txt

echo
echo "完成: 成功/已有 ${OK},镜像站不覆盖(需要走老路径) ${SKIPPED_NO_MIRROR},失败 ${FAILED}(共 ${TOTAL})"
REMOTE_SCRIPT

echo
echo "日志: ${LOG_FILE}"
