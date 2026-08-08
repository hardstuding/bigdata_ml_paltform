#!/usr/bin/env bash
# 安装/升级 ArgoCD 本身。这是唯一允许手动 helm install/upgrade 的组件
# (GitOps 引擎本身,鸡生蛋问题,见 ADR-005)。装完之后所有其他组件都通过
# platform/root-app.yaml 和 apps/root-app.yaml 交给 ArgoCD 管理,不再需要
# 手动 helm/kubectl 常驻变更。
#
# 用法:
#   ./scripts/01-bootstrap-argocd.sh              # 标准环境(云/IDC,网络能直连 git 仓库)
#   NEEDS_LOCAL_PROXY=1 ./scripts/01-bootstrap-argocd.sh   # 本机 + colima 这种需要过代理才能出网的环境
set -euo pipefail

helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
helm repo update argo >/dev/null

kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f - >/dev/null

VALUES_ARGS=(-f platform/bootstrap/argocd-values.yaml)
if [ "${NEEDS_LOCAL_PROXY:-0}" = "1" ]; then
  echo "==> 叠加本机代理 overlay(argocd-values.local-proxy.yaml)"
  VALUES_ARGS+=(-f platform/bootstrap/argocd-values.local-proxy.yaml)
fi

helm upgrade --install argocd argo/argo-cd -n argocd "${VALUES_ARGS[@]}"

echo "==> 等待核心组件就绪"
kubectl -n argocd rollout status deploy/argocd-repo-server --timeout=180s
kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=180s
kubectl -n argocd rollout status deploy/argocd-server --timeout=180s

echo
echo "完成。初始管理员密码:"
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
echo
echo "访问方式(本地调试用):kubectl -n argocd port-forward svc/argocd-server 8080:443"
