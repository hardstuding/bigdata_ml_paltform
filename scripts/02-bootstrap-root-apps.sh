#!/usr/bin/env bash
# 把 GitOps 的两个 app-of-apps 交给 ArgoCD——这是最后一次手动 kubectl apply,
# 之后所有组件的增删改都通过 git push 完成,ArgoCD 自动同步(见 ADR-005)。
#
# 前置条件:
#   1. scripts/00-generate-secrets.sh 已经跑过(各组件要用的管理员密码已就绪)
#   2. scripts/01-bootstrap-argocd.sh 已经跑过(ArgoCD 本身已经在跑)
#   3. platform/root-app.yaml、apps/root-app.yaml、以及 apps/definitions/*.yaml、
#      platform/apps/*.yaml 里的 repoURL 已经指向你实际要用的 git 仓库地址
#      (不是这几个文件现在写的 GitHub demo 地址)——迁移仓库时先跑
#      scripts/set-repo-url.sh <新地址>,再执行这一步。
set -euo pipefail

kubectl apply -f platform/root-app.yaml
kubectl apply -f apps/root-app.yaml

echo "==> 等待 ArgoCD 完成首次同步(可能需要几分钟,组件比较多)"
sleep 15
kubectl get applications -n argocd

echo
echo "完成。如果有 Application 显示 OutOfSync/Progressing 属于正常现象,"
echo "过一会儿再跑一次上面这条 kubectl get applications -n argocd 看看是否收敛。"
echo "如果长期卡住,先查 docs/operations/troubleshooting.md 有没有已知的坑。"
