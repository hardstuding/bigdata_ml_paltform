# 未决问题流水账

> 原本放在 `docs/architecture.md` 的「还没定的事」一节,2026-08-29 移到这里。
> 架构文档应该讲**架构现在长什么样**;一份大半已经划掉的待决清单属于项目
> 过程记录,混在一起会让读者要先跳过十条历史才看到有用的东西。
>
> 仍然未决的那几条已经提炼进 [`../architecture.md`](../architecture.md)
> 的「未决的架构问题」;这里保留完整原文,包括**已经解决的那些和当时是
> 怎么决的** —— 那部分的价值在于"为什么当时那样选",和 ADR 是互补的。

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
  丢了",这次已经完整找回并写进 [ADR-040](../decisions/040-enterprise-governance-roadmap.md)。
  结论仍然是"权限 OA 审批系统是最重的一块,建议等平台核心稳定后单独
  立项",这里只是确保这个待办不会再丢一次,不代表已经开始实现——
  其中"资源隔离"相对独立,**已经单独做完并验证过,见
  [ADR-041](../decisions/041-queue-resource-management.md)**;"建表工具"
  (权限 OA 审批系统 Phase 1)也已实现,见
  [ADR-043](../decisions/043-table-registration-tool.md)——只做登记(建表 +
  回写负责人/安全等级进 OpenMetadata);**"权限 OA 分级审批"(Phase 2)
  2026-08-14 已实现,见 [ADR-044](../decisions/044-tiered-approval-workflow.md)
  ——按安全等级路由的多级审批链已经跑通,职级数据是虚拟占位(标准 HR
  导出表结构,等公司真实数据接入),真正的 Trino 访问拦截还没做,这次
  只做决策与留痕**。**Phase 3(可插拔审批后端 + 企微通知 + 超时升级 +
  权限交接 + 审计看板)2026-08-14 也已实现,见
  [ADR-045](../decisions/045-approval-backend-notifications-escalation.md)
  ——"权限交接"这条原本在待规划清单里,已经在这轮做完,不再是待办**。
  **"资源回收"(权限到期自动回收)2026-08-15 也已实现,见
  [ADR-050](../decisions/050-grant-expiry-reclamation.md)。"血缘"这条
  2026-08-15 部分实现:Trino 细粒度访问控制(OPA 策略引擎)见
  [ADR-051](../decisions/051-trino-opa-access-control.md)(策略+数据同步已
  验证,故意没接进 Trino 生效);SeaTunnel 数据管道的表级血缘推送见
  [ADR-052](../decisions/052-seatunnel-lineage.md)(核心 API 机制已用真实
  OpenMetadata 实例验证,完整 DAG 触发跑一遍受限于 SeaTunnel 当前 park
  状态还没测);Spark(ADR-014)的血缘仍是设计未实现。**
  **"分析师开发平台"(dbt,ADR-012)2026-08-15 也做了最小骨架,见
  [ADR-053](../decisions/053-dbt-analyst-platform-mvp.md)——dbt build 在
  Trino/Iceberg 上跑的核心链路+MinIO 产物上传已本地充分验证,故意没接
  Cosmos(需要改 Airflow scheduler/dagProcessor 的 Python 运行时,这次
  没有贸然做)和 OpenMetadata dbt 摄入连接器,受限于 Trino 当前不稳定,
  没有在集群里端到端跑过一次完整 DAG。**
  "深度回溯"仍然是待规划状态。
  另外"AI 角色化 + 知识沉淀"是同一轮会话里用户额外提出的第 8 条(不属于
  08-09 那 7 条),原话和现状见 ADR-040 补充章节——"AI 角色化"这部分
  2026-08-15 部分实现,见 [ADR-048](../decisions/048-ai-operator-role.md)
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
