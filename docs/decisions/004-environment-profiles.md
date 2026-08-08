# 004. 用环境画像(profile)而不是分叉代码来适配不同规模

- 状态: 已采纳(2026-08-08)

## 背景

本地 Mac 只有 16GB 内存,跑不动全部组件同时常驻;未来云服务器、以及最终的生产环境,资源规模和要开启的组件都不一样。常见的失败模式是"开发环境 = 生产环境"的假设不成立,或者为每个环境单独维护一套配置导致漂移。

## 决策

定义三个环境画像:`local-lite`、`cloud-full`、`prod`。三者共用同一套 Helm chart / Kustomize base,只通过 `environments/<profile>/values.yaml` 覆盖哪些组件开启、给多少资源。不为不同环境写分叉的组件配置。

## 理由

- 保证"本地验证过的配置,换个 values 就能在云上跑"这个承诺是可执行的,而不是停留在口头。
- 组件粒度独立(见 [ADR-005](005-argocd-gitops.md) 的 per-app Application 设计),profile 只决定"这个 ArgoCD Application 在这个环境要不要存在、resources 配多少",不涉及重写组件本身的部署逻辑。

## 后果

- 新增组件时,必须同时想清楚它在三个 profile 里各自的状态(开启/关闭/降配),而不是只考虑当前在跑的那个环境。
- `environments/` 目录下的 values 文件是这套系统里少数"环境相关"的地方,评审配置变更时要重点看这里有没有不小心把 local 专用的降配(比如单副本 Postgres)带到了 prod 的 values 里。
