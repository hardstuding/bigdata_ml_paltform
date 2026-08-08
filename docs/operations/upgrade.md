# 组件升级

> 占位文档,随 Phase 0 起会补充实际操作过的升级记录。原则见 [ADR-005](../decisions/005-argocd-gitops.md):每个组件是独立 ArgoCD Application,升级一个不应牵动其他组件。

## 标准流程

1. 在 `apps/<component>/Chart.yaml`(或 values 里的 `targetRevision`)改版本号。
2. 本地 `helm template` 走一遍 diff,确认没有意外的破坏性变更。
3. push 到 Git,在 `local-lite` 先验证。
4. 验证通过后,把变更同步到 `cloud-full` / `prod` 对应的 values(可能是同一个 PR,也可能分开,取决于变更风险)。

## 各组件已知的升级注意事项

(留空,遇到具体坑再补充)
