# 架构总览

> 这是权威版本,随架构演进直接在这里更新。可视化版本见 Claude 生成的 [architecture artifact](https://claude.ai/code/artifact/8e614524-4b2b-49c0-80b9-542109a39c52)(两边保持同步,以本文件为准)。

## 定位

不做 CDH 的复刻品,也不做一次性 Demo。目标是一套能在本机验证、按 profile 切换规模、最终原样搬到生产的平台骨架 —— 湖仓为核心,兼容现有 Hadoop 体系,面向未来以 AI Agent 为主要运维者设计。

## 设计原则

1. **兼容而非重建** —— 不在 k8s 里重新搭一套 HDFS/YARN 去平替现有遗留 Hadoop 集群。新平台用 Trino 联邦查询直接接现有 Hive/HDFS,数据按需渐进搬进 Iceberg,而不是一次性迁移。见 [ADR-003](decisions/003-no-hdfs-on-k8s.md)。
2. **环境画像(Profile)** —— 同一套 Helm chart,不同 values 文件决定开哪些组件、配多少资源。`local-lite` / `cloud-full` / `prod` 是三个画像,不是三套代码。见 [ADR-004](decisions/004-environment-profiles.md)。
3. **组件独立可升级** —— 每个组件是 ArgoCD 里独立的 Application,各自锁定 chart 版本、各自发布。禁止用一个大 umbrella chart 把所有组件焊在一起。
4. **AI 原生可运维** —— GitOps 即操作接口:人和 AI Agent 都通过提交 Git 变更来操作平台。机器状态 = Git 状态,不允许手动 `kubectl apply` 之类的旁路操作。见 [ADR-005](decisions/005-argocd-gitops.md)、[ADR-006](decisions/006-ai-agent-identity-v1.md)。
5. **治理预留位,不预先重** —— Keycloak 现在就上,是身份底座。细粒度数据权限(行/列级)现在不部署,但查询引擎都通过标准插拔式授权接口接入 —— 以后装(倾向于 OPA,理由见 [ADR-028](decisions/028-iam-org-model.md))是配置变更,不是重新架构。
6. **可交接、可一键拉起、可原样上生产** —— 三条硬性约束,不是阶段性目标:
   - **可交接**:新接手的人(人类或另一个 AI)只靠仓库本身就能读懂现状,
     不依赖任何一次对话的记忆。约定是:决策记 ADR、架构记这份文档、进度记
     `project/`,不允许只存在于某次对话里或某个 AI 的私有记忆里。
   - **可一键拉起**:新集群一条命令跑通(`scripts/bootstrap-all.sh`),
     不是一串照着 README 手敲的步骤。
   - **可原样上生产**:改配置就能从 local-lite 切到 cloud-full/prod,
     不是三套要分别维护的副本。机制是 `environments/<env>/config.yaml`
     选组件 + `environments/resource-profiles.yaml` 分规格,见
     [ADR-059](decisions/059-resource-profiles.md)。

   这三条**当前各自做到了什么程度**,见
   [`project/production-readiness-gaps.md`](project/production-readiness-gaps.md)
   —— 原则写在这里,进度不写在这里。

7. **可插拔基础设施** —— 真实企业大概率已经有自己的 Postgres / Kafka /
   对象存储 / 统一身份系统,不该强制"必须用我们打包的那一份"。做法是在每个
   组件的 Application 里用统一的 `【可插拔基础设施】` 注释标出"这里可以换成
   外部实例"和对应要跳过的初始化步骤,**不引入中心化的生成器或抽象层**
   (为什么不做生成器,见 ADR-030 的取舍)。见
   [ADR-030](decisions/030-pluggable-external-infrastructure.md)。

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
│              │   │ Iceberg · Trino · OPA(预留,细粒度权限)·HBase*/Doris*│
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

**local-lite 这一列是"这个 Phase 设计上归哪个环境"的静态标注,不是这台机器
现在实际跑着什么**——按需启用/关闭是这台机器的常态(资源有限,验证完
一个组件经常发现还要接着测下一个,就没关掉),真实的"现在到底启用哪些
组件"以 `environments/<env>/config.yaml` 里的 `enabled_components`
列表(2026-08-20 起,ADR-057 第三批)和 `ls apps/definitions/` 的当前
输出为准,不要相信这张表或其他文档里任何写死的组件名单——2026-08-14
文档审计就发现过 Spark Operator/Airflow 在这张表标着"cloud-full 才有",
但实际已经在 `apps/definitions/` 常驻好几天,文档没跟上。

| 层 | 组件 | 作用 | 资源权重 | local-lite(设计归属) | cloud-full | prod | 阶段 |
|---|---|---|---|---|---|---|---|
| 底座 | Kubernetes(colima + k3s) | 统一调度层 | 中 | ✅ | ✅ | ✅ | Phase 0 |
| 底座 | ArgoCD | GitOps 持续部署 | 轻 | ✅ | ✅ | ✅ | Phase 0 |
| 底座 | ingress-nginx + cert-manager | 统一入口与证书 | 轻 | ✅ | ✅ | ✅ | Phase 0 |
| 底座 | Keycloak | 统一身份 / OIDC | 中 | ✅ | ✅ | ✅ | Phase 0 |
| 底座 | Prometheus + Grafana + Loki | 指标 + 日志 | 中 | ✅ | ✅ | ✅ | Phase 0 |
| 底座 | Harbor | 私有镜像仓库 | 轻 | — | ✅ | ✅ | Phase 4 |
| 底座 | 平台门户(自建) | 统一入口页面,现场探测各工具状态 | 轻 | ✅ | ✅ | ✅ | Phase 0 |
| 治理 | 权限申请门户(自建) | 组权限申请 + 表访问分级审批 + 权限交接 + 审计 + 到期自动回收 | 轻 | ✅ | ✅ | ✅ | Phase 0 |
| 治理 | 建表注册工具(自建) | 建表 + 回写负责人/安全等级进 OpenMetadata | 轻 | ✅ | ✅ | ✅ | Phase 0 |
| 治理 | AI 运维角色(RBAC) | 给 AI 独立 ServiceAccount + 权限边界,开发阶段档已实测,运维阶段收紧档+危险操作审批链未实现(ADR-048) | 轻 | ⚠️ 部分 | — | — | Phase 0 |
| 治理 | OPA(Trino 细粒度权限) | 策略引擎 + grants 数据实时同步,已实测(opa test + 真实 HTTP 场景验证);**故意没有**接进 Trino 的 access-control.properties 生效——上线是一次真实的行为收紧(Trino 现在零访问控制),需要先确认现有数据源(比如 Superset 用的表)都有对应 grant,不能无人看管时直接切换,见 ADR-051 | 轻 | ⚠️ 未接入 | — | — | Phase 0 |
| 湖仓 | MinIO | S3 兼容对象存储 | 轻 | ✅ | ✅ | ✅ | Phase 1 |
| 湖仓 | Postgres(CloudNativePG operator 管理) | 元数据库共用 | 轻 | ✅ | ✅ | ✅ | Phase 1 |
| 湖仓 | Hive Metastore | 表元数据 | 轻 | ✅ | ✅ | ✅ | Phase 1 |
| 湖仓 | Iceberg | 开放表格式 | 轻 | ✅ | ✅ | ✅ | Phase 1 |
| 湖仓 | Trino | 交互式 SQL / 联邦查询 | 重 | — | ✅ | ✅ | Phase 1 |
| 湖仓 | OpenMetadata | 数据目录 / 血缘 | 重 | — | ✅ | ✅ | Phase 1 |
| 湖仓 | Superset | BI / 看板 | 中 | — | ✅ | ✅ | Phase 1 |
| 湖仓 | OPA(原计划 Ranger,见 ADR-028) | 细粒度权限(行/列级) | 中 | — | — | ✅ | Phase 4 |
| 湖仓 | HBase / Doris | KV / OLAP,按需 | 重 | — | — | 可选 | Backlog |
| 数据工程 | Airflow | 批处理编排 | 中 | — | ✅ | ✅ | Phase 2 |
| 数据工程 | SeaTunnel | 批流一体数据集成 | 中 | — | ✅ | ✅ | Phase 2 |
| 数据工程 | Spark Operator | k8s 原生 Spark 作业 | 重 | — | ✅ | ✅ | Phase 2 |
| 数据工程 | Kafka | 消息队列,公司现有生产环境同款 | 中 | — | ✅ | ✅ | Phase 2 |
| 数据工程 | Flink | 实时计算 / CDC | 重 | — | — | ✅ | Phase 4(2026-08-15 用户确认为必要组件,不是"按需"可选项,见 `docs/project/roadmap.md`) |
| AI/ML | JupyterHub | 多用户 Notebook | 中 | — | ✅ | ✅ | Phase 3 |
| AI/ML | MLflow | 实验跟踪 / 模型注册 | 轻 | — | ✅ | ✅ | Phase 3 |
| AI/ML | Argo Workflows | 训练流水线编排 | 中 | — | ✅ | ✅ | Phase 3 |
| AI/ML | KServe | 模型在线服务 | 中 | — | ✅ | ✅ | Phase 3 |
| AI/ML | TF Serving / vLLM | 具体推理 runtime | 重 | — | ✅ | ✅ | Phase 3 |
| AI/ML | Feast | 特征存储(离线 Spark+Iceberg + 在线 Redis) | 中 | ✅ | ✅ | ✅ | Phase 3.5 |

## 环境画像

- **local-lite**(本机 M2/16GB/colima + k3s):Kubernetes + ArgoCD + Ingress + Keycloak + Prometheus/Grafana + MinIO + Postgres + Hive Metastore + Iceberg。目标是验证 GitOps 流程和存储/元数据打通,不追求性能。
- **cloud-full**(公有云或公司 IDC 机房,建议 ≥32GB;demo 跑通后再接入,生产大概率落在自有 IDC):local-lite 全部 + Trino/Superset/OpenMetadata + Airflow/SeaTunnel/Spark Operator/Kafka + JupyterHub/MLflow/Argo Workflows/KServe。目标是功能完整的开发与集成验证环境。
- **prod**:cloud-full 全部 + Harbor + OPA(细粒度数据权限)+ 接入现有遗留 Hadoop 集群(Trino 联邦)+ 按需 Flink/Feast/HBase/Doris。目标是替换掉现有的旧平台。

## 进度

架构长什么样和"我们做到哪了"是两件事,这份文档只回答前者。进度看:

- [`project/capability-matrix.md`](project/capability-matrix.md) ——
  五个角色今天各自真的能做什么(**这是权威入口**)
- [`project/phase-history.md`](project/phase-history.md) —— Phase 0-4 的
  组件部署与验证记录(历史)
- [`project/production-readiness-gaps.md`](project/production-readiness-gaps.md)
  —— 距离生产可用还差什么

## 未决的架构问题

只列**还没定、而且会影响架构形态**的。已经定了的看
[`decisions/`](decisions/);完整的历史流水(包括当时是怎么决的)看
[`project/open-questions-log.md`](project/open-questions-log.md)。

- **SSO 的可插拔**。Postgres 和对象存储这两条"接公司已有实例"已经推广开了,
  **Keycloak → 公司已有 IdP 这条还没有**。它的改动面比前两条大得多(所有
  组件的 OIDC 配置、组/角色映射、以及 `platform/iam/` 这套组织架构数据的
  归属),需要单独出 ADR。
- **A-B 实验的落点**。分流和指标口径放在哪一层没有定:放查询层(Trino/
  Superset 各自算)还是引入独立的实验平台。倾向前者(不新增组件),但没有
  真实需求驱动之前不定。
- **企业内部已有 Prometheus/Grafana 的对接方式**。是把平台指标推过去,还是
  平台自带一套、只把关键告警转发出去。取决于对方的接入规范,现在定太早。
- **多集群 / 多机房**。当前所有设计都假设单个 Kubernetes 集群。真要跨机房,
  Ingress、身份、对象存储、元数据的一致性都要重新考虑,不是加个 Application
  就完事。
