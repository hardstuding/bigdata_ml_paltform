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
6. **可交接、可一键拉起、可原样上生产**(2026-08-14 用户明确要求,长期硬性约束,不是某一次任务的临时目标)——
   - **可交接**:项目文档要让新接手的人(不管是人类还是另一个 AI)能不靠对话记忆、只靠仓库本身快速读懂现状。约定是:决策记 ADR、现状记 `docs/architecture.md`/README,不允许只存在于某次对话或者只存在于某个 AI 私有的 memory 系统里——这本身也是 [ADR-040](decisions/040-enterprise-governance-roadmap.md) 那次"记下来但丢了"事故的教训延伸。
   - **可一键拉起**:目标是新集群能一条命令跑通,不是一串手动步骤。**现状离这个目标还有差距**——"从零拉起整套服务"这一节目前是 7 个手动步骤(见 README),没有收敛成单一脚本,是已知欠账,不是已经做到。
   - **可原样上生产**:即"环境画像"这条原则(见上面第 2 条)的验收标准——真正做到"改 values 文件就能从 local-lite 切到 cloud-full/prod",而不是三套要分别维护的配置。**现状同样没有完全做到**:`environments/cloud-full/`、`environments/prod/` 目前只是资源规划参考清单(README 模板),ADR-004 最初设想的"改 values.yaml 自动切环境"这个机制还没有真正建成,是已知欠账。
