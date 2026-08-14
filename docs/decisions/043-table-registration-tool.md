# 043. 建表注册工具(权限 OA 审批系统 Phase 1)

- 状态: 已采纳,已部署到集群。Trino 建表路径已真实验证;OpenMetadata 回写
  路径按当前(1.13.3)真实 API 契约实现,但需要管理员手动创建 bot token
  才能跑通,这次没有验证到那一步(2026-08-14)

## 背景

[ADR-040](040-enterprise-governance-roadmap.md) 归档的 7 条企业治理需求里,
需求 1"建表工具"当时的判断是**半现成**——OpenMetadata 本身有 Owner 和
Tag/Classification 字段能存"负责人"和"安全等级"这类治理元数据,但"建表时
强制填这些信息"这个流程约束没有现成工具会管,需要一个薄的自建入口。

`e95fec7` 这次提交把 OpenMetadata/OpenSearch 从"验证完就 park 回去"改成了
长期依赖,明确是为了配合这个建表注册工具——这份 ADR 就是接着那个方向的
具体实现。

## 决策

### 范围:只做登记,不做审批

这次只实现"提交建表请求 -> 通过 Trino 真实执行 DDL -> 把负责人/安全等级
回写进 OpenMetadata"这一条链路。ADR-040 需求 2(权限 OA 分级审批链路)还
没有实现,这次不做——安全等级(1/2/3)现在只是在建表时强制填、存下来,
给以后真正的审批链路留数据基础,**这次不拦截任何人提交**,任何登录用户
都能建表(和 ADR-032 的"权限申请门户任何人都能提交"是同一个设计取向)。

### 独立组件,不塞进 permission-request-app

评估过在 `apps/permission-request-app/` 里加一个新页面/路由,但两者是不同
的治理动作(权限申请 vs 建表治理),依赖也不同(这个要连 Trino +
OpenMetadata,那个要连 git),按架构原则 3"组件独立可升级,禁止焊在一起"
(architecture.md),新建 `apps/table-registration-app/` 更合适。技术栈和
部署模式完全照抄 permission-request-app(ADR-032):单文件 Flask + ConfigMap
挂源码 + `python:3.12-slim` 启动时 `pip install`,不建容器镜像仓库;
oauth2-proxy 挡在前面做 Keycloak SSO,只取 `X-Forwarded-User` 当默认负责人
(这次不需要解 groups claim,没有审批门槛,比 permission-request-app 的身份
处理更简单)。

### Trino 服务账号:新建一个,不复用 superset_service

按 [ADR-021](021-trino-service-account-auth.md) 的原则(各组件各自独立
账号,方便追溯/吊销),新增 `table_registration_service` 这个 Trino 服务
账号。实现方式上有个技术细节:Trino 的 file password authenticator 一次
只认一个密码文件,`trino-service-account` 这个 Secret 的 `password.db` 是
所有服务账号共享的一份 htpasswd 风格文件——`scripts/00-generate-secrets.sh`
里原来"存在就跳过、不存在就创建"的逻辑改成了
`ensure_trino_service_account()`,用 `kubectl patch --type merge` 只追加/
更新 `password.db` 和这个新用户专属的 `password-<username>` 这两个 key,
不碰 Secret 里已有的 `username`/`password` 顶层字段(那两个是最早创建时
写的,被 Superset 直接消费,不能被后来新增的账号覆盖掉)。

### OpenMetadata 回写:直接建实体层级,不等自动采集

