#!/usr/bin/env bash
# kserve-resources 这个 Helm chart(oci://ghcr.io/kserve/charts/kserve-resources)
# 从 v0.19.0 开始不再打包 ClusterServingRuntime 资源(sklearn/xgboost/mlserver等)
# ——这些是 KServe 主仓库 config/runtimes/ 下的静态 YAML,官方自己的安装方式
# (quick_install.sh)是单独 kubectl apply -k 这个目录,不归 Helm chart管。
# ArgoCD 也没法管这个:它不是哪个 chart 的一部分,单独建 Application 意义不大
# (只有十几个 ClusterServingRuntime 声明,没有需要 GitOps 追踪变更的价值),
# 所以和 scripts/04-install-kube-prometheus-crds.sh 一样,走一次性手动脚本。
#
# 2026-08-15 版本审计后改成 apply 本地 vendor 的
# apps/kserve-runtimes/manifests/(见那个目录下 kustomization.yaml 的
# 说明),不再直接 `kubectl apply -k` 官方 GitHub 仓库——原来那种写法每次
# 跑结果都取决于上游此刻的内容,不可重现,而且官方那份 kustomization.yaml
# 里有 7 个 runtime 镜像用的是浮动的 latest/latest-gpu,违反这个项目自己
# "版本必须显式固定"的规则。vendor 下来之后这些都改成了显式版本号
# (v0.19.0,和 kserve-resources 控制面版本对齐)+ digest。
#
# 幂等:kubectl apply 天然幂等,重复跑安全。

set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/install-kserve-serving-runtimes.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== $(date -u +%FT%TZ) 安装 KServe ClusterServingRuntimes(本地 vendor,apps/kserve-runtimes/manifests/) ==="

kubectl apply -k apps/kserve-runtimes/manifests/

echo "=== 验证 ==="
kubectl get clusterservingruntimes.serving.kserve.io

echo "=== 完成 $(date -u +%FT%TZ) ==="