7. **可插拔基础设施**(用户多次提过,长期原则,不是某个组件的局部优化)—— 这个项目定位是开源项目,不是一次性内部工具,真实企业场景大概率已经有自己的 Postgres/Kafka/对象存储/统一身份系统,不该强制"必须用我们打包的那一份"。做法是在每个组件自己的 Application yaml 里用统一的 `【可插拔基础设施】` 注释标出"这里可以换成外部实例"和对应要跳过的初始化步骤,不引入中心化的生成器/抽象层(为什么不做生成器见 ADR-030 里的取舍)。见 [ADR-030](decisions/030-pluggable-external-infrastructure.md)。**当前进度**:Postgres 这条已经推广到 Keycloak/Hive Metastore/MLflow/Superset/OpenMetadata/Airflow;对象存储(MinIO→外部 S3)推广到 Hive Metastore/Trino/Spark History Server/Postgres 备份;Kafka 是不同的覆盖方式(整个组件不部署,不是改连接串);**SSO(Keycloak→公司已有 IdP)还没推广**,架构改动更大,是明确的后续课题。

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
现在实际跑着什么**——按需 park/unpark 是这台机器的常态(资源有限,验证完
一个组件经常发现还要接着测下一个,就没收回去),真实的"现在到底常驻哪些
组件"以 `ls apps/definitions/`(常驻)和 `ls environments/cloud-full/
pending-definitions/`(park 着)的当前输出为准,不要相信这张表或其他文档
里任何写死的组件名单——2026-08-14 文档审计就发现过 Spark Operator/Airflow
在这张表标着"cloud-full 才有",但实际已经在 `apps/definitions/` 常驻好几天,
文档没跟上。

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
| 数据工程 | Flink | 实时计算 / CDC | 重 | — | — | ✅ | Phase 4(2026-08-15 用户确认为必要组件,不是"按需"可选项,见 `docs/BACKLOG.md`) |
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

## 路线图

| Phase | 目标 | 退出标准 |
|---|---|---|
| 0 | 平台底座 | ✅ 改一个 values 文件、push,ArgoCD 自动同步——2026-08-13 用真正的推倒重建验证过这句话不是空话,不只是文档(ADR-039)。✅ 企业级权限管理:组织架构/角色同步进 Keycloak,按 group 分权限,自助申请门户,已验证(ADR-028/031/032)。✅ 安全与可靠性补强:NetworkPolicy 推广到核心命名空间(ADR-035)、Postgres 每日备份 + 恢复演练验证过(ADR-033)、Alertmanager 打开(ADR-034)、队列资源管理(ResourceQuota/LimitRange/PriorityClass,ADR-041)。可插拔外部基础设施起步(ADR-030) |
| 1 | 湖仓核心(local-lite) | ✅ 建一张 Iceberg 表、写入,Trino 读出、Superset 出图(2026-08-10,`scripts/08-create-demo-data.sh`);✅ Spark 通过 Spark Operator 真实读写同一张表(ADR-036);✅ 共享 Postgres 迁移到 CloudNativePG operator 管理,老实例已下线(ADR-038) |
| 2 | 数据工程(转 cloud-full) | ✅ SeaTunnel → Iceberg → Airflow 调度 → Superset 看板端到端跑通(2026-08-12/13 验证,见 ADR-037)。✅ Kafka(Strimzi KRaft 单节点)已验证部署,真实生产/消费一条消息跑通(2026-08-13)。Spark 权限/可观测性配置已就绪(ADR-029:History Server + oauth2-proxy SSO,Grafana 指标暴露) |
| 3 | AI/ML | ✅ 核心链路已验证(2026-08-11,见 ADR-025/026/027):JupyterHub/Argo Workflows/MLflow 接了 Keycloak SSO,模型训练 → MLflow 注册 → KServe(Standard 模式)部署成 InferenceService,V2 协议推理请求验证通过(`scripts/09-train-demo-model.sh` + `scripts/11-deploy-demo-inference-service.sh`)。算法/模型 A-B 实验用 KServe 原生的 canary 流量切分这条还没做(不是单独部署一套产品分析工具,见下面"还没定的事"里 2026-08-11 那条) |
| 3.5 | AI 闭环验证 | ✅ Feast 打通离线(Spark 读 Iceberg)/在线(Redis)特征,`feast materialize` 接入 Airflow DAG 定时物化(ADR-042)。"训练模型接入在线特征做推理"这一步是否也验证了见 ADR-042"后果"部分和对应 commit,如果没做仍是待办 |
| 4 | 企业化增强(prod) | Harbor + 遗留集群正式联邦对接,可作为旧平台替代方案上生产。Trino 细粒度数据权限用 OPA 而不是 Ranger——Ranger 官方(Apache 项目本身)没有维护 Helm chart,不满足这个项目"只用官方支持的部署方式"的门槛;OPA 本身也没有官方 chart(ADR-028 曾经写错这条,已更正),但 Trino 原生支持 OPA 授权插件(官方文档),原生支持行过滤/列脱敏,OPA 用官方镜像+原生 manifest 部署,不需要 chart。策略+数据同步机制已实现(ADR-051),**故意没有**接进 Trino 生效,见 ADR-051 |

### Phase 4 之后:从"组件都部署了"到"每个角色真的能用"

2026-08-15 用户请 Codex 对这个项目做了一轮独立评审(完整内容见
`docs/claude-improvement-recommendations-2026-08-15.md`,响应决策见
[ADR-055](decisions/055-external-review-response-2026-08-15.md)),
其中一条核心判断值得长期记在路线图里,不只是当次任务清单:**"组件已经
部署、API 已经打通、Demo 已经运行,不等于对应岗位已经获得可日常使用的
产品能力"**。按这个标准评估,当前项目是"覆盖面较广、许多链路经过真实
验证的平台原型/集成验证环境",还不是让分析师/大数据开发/算法/运维/
管理岗都觉得好用好管的生产级平台——这不是否定已完成的工作(技术选型/
组件覆盖/核心链路验证这几项评分较高),是纠正"接下来该做什么"的判断
基准:**从"再接入哪个开源组件"转为"一个角色完成一项工作还缺哪些环节"**。

评审给出五条面向角色的产品主线(完整方案见原文档,这里只记方向,不
重复展开):

- **A. 统一开发工作台**(分析师/大数据开发/算法共用):项目模型 →
  SQL/Notebook 黄金路径 → 作业模板+CI/CD → 训练黄金路径。核心原则是
  "先做薄控制面,复用成熟组件,不重新自研查询引擎/调度器"。
- **B. 数据资产与治理闭环**:权限真正执行+审计闭环 → 数据契约+质量
  规则 → 端到端血缘+变更影响分析 → 敏感字段行列级策略。
- **C. 完整 MLOps**:标准镜像+可复现训练 → 模型审批/灰度/回滚 → 推理
  可观测性 → 真实特征服务与漂移监控。
- **D. 统一运维控制面**:服务目录+黄金链路告警 → 统一 Runbook → 容量/
  成本看板 → 多节点故障/备份恢复/升级回滚演练。
- **E. 管理驾驶舱**:不新建数据源,汇总现有系统指标;第一版只回答"平台
  健不健康、谁在用、资源花在哪、数据资产覆盖率、权限风险、模型健康度"
  这几个真实问题。

**排序建议**(评审原文,已认可):可靠底座(当前 P0,尤其是成本门禁/
破坏性操作防护/权限真实执行)→ 统一项目模型 → 分析师黄金路径 → 大数据
开发黄金路径 → 算法黄金路径 → 运维控制面+管理驾驶舱(从前三条产生的
真实指标构建,不先造空看板)→ 新引擎(只有现有引擎量化验证撑不住时才
评估 ClickHouse 这类新增)。**这五条现在都还没有开始实现**,不要在还没
真正启动某一条时误以为"已经在做"。

**2026-08-15 Codex 补的一轮架构细化(核心结论:不反对上面五条,是给
"怎么落地"补细节)**——核心判断是**"这些开源组件不应该整套部署后拼起来,
而应该按职责选用,平台自己的门户/权限/项目模型才是统一入口和控制面",
否则容易从"缺功能"变成"部署了五六个平台,每个平台一套用户/权限/菜单/
运维体系"**。逐条判断,大部分是给已经决定的方向补执行细节,不是新方向,
只有一条(Stackable)是真正新提出、还没评估过的:

- **OpenMetadata 定位不变**(已经在用,不用换),但建议不要让 Spark/
  Flink/Trino/Airflow 等每个组件分别直连它写血缘,改成平台后端统一一个
  `metadata-sync-service` 适配层(任务事件 → 内部事件总线 → 这个 service
  → OpenMetadata API,同步失败进重试队列,不能让 OpenMetadata 暂时不可用
  拖垮 Spark 作业)。**判断:合理,而且和 [ADR-030](decisions/030-pluggable-external-infrastructure.md)
  "统一注释标出可替换点、不做重抽象层"的哲学一致**——这本身就是一个
  "统一改动点",不是新增一层重框架,值得在真正做 A/B 两条产品主线时
  采纳,不需要现在就动工。
- **MinIO/S3 存储抽象**:建议业务代码统一走 `s3a://`/标准 S3 SDK/Iceberg
  Catalog,不绑定 MinIO 专有 API,以后能换 Ceph/AWS S3/阿里云 OSS。
  **判断:这条现状已经满足**,这个项目本来就是这么做的(Hive Metastore/
  Trino/Spark 都是标准 S3A 配置连 MinIO,没有任何地方用 MinIO 专有 SDK),
  不需要额外动作,记录下来是为了确认"已经做对了",不是发现新差距。
