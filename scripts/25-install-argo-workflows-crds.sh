#!/usr/bin/env bash
# argo-workflows chart(1.0.24)默认靠一个 pre-install/pre-upgrade Job 从
# raw.githubusercontent.com 实时下载 7 个"完整版"CRD 再 apply(chart 自己
# 打包的 CRD 只有体积更小的 minified 版本,完整版因为体积太大特意没打进
# chart 包)。这个 Job 需要访问 raw.githubusercontent.com——local-lite
# 靠 colima 宿主机的本地代理(192.168.5.2:1087)才能连上,而这个代理地址
# 只在这台 Mac 上存在,cloud-full(阿里云 ECS,独立公网出口)根本连不到
# 192.168.5.2,这个 Job 会一直超时卡住(2026-08-16 云端部署时发现的真实
# 故障)。
#
# 和 KServe(ADR-027)/CloudNativePG(scripts/16-install-cloudnative-pg-crds.sh)
# 是同一类问题的另一种表现——那两个是 CRD 体积超过 kubectl 262144 字节的
# last-applied-configuration 注解上限,这个是"下载 CRD 内容"这一步本身
# 依赖了一个只在特定网络里存在的代理,两种问题的共同解法都是"不要在
# GitOps 同步路径里做这种脆弱的动态下载,退回到一次性手动脚本"。
#
# 这次选择把 7 个 CRD 内容直接 vendor 进仓库(apps/argo-workflows-crds/
# manifests/,来自官方 https://github.com/argoproj/argo-helm 对应 tag
# 下的 charts/argo-workflows/files/crds/full/*.yaml),不是像 CNPG 那样
# 在脚本里现下载——这样 local-lite 和 cloud-full 都不再需要任何代理/
# 网络依赖,离线也能跑,更符合"一键部署、新环境能直接复现"的要求。
# apps/definitions/argo-workflows.yaml 里已经把 crds.install 关掉,不再
# 依赖 chart 自带的下载 Job 和这次已经用不到的 HTTP_PROXY/HTTPS_PROXY
# extraEnv。
#
# 幂等:kubectl apply --server-side 本身就是幂等的。
#
# 用法:
#   ./scripts/25-install-argo-workflows-crds.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/install-argo-workflows-crds.log"
echo "=== install-argo-workflows-crds $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

echo "==> kubectl apply --server-side 7 个 vendor 进仓库的完整版 CRD"
kubectl apply --server-side --force-conflicts \
  -f apps/argo-workflows-crds/manifests/ \
  2>&1 | tee -a "$LOG_FILE"

echo
echo "==> 确认"
kubectl get crd | grep argoproj.io

echo
echo "完成。详细日志: ${LOG_FILE}"
