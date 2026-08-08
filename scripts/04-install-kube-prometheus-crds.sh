#!/usr/bin/env bash
# 一次性 bootstrap 脚本,不属于 GitOps 流程(和 ArgoCD 本身、platform/root-app.yaml
# 一样是允许手动执行的例外,见 ADR-005)。
#
# 背景:kube-prometheus-stack 的 CRD(尤其 prometheuses.monitoring.coreos.com)体积
# 超过 262144 字节的 annotation 上限。ArgoCD 即使开了 ServerSideApply=true 也还是会
# 在这几个 CRD 上踩到同样的校验错误(具体原因待查,怀疑是 ArgoCD 内部渲染/dry-run
# 阶段的某个环节仍然依赖了会超限的 annotation)。绕过办法:CRD 单独用原生
# `kubectl apply --server-side` 装,chart 本体设 `crds.enabled: false` 交给 ArgoCD
# 管理其余资源。详见 docs/operations/troubleshooting.md。
#
# 什么时候要重新跑:升级 kube-prometheus-stack 版本、CRD schema 变化时。
set -euo pipefail

CHART_VERSION="${1:-88.2.0}"

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update prometheus-community >/dev/null

echo "==> 提取 kube-prometheus-stack ${CHART_VERSION} 自带的 CRD"
helm show crds prometheus-community/kube-prometheus-stack --version "${CHART_VERSION}" > /tmp/kps-crds.yaml

echo "==> server-side apply"
kubectl apply --server-side --force-conflicts -f /tmp/kps-crds.yaml

echo "==> 完成,CRD 列表:"
kubectl get crd | grep monitoring.coreos.com