- **Stackable(Spark/Trino/Hive 统一 Operator 平台)**:**这是真正新提出
  的候选项,这个项目目前完全没有评估过**。Codex 自己给的判断是它有明显
  的版本滞后代价(比如 Spark 4.x 支持明显晚于社区发布,当前长期支持线是
  3.5.8),CRD/Operator/镜像本身会形成中等到较高程度的平台绑定,建议
  只当独立 PoC 验证(非生产环境、对比部署/升级/故障恢复成本、通过统一
  "计算引擎适配层"调用、保留绕开 Stackable 直接用官方 Operator 的能力),
  不建议现在迁移任何现有组件。**判断:认可这个"仅 PoC、不替换"的谨慎
  态度**,列入下面的 P2 候选项,现在不做。
- **Kubeflow**:建议只评估 Notebook/训练任务/MLflow/KServe 这几块能力,
  不整套部署 Kubeflow UI 当最终产品。**判断:这一条本来就是这个项目
  已经在做的事**——路线图里 Phase 3 是 JupyterHub+MLflow+Argo
  Workflows+KServe 分别独立部署,而不是装一个 Kubeflow 全家桶,方向
  完全一致,不是新信息。
- **Backstage**:建议学习它的实体模型(System/Component/API/Resource/
  Owner)、插件架构、模板化创建能力,但不要现在部署一套 Backstage UI。
  **判断:这条是已经决定过的事**,[ADR-032](decisions/032-permission-request-app.md)的"背景"一节
  记过这个判断(指向 ADR-028"后续"部分的完整评估:Backstage 需要自己
  开发维护一个 React+TypeScript 应用才能用,不是"装了就能用"的产品,
  不满足这个项目"只用官方支持部署方式"的门槛),这次 Codex 给的结论和
  当时一致,没有推翻,不需要重新评估。

**一句话总结这轮补充**:没有改变"5 条产品主线还没开始、可靠底座优先"
这个既定判断,唯一真正的新增行动项是"Stackable 值得找时间做一次独立
PoC(不影响现有部署)",已经加进 `docs/BACKLOG.md` P2。

## 还没定的事

- **2026-08-14,补齐"可一键拉起"和"可原样上生产"这两条欠账**(见上面
  设计原则第 6 条):
  1. 把 README"从零拉起整套服务"里的 7 个手动步骤收敛成一个真正的
     单一入口脚本(比如 `./scripts/bootstrap-all.sh`),内部按顺序调用
     现有各步骤,而不是要求操作者手动照着 README 一条条敲——已经在
     ADR-039 的推倒重建测试里验证过这 7 步本身是对的,缺的是"合并成
     一键"这层封装,不是流程本身有问题。
  2. 把 ADR-004 设想的"改一个 values 文件、从 local-lite 切到
     cloud-full/prod"这个机制真正建起来——目前 `environments/cloud-full/`
     `environments/prod/` 还只是资源规划参考清单,不是能直接生效的配置。
  这两条还没有具体设计,留到真正动手时展开,不代表现在就要做。
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
- **企业内部 Prometheus/Grafana 对接**(用户明确说不急,先搁置):现有的
  kube-prometheus-stack 是 local-lite 自己独立一套,还没评估要不要 /
  怎么接到公司现有的监控体系
