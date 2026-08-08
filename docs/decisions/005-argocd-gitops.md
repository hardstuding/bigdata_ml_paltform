# 005. ArgoCD + GitOps 作为唯一的变更入口

- 状态: 已采纳(2026-08-08)

## 背景

平台的长期目标是"AI Agent 是主要运维者,人类只做辅助"。如果操作方式是 ssh 上机改 yaml、手动 `kubectl apply`,机器的真实状态和仓库里记录的状态会逐渐漂移,AI Agent 也很难安全地参与运维。

## 决策

用 ArgoCD 做 GitOps 持续部署。每个组件是 ArgoCD 里独立的 Application,禁止手动 `kubectl apply` / `helm install` 做常驻变更(临时调试除外,调试后的最终状态必须回写到 Git)。所有变更路径统一为:改 Git → push → ArgoCD 同步到集群。

## 理由

- "机器状态 = Git 状态" 是可审计、可回滚、可被 AI Agent 安全操作的前提 —— Agent 提交一个 PR 远比让它直接拿 kubeconfig 操作生产集群更可控。
- 每个组件独立 Application(而不是一个大 umbrella chart),意味着升级/回滚一个组件不需要牵动其他组件,降低"以后不敢升级"的风险。

## 后果

- 引导阶段(Phase 0 装 ArgoCD 本身)之外,不应该再出现手动 `helm install` 常驻资源的情况。
- 需要给人和未来的 AI Agent 分配到 Git 仓库的写权限(分支保护 / PR 流程),而不是直接给 kubeconfig,见 [ADR-006](006-ai-agent-identity-v1.md)。
