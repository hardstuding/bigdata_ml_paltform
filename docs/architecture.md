# 架构总览

> 这是权威版本,随架构演进直接在这里更新。可视化版本见 Claude 生成的 [architecture artifact](https://claude.ai/code/artifact/8e614524-4b2b-49c0-80b9-542109a39c52)(两边保持同步,以本文件为准)。

## 定位

不做 CDH 的复刻品,也不做一次性 Demo。目标是一套能在本机验证、按 profile 切换规模、最终原样搬到生产的平台骨架 —— 湖仓为核心,兼容现有 Hadoop 体系,面向未来以 AI Agent 为主要运维者设计。

## 设计原则

1. **兼容而非重建** —— 不在 k8s 里重新搭一套 HDFS/YARN 去平替现有遗留 Hadoop 集群。新平台用 Trino 联邦查询直接接现有 Hive/HDFS,数据按需渐进搬进 Iceberg,而不是一次性迁移。见 [ADR-003](decisions/003-no-hdfs-on-k8s.md)。
2. **环境画像(Profile)** —— 同一套 Helm chart,不同 values 文件决定开哪些组件、配多少资源。`local-lite` / `cloud-full` / `prod` 是三个画像,不是三套代码。见 [ADR-004](decisions/004-environment-profiles.md)。
3. **组件独立可升级** —— 每个组件是 ArgoCD 里独立的 Application,各自锁定 chart 版本、各自发布。禁止用一个大 umbrella chart 把所有组件焊在一起。
4. **AI 原生可运维** —— GitOps 即操作接口:人和 AI Agent 都通过提交 Git 变更来操作平台。机器状态 = Git 状态,不允许手动 `kubectl apply` 之类的旁路操作。见 [ADR-005](decisions/005-argocd-gitops.md)、[ADR-006](decisions/006-ai-agent-identity-v1.md)。
5. **治理预留位,不预先重** —— Keycloak 现在就上,是身份底座。Ranger 现在不部署,但查询引擎都通过标准插拔式授权接口接入 —— 以后装 Ranger 是配置变更,不是重新架构。

## 分层架构

```
用户与入口: 数据工程师 / 分析师 / 算法工程师 / 业务·运营 / AI Agent
                              │  统一入口(Keycloak OIDC)
                              ▼
┌──────────────┐   ┌─────────────────────────────────────────┐
│ 横切关注点     │   │ L2 · AI/ML(面向使用者)                     │
│ (平台底座)     │   │ JupyterHub · MLflow · Argo Workflows ·   │
│              │   │ KServe · TF Serving/vLLM · Feast*        │
│ Kubernetes   │   ├─────────────────────────────────────────┤
│ GitOps(ArgoCD)│  │ L1 · 数据工程与管道                          │
│ Ingress+TLS  │   │ Airflow · SeaTunnel · Spark Operator ·   │
│ 身份(Keycloak)│   │ Redpanda · Flink*                        │
│ 镜像仓库(Harbor)│ ├─────────────────────────────────────────┤
│ 可观测性      │   │ L0 · 湖仓核心(存储与元数据)                    │
│              │   │ MinIO · Postgres · Hive Metastore ·      │
│              │   │ Iceberg · Trino · Ranger(预留)·HBase*/Doris*│
└──────────────┘   └─────────────────────────────────────────┘
      ▲                              ▲
      │ push → 同步                    │ Trino 联邦查询 · 渐进迁移
┌──────────────┐            ┌───────────────────────────┐
│ Git 仓库(本仓库)│            │ 现有遗留 Hadoop 集群(线下机房) │
└──────────────┘            │ HDFS · Hive · HBase · Doris │
                             └───────────────────────────┘
```

`*` = 架构上预留位置,不在当前阶段部署清单里。

## 组件清单

| 层 | 组件 | 作用 | 资源权重 | local-lite | cloud-full | prod | 阶段 |
|---|---|---|---|---|---|---|---|
| 底座 | Kubernetes(OrbStack) | 统一调度层 | 中 | ✅ | ✅ | ✅ | Phase 0 |
| 底座 | ArgoCD | GitOps 持续部署 | 轻 | ✅ | ✅ | ✅ | Phase 0 |
| 底座 | ingress-nginx + cert-manager | 统一入口与证书 | 轻 | ✅ | ✅ | ✅ | Phase 0 |
| 底座 | Keycloak | 统一身份 / OIDC | 中 | ✅ | ✅ | ✅ | Phase 0 |
| 底座 | Prometheus + Grafana + Loki | 指标 + 日志 | 中 | ✅ | ✅ | ✅ | Phase 0 |
| 底座 | Harbor | 私有镜像仓库 | 轻 | — | ✅ | ✅ | Phase 4 |
| 湖仓 | MinIO | S3 兼容对象存储 | 轻 | ✅ | ✅ | ✅ | Phase 1 |
| 湖仓 | Postgres(单实例 → CloudNativePG) | 元数据库共用 | 轻 | ✅ | ✅ | ✅ | Phase 1 |
| 湖仓 | Hive Metastore | 表元数据 | 轻 | ✅ | ✅ | ✅ | Phase 1 |
| 湖仓 | Iceberg | 开放表格式 | 轻 | ✅ | ✅ | ✅ | Phase 1 |
| 湖仓 | Trino | 交互式 SQL / 联邦查询 | 重 | — | ✅ | ✅ | Phase 1 |
| 湖仓 | OpenMetadata | 数据目录 / 血缘 | 重 | — | ✅ | ✅ | Phase 1 |
| 湖仓 | Superset | BI / 看板 | 中 | — | ✅ | ✅ | Phase 1 |
| 湖仓 | Ranger | 细粒度权限 | 重 | — | — | ✅ | Phase 4 |
| 湖仓 | HBase / Doris | KV / OLAP,按需 | 重 | — | — | 可选 | Backlog |
| 数据工程 | Airflow | 批处理编排 | 中 | — | ✅ | ✅ | Phase 2 |
| 数据工程 | SeaTunnel | 批流一体数据集成 | 中 | — | ✅ | ✅ | Phase 2 |
| 数据工程 | Spark Operator | k8s 原生 Spark 作业 | 重 | — | ✅ | ✅ | Phase 2 |
| 数据工程 | Redpanda | 消息队列(Kafka 协议兼容) | 中 | — | ✅ | ✅ | Phase 2 |
| 数据工程 | Flink | 实时计算 / CDC | 重 | — | — | ✅ | Phase 4 |
| AI/ML | JupyterHub | 多用户 Notebook | 中 | — | ✅ | ✅ | Phase 3 |
| AI/ML | MLflow | 实验跟踪 / 模型注册 | 轻 | — | ✅ | ✅ | Phase 3 |
| AI/ML | Argo Workflows | 训练流水线编排 | 中 | — | ✅ | ✅ | Phase 3 |
| AI/ML | KServe | 模型在线服务 | 中 | — | ✅ | ✅ | Phase 3 |
| AI/ML | TF Serving / vLLM | 具体推理 runtime | 重 | — | ✅ | ✅ | Phase 3 |
| AI/ML | Feast | 特征存储(离线 Iceberg + 在线 Redis) | 中 | — | — | ✅ | Phase 3.5 |

## 环境画像

- **local-lite**(本机 M2/16GB/OrbStack):Kubernetes + ArgoCD + Ingress + Keycloak + Prometheus/Grafana + MinIO + Postgres + Hive Metastore + Iceberg。目标是验证 GitOps 流程和存储/元数据打通,不追求性能。
- **cloud-full**(云服务器,建议 ≥32GB):local-lite 全部 + Trino/Superset/OpenMetadata + Airflow/SeaTunnel/Spark Operator/Redpanda + JupyterHub/MLflow/Argo Workflows/KServe。目标是功能完整的开发与集成验证环境。
- **prod**:cloud-full 全部 + Harbor + Ranger + 接入现有遗留 Hadoop 集群(Trino 联邦)+ 按需 Flink/Feast/HBase/Doris。目标是替换掉现有的旧平台。

## 路线图

| Phase | 目标 | 退出标准 |
|---|---|---|
| 0 | 平台底座 | 改一个 values 文件、push,ArgoCD 能自动同步到集群 |
| 1 | 湖仓核心(local-lite) | 建一张 Iceberg 表、写入,Spark/Trino 各自读出同样的数据 |
| 2 | 数据工程(转 cloud-full) | SeaTunnel → Iceberg → Airflow 调度 → Superset 看板端到端跑通 |
| 3 | AI/ML | 模型从 JupyterHub 训练、MLflow 注册、KServe 一键部署成 API |
| 3.5 | AI 闭环验证 | Feast 打通离线/在线特征,接入模型服务 |
| 4 | 企业化增强(prod) | Harbor + Ranger + 遗留集群正式联邦对接,可作为旧平台替代方案上生产 |

## 还没定的事

- 云服务器什么时候接入、大概配置 —— 决定 Phase 2 什么时候能开始
- GitHub 仓库建在个人账号还是组织下,是否私有
- Ranger 的插拔式授权点现在要不要在 Trino/Hive 配置里提前占位