- **2026-08-13,企业级治理需求(表权限分级审批、血缘、建表规范、资源
  隔离、权限交接、资源回收、深度回溯等)**:2026-08-09 就讨论过、当时
  也做了"现成工具 vs 自建"的分析,但没有及时归档进仓库,一度"从规划里
  丢了",这次已经完整找回并写进 [ADR-040](decisions/040-enterprise-governance-roadmap.md)。
  结论仍然是"权限 OA 审批系统是最重的一块,建议等平台核心稳定后单独
  立项",这里只是确保这个待办不会再丢一次,不代表已经开始实现——
  其中"资源隔离"相对独立,**已经单独做完并验证过,见
  [ADR-041](decisions/041-queue-resource-management.md)**;"建表工具"
  (权限 OA 审批系统 Phase 1)也已实现,见
  [ADR-043](decisions/043-table-registration-tool.md)——只做登记(建表 +
  回写负责人/安全等级进 OpenMetadata);**"权限 OA 分级审批"(Phase 2)
  2026-08-14 已实现,见 [ADR-044](decisions/044-tiered-approval-workflow.md)
  ——按安全等级路由的多级审批链已经跑通,职级数据是虚拟占位(标准 HR
  导出表结构,等公司真实数据接入),真正的 Trino 访问拦截还没做,这次
  只做决策与留痕**。**Phase 3(可插拔审批后端 + 企微通知 + 超时升级 +
  权限交接 + 审计看板)2026-08-14 也已实现,见
  [ADR-045](decisions/045-approval-backend-notifications-escalation.md)
  ——"权限交接"这条原本在待规划清单里,已经在这轮做完,不再是待办**。
  **"资源回收"(权限到期自动回收)2026-08-15 也已实现,见
  [ADR-050](decisions/050-grant-expiry-reclamation.md)。"血缘"这条
  2026-08-15 部分实现:Trino 细粒度访问控制(OPA 策略引擎)见
  [ADR-051](decisions/051-trino-opa-access-control.md)(策略+数据同步已
  验证,故意没接进 Trino 生效);SeaTunnel 数据管道的表级血缘推送见
  [ADR-052](decisions/052-seatunnel-lineage.md)(核心 API 机制已用真实
  OpenMetadata 实例验证,完整 DAG 触发跑一遍受限于 SeaTunnel 当前 park
  状态还没测);Spark(ADR-014)的血缘仍是设计未实现。**
  **"分析师开发平台"(dbt,ADR-012)2026-08-15 也做了最小骨架,见
  [ADR-053](decisions/053-dbt-analyst-platform-mvp.md)——dbt build 在
  Trino/Iceberg 上跑的核心链路+MinIO 产物上传已本地充分验证,故意没接
  Cosmos(需要改 Airflow scheduler/dagProcessor 的 Python 运行时,这次
  没有贸然做)和 OpenMetadata dbt 摄入连接器,受限于 Trino 当前不稳定,
  没有在集群里端到端跑过一次完整 DAG。**
  "深度回溯"仍然是待规划状态。
  另外"AI 角色化 + 知识沉淀"是同一轮会话里用户额外提出的第 8 条(不属于
  08-09 那 7 条),原话和现状见 ADR-040 补充章节——"AI 角色化"这部分
  2026-08-15 部分实现,见 [ADR-048](decisions/048-ai-operator-role.md)
  (开发阶段 RBAC 身份已实测,运维阶段收紧+危险操作审批链未实现);
  "知识沉淀"仍待规划。
- ~~共享 Postgres 的 HA 迁移什么时候做~~ **已解决(2026-08-13,ADR-038/039)**:
  用户在场安排了迁移窗口,真正切换了共享实例到 CloudNativePG operator
  管理,含真实数据迁移、切流量、TLS 兼容问题修复;老实例确认稳定后已
  正式下线。（真正的多副本 HA 要等接入 cloud-full/生产、有多个节点能
  分布副本才有意义,local-lite 单节点机器上做的是"operator 管理带来的
  运维能力",不是"现在就有高可用"）
- ~~Ranger 的插拔式授权点现在要不要在 Trino/Hive 配置里提前占位~~ **已解决(2026-08-11,ADR-028)**:倾向于 OPA 不用 Ranger(Ranger 官方没有维护 Helm chart),Trino 原生支持通过 OPA 做行过滤/列脱敏,设计留到真正要做的时候展开
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
