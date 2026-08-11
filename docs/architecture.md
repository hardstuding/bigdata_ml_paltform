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
│ 身份(Keycloak)│   │ Kafka · Flink*                           │
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
| 底座 | Kubernetes(colima + k3s) | 统一调度层 | 中 | ✅ | ✅ | ✅ | Phase 0 |
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
| 数据工程 | Kafka | 消息队列,公司现有生产环境同款 | 中 | — | ✅ | ✅ | Phase 2 |
| 数据工程 | Flink | 实时计算 / CDC | 重 | — | — | ✅ | Phase 4 |
| AI/ML | JupyterHub | 多用户 Notebook | 中 | — | ✅ | ✅ | Phase 3 |
| AI/ML | MLflow | 实验跟踪 / 模型注册 | 轻 | — | ✅ | ✅ | Phase 3 |
| AI/ML | Argo Workflows | 训练流水线编排 | 中 | — | ✅ | ✅ | Phase 3 |
| AI/ML | KServe | 模型在线服务 | 中 | — | ✅ | ✅ | Phase 3 |
| AI/ML | TF Serving / vLLM | 具体推理 runtime | 重 | — | ✅ | ✅ | Phase 3 |
| AI/ML | Feast | 特征存储(离线 Iceberg + 在线 Redis) | 中 | — | — | ✅ | Phase 3.5 |

## 环境画像

- **local-lite**(本机 M2/16GB/colima + k3s):Kubernetes + ArgoCD + Ingress + Keycloak + Prometheus/Grafana + MinIO + Postgres + Hive Metastore + Iceberg。目标是验证 GitOps 流程和存储/元数据打通,不追求性能。
- **cloud-full**(公有云或公司 IDC 机房,建议 ≥32GB;demo 跑通后再接入,生产大概率落在自有 IDC):local-lite 全部 + Trino/Superset/OpenMetadata + Airflow/SeaTunnel/Spark Operator/Kafka + JupyterHub/MLflow/Argo Workflows/KServe。目标是功能完整的开发与集成验证环境。
- **prod**:cloud-full 全部 + Harbor + Ranger + 接入现有遗留 Hadoop 集群(Trino 联邦)+ 按需 Flink/Feast/HBase/Doris。目标是替换掉现有的旧平台。

## 路线图

| Phase | 目标 | 退出标准 |
|---|---|---|
| 0 | 平台底座 | 改一个 values 文件、push,ArgoCD 能自动同步到集群 |
| 1 | 湖仓核心(local-lite) | ✅ 建一张 Iceberg 表、写入,Trino 读出、Superset 出图(2026-08-10 验证,见 `scripts/08-create-demo-data.sh`);Spark 读出还没做,留到 Spark Operator 真正跑作业时一起验证 |
| 2 | 数据工程(转 cloud-full) | SeaTunnel → Iceberg → Airflow 调度 → Superset 看板端到端跑通 |
| 3 | AI/ML | ✅ 核心链路已验证(2026-08-11,见 ADR-025/026/027):JupyterHub/Argo Workflows/MLflow 接了 Keycloak SSO,模型训练 → MLflow 注册 → KServe(Standard 模式)部署成 InferenceService,V2 协议推理请求验证通过(`scripts/09-train-demo-model.sh` + `scripts/11-deploy-demo-inference-service.sh`)。算法/模型 A-B 实验用 KServe 原生的 canary 流量切分这条还没做(不是单独部署一套产品分析工具,见下面"还没定的事"里 2026-08-11 那条) |
| 3.5 | AI 闭环验证 | Feast 打通离线/在线特征,接入模型服务 |
| 4 | 企业化增强(prod) | Harbor + Ranger + 遗留集群正式联邦对接,可作为旧平台替代方案上生产 |

## 还没定的事

- **2026-08-11,A-B 实验工具的落点**:一开始想的是 PostHog + ClickHouse,
  调研发现 PostHog 官方已经在 2023-05 不支持 k8s 部署了(只有社区非官方
  维护的 chart),而且 PostHog 本质是消费端产品分析工具(feature flag、
  session replay,面向的是网页/App 的终端用户)。跟用户确认后,明确了
  实际需求是**算法/模型层面的 A-B 实验**(比如比较不同模型版本的效果),
  不是消费端产品分析,而且目前公司产品/应用侧还没有自己的 A-B 工具——
  但这类实时流量分配/特征开关的工具天然应该跟着"服务真实用户请求"的应用
  基础设施走,不适合放进一个做批处理/数仓/BI 的大数据平台里。算法层面的
  A-B 实验,更自然的落点是 KServe 的 canary 流量切分(Phase 3,原生支持,
  不需要额外工具做流量分配)+ 复用现有的 MLflow/湖仓链路做效果分析(把
  "这次请求用的是哪个模型版本"当一个字段记下来,落到 Iceberg 表,用
  Trino/Superset 分析不同版本的业务指标差异——这条链路 2026-08-10 已经
  验证过端到端能跑通,见 `scripts/08-create-demo-data.sh` / `scripts/09-train-demo-model.sh`)。
  不再计划单独部署 PostHog 或者同类产品分析工具。
- 云服务器什么时候接入、大概配置 —— 决定 Phase 2 什么时候能开始
- GitHub 仓库建在个人账号还是组织下,是否私有
- Ranger 的插拔式授权点现在要不要在 Trino/Hive 配置里提前占位
- ~~Superset 查 Trino 用什么身份~~ **已解决(2026-08-10,ADR-021)**:方案 (a),
  给 Trino 加了并存的 PASSWORD 认证方式,专门给 Superset 用的服务账号
  (`superset_service`,file password authenticator + bcrypt),人类继续走
  Keycloak OAuth2,两条路互不干扰,已验证真实查询能跑通。
- ~~端到端 demo 具体要展示什么~~ **已解决(2026-08-10)**:两条路径都做了。
  (a) 湖仓核心——`iceberg.demo.orders` 表 → Trino → Superset
  Dataset/Chart/Dashboard,见 `scripts/08-create-demo-data.sh`。(b) AI/ML——
  训练一个真实 sklearn 模型 → MLflow 记录实验/指标 → Model Registry 注册,
  见 `scripts/09-train-demo-model.sh`(ADR-023,训练任务直接连集群内部
  Service,不走 oauth2-proxy,和 Trino 服务账号是同一类"人走 SSO、服务到
  服务走不了这条路"的问题,但解法更简单)。Data + AI 两条主线都有一条真实
  跑通的链路了。
