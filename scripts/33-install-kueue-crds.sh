#!/usr/bin/env bash
# 单独装 Kueue 的 11 个 CRD。
#
# **为什么不能交给 ArgoCD**:workloads.kueue.x-k8s.io 这个 CRD 单个文件
# 1.4MB,ArgoCD 同步时报
#   metadata.annotations: Too long: may not be more than 262144 bytes
# ——它把整份 manifest 塞进 last-applied-configuration 注解,超过了 K8s 的
# 硬限制。这个仓库已经在 kube-prometheus-stack(scripts/04)、
# CloudNativePG(scripts/16)、argo-workflows(scripts/25)上踩过三次,
# **syncOptions 里的 ServerSideApply=true 解决不了**(2026-08-23 实测:
# 加了照样报同一个错,和 apps/components/cloudnative-pg-operator.yaml 里
# 那段记录一致)。
#
# CRD 的来源是 vendor 进仓库的 chart,已经在 vendor 时渲染成纯 YAML 摘到
# 了 crds-out-of-band/(见 scripts/28-vendor-helm-chart.sh 的 --exclude-crds),
# **不联网**——升级 Kueue 版本时重跑那条 vendor 命令,这里不用改。
#
# 幂等:kubectl apply --server-side 本身就是幂等的,重复跑没有副作用。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/install-kueue-crds.log"
echo "=== install-kueue-crds $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

CRD_FILE="apps/kueue-chart/crds-out-of-band/crds.yaml"
[ -f "$CRD_FILE" ] || {
  echo "找不到 $CRD_FILE ——先跑:" >&2
  echo "  ./scripts/28-vendor-helm-chart.sh oci://registry.k8s.io/kueue/charts kueue <版本> apps/kueue-chart --exclude-crds" >&2
  exit 1
}

echo "==> kubectl apply --server-side ${CRD_FILE}" | tee -a "$LOG_FILE"
kubectl apply --server-side --force-conflicts -f "$CRD_FILE" 2>&1 | tee -a "$LOG_FILE"

echo "==> 确认 CRD 已注册" | tee -a "$LOG_FILE"
kubectl get crd -o name | grep kueue.x-k8s.io | tee -a "$LOG_FILE" | wc -l | xargs echo "    共" | tee -a "$LOG_FILE"
