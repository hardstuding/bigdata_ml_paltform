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

- ✅ Phase 0(平台底座):ArgoCD + ingress-nginx + cert-manager + Keycloak + kube-prometheus-stack,全部 Synced/Healthy
- ✅ Phase 1(湖仓核心,local-lite 范围):MinIO(`lakehouse` bucket)+ Postgres(单实例)+ Hive Metastore,已验证 Thrift 端口连通、metastore 库建好
- ⏳ Phase 1 完整退出标准(Spark/Trino 各自读写同一张 Iceberg 表)要等 Phase 2 引入 Spark Operator 后才补验证,现在的范围是"存储 + 元数据服务健康",还没有计算引擎接进来

详见 [`docs/architecture.md`](docs/architecture.md) 里的路线图,踩过的坑都记在 [`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md)。
