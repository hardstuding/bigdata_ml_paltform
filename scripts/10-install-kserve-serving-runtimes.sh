#!/usr/bin/env bash
# kserve-resources 这个 Helm chart(oci://ghcr.io/kserve/charts/kserve-resources)
# 从 v0.19.0 开始不再打包 ClusterServingRuntime 资源(sklearn/xgboost/mlserver等)
# ——这些是 KServe 主仓库 config/runtimes/ 下的静态 YAML,官方自己的安装方式
# (quick_install.sh)是单独 kubectl apply -k 这个目录,不归 Helm chart管。
# ArgoCD 也没法管这个:它不是哪个 chart 的一部分,单独建 Application 意义不大
# (只有十几个 ClusterServingRuntime 声明,没有需要 GitOps 追踪变更的价值),
# 所以和 scripts/04-install-kube-prometheus-crds.sh 一样,走一次性手动脚本。
#
# 这一步在宿主机上直接跑(不是在 pod 里),宿主机能直连 GitHub,不需要走
# platform/coredns-custom 或代理那一套(那些是给集群内 pod 用的)。
#
# 幂等:kubectl apply 天然幂等,重复跑安全。

set -euo pipefail

LOG_FILE="/tmp/kserve-serving-runtimes-install.log"
exec > >(tee -a "$LOG_FILE") 2>&1

KSERVE_VERSION="v0.19.0"

echo "=== $(date) 安装 KServe ClusterServingRuntimes (${KSERVE_VERSION}) ==="

kubectl apply -k "https://github.com/kserve/kserve/config/runtimes?ref=${KSERVE_VERSION}"

echo "=== 验证 ==="
kubectl get clusterservingruntimes.serving.kserve.io

echo "=== 完成 $(date) ==="