OpenMetadata 支持两种方式知道一张新表的存在:配置一个 Trino 数据源的定期
采集任务(ingestion pipeline)让它自己发现,或者直接通过 API
createOrUpdate(`PUT`)。选了后者——不想让"负责人/安全等级写进去"这件事
依赖一个采集任务的调度周期,建表这一刻就同步写完更符合"强制在建表时录入
治理信息"这个需求本身。需要依次 upsert 整条实体链路(`DatabaseService
trino` -> `Database iceberg` -> `DatabaseSchema <schema>` -> `Table
<table>`),因为当前没有配置任何 OpenMetadata 采集任务,这条链路在
OpenMetadata 目录里之前完全不存在,不能只 PUT 最底层的 Table。

安全等级用 OpenMetadata 原生的 Classification/Tag 机制(`SecurityLevel`
classification,`Level1`/`Level2`/`Level3` 三个 tag),不是塞进某个自由文本
字段——这是 ADR-040 分析里明确提到的"现成能力",直接用。负责人尝试解析成
OpenMetadata 的 `owners`(`EntityReference` 列表,2026-08-14 实测 1.13.3 的
`CreateTable` schema 这个字段是**复数** `owners`,不是旧版本文档里常见的
单数 `owner`,通过 `GET /swagger.json` 查真实 API 契约确认的,没有凭记忆
猜)——如果这个用户从没登录过 OpenMetadata UI(OM 侧还没有这个用户的
记录),就找不到对应的 `EntityReference`,这种情况下降级成把负责人/安全
等级写进 `extension`(表实体上的自由 JSON 字段),保证信息不丢,只是没有
挂成真正的 Owner 关联,页面上会明确提示这个降级状态。

### OPENMETADATA_TOKEN:和 GIT_TOKEN 同一类"手动建的敏感凭据"

OpenMetadata 这次配的是 Keycloak SSO(custom-oidc,confidential
client),没有开本地 basic-auth 登录,API 调用需要一个 bot 的 JWT
token——这个 token 要管理员在 OpenMetadata UI 里手动建
(Settings -> Bots -> Add Bot -> 生成 token)才能拿到,不是任何脚本能自动
生成的,和 `permission-request-app` 的 `GIT_TOKEN`(ADR-032)是同一类考虑。
没配这个 Secret 之前,建表这条路径(Trino DDL)完全不受影响,只是
OpenMetadata 回写这一步会跳过,页面上明确提示"需要管理员配置"。

## 已经验证的部分

- `table_registration_service` 这个 Trino 服务账号已经在活集群里生成,
  `trino-service-account` Secret 的 `password.db` 确认同时有
  `superset_service` 和 `table_registration_service` 两行,`superset_service`
  的密码没有被改动(读取活 Secret 内容核对过)。
- 通过 `curl` 直接查了活集群里 OpenMetadata 1.13.3 实例的真实
  `swagger.json`(不是查文档/凭记忆),确认了 `CreateTable`/
  `CreateDatabaseService`/`CreateDatabase`/`CreateDatabaseSchema`/
  `EntityReference`/`TagLabel` 这几个 schema 的真实字段和必填项,代码是
  照着这份真实契约写的。

## 还没验证的部分

- **验证过程中发现 Trino coordinator 在当前集群负载下重启了几次**(几分钟内
  2 次,`Liveness probe failed`,起因看着是 JVM 启动/GC 期间探针超时,和
  这轮同时在跑的 Feast 集成抢内存有关,不是这个工具本身的 bug)——已经
  提交过的表数据是持久的(Iceberg 数据在 MinIO,Trino 本身无状态,重启后
  核对过之前建的表还在、结构没变),但重启期间那个时间窗口内的建表请求会
  失败,页面上会如实显示 `failed` 和具体报错,不会误报成功。这是当前
  colima 4vCPU/11GB 在多个组件同时活跃时的真实资源约束,不是这次新引入的
  问题,后续如果要长期同时跑这么多组件,需要重新评估资源分配或错峰验证。
- **OpenMetadata 回写这条路径没有做完整的活集群端到端验证**——需要一个
  真实的 bot JWT token,这一步是管理员在 UI 里手动操作,这次没有做(和
  ADR-032 里 `GIT_TOKEN` 没建完是同一类未完成状态)。代码逻辑是照着真实
  API 契约写的,但"提交请求后 OpenMetadata 里真的能查到这张表、Owner、
  安全等级标签"这个最终结果还没有肉眼确认过。
- Trino 真实建表这条路径的端到端验证见
  `scripts/18-table-registration-demo.sh` 的运行记录(如果这次跑成功了会在
  这里更新;如果卡在别的地方,以最终验证脚本的实际输出为准)。
- 负责人解析成 OpenMetadata `owners` 这条路径依赖"这个用户之前登录过
  OpenMetadata UI",没有专门验证"一个全新用户"和"一个已登录过的用户"两种
  情况分别的实际表现,只是代码里做了 try/except 降级,没有针对性测试两条
  分支。

## 后果

- `apps/table-registration-app/` 是一个新的常驻组件,占用集群资源不多
  (和 permission-request-app 同量级,requests 50m CPU/128Mi 内存),但在
  colima 4vCPU/11GB 这个紧张的本机环境上,和同一时间在跑的其它组件一起要
  留意总资源占用。
- 建表注册工具目前和 Iceberg/Trino 强绑定(DDL 是 Trino 语法,`WITH
  (format='ICEBERG', ...)` 之类的建表选项这次没有暴露在表单里,只建最基础
  的列结构),以后如果要支持指定 Iceberg 分区策略等选项,需要扩展表单和
  DDL 拼接逻辑。
- 列类型只支持一个白名单内的常见 Trino 类型(VARCHAR/BIGINT/DECIMAL 等),
  没有覆盖 Trino 全部类型系统,够日常建表用,复杂类型(ARRAY/MAP/ROW)这次
  没做。
- SQLite 存的是这个工具自己的登记记录(审计用),不是权威数据源——真正的
  表结构权威来源是 Trino/Iceberg 本身,治理元数据(负责人/安全等级)的
  权威来源是 OpenMetadata,这个 SQLite 丢了不是灾难性的,和 ADR-032 里
  `permission-request-app` 的 SQLite 是同一个定位。
