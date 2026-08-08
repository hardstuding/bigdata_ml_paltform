# bigdata_ml_paltform

Kubernetes-native Data + AI Platform. 目标不是复刻 CDH,而是一套本地能验证、按 profile 切换规模、最终原样搬上生产的平台骨架,同时兼容现有的遗留 Hadoop 集群。

架构总览、组件清单、路线图见 [`docs/architecture.md`](docs/architecture.md);关键决策的取舍理由见 [`docs/decisions/`](docs/decisions);日常运维操作见 [`docs/operations/`](docs/operations)。

## 仓库结构

```
infra/           # 本地/云端集群自举(OrbStack + k8s、云端节点初始化)
platform/        # 平台底座:ArgoCD、ingress-nginx、cert-manager、Keycloak、监控
apps/            # 各业务组件,每个组件一个目录,独立 Helm/Kustomize 配置
environments/    # local-lite / cloud-full / prod 三个环境画像的 values 覆盖
scripts/         # 一键拉起 / 销毁 / 常用运维脚本
docs/            # 架构文档、ADR、运维手册 —— 权威版本,新会话/新 agent 靠这个对齐上下文
```

## 环境画像

| Profile | 用途 | 位置 |
|---|---|---|
| `local-lite` | 本机验证 GitOps 流程 + 存储/元数据打通 | Mac (M2/16GB, OrbStack) |
| `cloud-full` | 功能完整的开发与集成环境 | 云服务器 |
| `prod` | 替换现有遗留大数据平台 | 生产环境 |

同一套 Helm chart,不同 `environments/<profile>/values.yaml` 决定开哪些组件、配多少资源。

## 当前状态

Phase 0 进行中(平台底座)。详见 [`docs/architecture.md`](docs/architecture.md) 里的路线图。
