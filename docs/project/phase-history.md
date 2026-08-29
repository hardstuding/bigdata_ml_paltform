# Phase 进度史(0-4)

> **这份是历史记录,不是衡量进度的标尺。** 衡量"我们做到哪了"请看
> [`capability-matrix.md`](capability-matrix.md) —— 它问的是"某个岗位能不能
> 独立完成一件真实工作",而不是"部署了哪些组件"。
>
> 这两者的落差正是这份表看不出来的东西:Phase 0-3 几乎全绿的时候,实际上
> 只有两个角色能真正开工。理由见
> [ADR-057](../decisions/057-architecture-review-2026-08-19.md)。
>
> 原本放在 `docs/architecture.md` 里,2026-08-29 移到这里 —— 架构文档
> 应该讲架构长什么样,不该同时兼任进度看板。

> **2026-08-19 起,衡量进度请看 [`docs/project/capability-matrix.md`](capability-matrix.md),不是下面这张
> 表**(见 [ADR-057](../decisions/057-architecture-review-2026-08-19.md))。
>
> 下面的 Phase 0-4 表格回答的是"**部署并验证了哪些组件**",作为历史记录
> 保留、也仍然准确。但它不是衡量进度的标尺——Phase 0-3 几乎全绿,而实际
> 上今天只有运维和分析师两个角色能真正开工,大数据开发和算法工程师是
> 结构性缺失。"组件部署了"和"某个岗位能干活"之间的落差,正是这张表看
> 不出来的东西,也正是 `project/capability-matrix.md` 存在的原因。

| Phase | 目标 | 退出标准 |
|---|---|---|
| 0 | 平台底座 | ✅ 改一个 values 文件、push,ArgoCD 自动同步——2026-08-13 用真正的推倒重建验证过这句话不是空话,不只是文档(ADR-039)。✅ 企业级权限管理:组织架构/角色同步进 Keycloak,按 group 分权限,自助申请门户,已验证(ADR-028/031/032)。✅ 安全与可靠性补强:NetworkPolicy 推广到核心命名空间(ADR-035)、Postgres 每日备份 + 恢复演练验证过(ADR-033)、Alertmanager 打开(ADR-034)、队列资源管理(ResourceQuota/LimitRange/PriorityClass,ADR-041)。可插拔外部基础设施起步(ADR-030) |
| 1 | 湖仓核心(local-lite) | ✅ 建一张 Iceberg 表、写入,Trino 读出、Superset 出图(2026-08-10,`scripts/08-create-demo-data.sh`);✅ Spark 通过 Spark Operator 真实读写同一张表(ADR-036);✅ 共享 Postgres 迁移到 CloudNativePG operator 管理,老实例已下线(ADR-038) |
| 2 | 数据工程(转 cloud-full) | ✅ SeaTunnel → Iceberg → Airflow 调度 → Superset 看板端到端跑通(2026-08-12/13 验证,见 ADR-037)。✅ Kafka(Strimzi KRaft 单节点)已验证部署,真实生产/消费一条消息跑通(2026-08-13)。Spark 权限/可观测性配置已就绪(ADR-029:History Server + oauth2-proxy SSO,Grafana 指标暴露) |
| 3 | AI/ML | ✅ 核心链路已验证(2026-08-11,见 ADR-025/026/027):JupyterHub/Argo Workflows/MLflow 接了 Keycloak SSO,模型训练 → MLflow 注册 → KServe(Standard 模式)部署成 InferenceService,V2 协议推理请求验证通过(`scripts/09-train-demo-model.sh` + `scripts/11-deploy-demo-inference-service.sh`)。上线前有审批门禁、出事能回滚(ADR-080,实测:未批准的版本会被拒、回滚真的换掉了线上服务的模型产物)。**canary 流量切分这条不是"还没做",是这套部署形态下做不了**:KServe 用 `deploymentMode: Standard`(RawDeployment,不装 Knative),`canaryTrafficPercent` 会被 API 接受但完全不生效——实测确认后改成显式拒绝这个参数,不留假开关(ADR-080「灰度」一节) |
| 3.5 | AI 闭环验证 | ✅ Feast 打通离线(Spark 读 Iceberg)/在线(Redis)特征,`feast materialize` 接入 Airflow DAG 定时物化(ADR-042)。"训练模型接入在线特征做推理"这一步是否也验证了见 ADR-042"后果"部分和对应 commit,如果没做仍是待办 |
| 4 | 企业化增强(prod) | Harbor + 遗留集群正式联邦对接,可作为旧平台替代方案上生产。Trino 细粒度数据权限用 OPA 而不是 Ranger——Ranger 官方(Apache 项目本身)没有维护 Helm chart,不满足这个项目"只用官方支持的部署方式"的门槛;OPA 本身也没有官方 chart(ADR-028 曾经写错这条,已更正),但 Trino 原生支持 OPA 授权插件(官方文档),原生支持行过滤/列脱敏,OPA 用官方镜像+原生 manifest 部署,不需要 chart。策略+数据同步机制已实现(ADR-051),**故意没有**接进 Trino 生效,见 ADR-051 |

### Phase 4 之后:从"组件都部署了"到"每个角色真的能用"

2026-08-15 用户请 Codex 对这个项目做了一轮独立评审(完整内容见
`reviews/2026-08-15-external-review.md`,响应决策见
[ADR-055](../decisions/055-external-review-response-2026-08-15.md)),
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
  拖垮 Spark 作业)。**判断:合理,而且和 [ADR-030](../decisions/030-pluggable-external-infrastructure.md)
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
  **判断:这条是已经决定过的事**,[ADR-032](../decisions/032-permission-request-app.md)的"背景"一节
  记过这个判断(指向 ADR-028"后续"部分的完整评估:Backstage 需要自己
  开发维护一个 React+TypeScript 应用才能用,不是"装了就能用"的产品,
  不满足这个项目"只用官方支持部署方式"的门槛),这次 Codex 给的结论和
  当时一致,没有推翻,不需要重新评估。

**一句话总结这轮补充**:没有改变"5 条产品主线还没开始、可靠底座优先"
这个既定判断,唯一真正的新增行动项是"Stackable 值得找时间做一次独立
PoC(不影响现有部署)",已经加进 `roadmap.md` P2。
