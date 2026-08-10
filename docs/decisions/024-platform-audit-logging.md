# 024. 平台审计日志:复用 Loki,不新增平台

- 状态: 已采纳(2026-08-10,已验证:Keycloak 登录/管理员事件、Trino 查询时间线均已确认流入 Loki)

## 背景

调研 PostHog 之后发现它已经不支持 k8s 部署,而且本质是消费端产品分析
工具,和这次实际要的"平台审计日志"(谁查了哪张表、谁登录过、管理员改了
什么配置)不是一回事,见 `docs/architecture.md` "还没定的事"里 2026-08-11
那条的完整调研记录。

## 决策

不新增任何平台,复用已经在跑的 Loki + Grafana(见 ADR-020):

- **Keycloak**:chart 自带 `jboss-logging` 事件监听器,默认关闭。
  `scripts/03-configure-keycloak.sh` 给 `platform` realm 开
  `eventsEnabled` + `adminEventsEnabled` + `adminEventsDetailsEnabled`
  (谁登录、谁登出、管理员改了哪个 client/realm 配置,细节都记)。
  `platform/apps/keycloak.yaml` 加两个环境变量控制日志级别
  (`KC_SPI_EVENTS_LISTENER_JBOSS_LOGGING_*_LEVEL`)。事件本身走 Keycloak
  自己的应用日志,Quarkus 发行版默认输出到 stdout,Alloy 已经在采集所有
  pod 的 stdout,不需要额外接收端或者新组件。
- **Trino**:`io.trino.event.QueryMonitor` 默认在 INFO 级别把每条查询的
  完成状态和耗时(TIMELINE 那一行)打到 stdout,同样不需要任何额外配置,
  已确认流入 Loki。

## 明确没做什么,以及为什么

**Trino 查询审计目前只有"查询完成时间线",没有"具体是哪条 SQL、查了哪张
表"**。Trino 官方支持更完整的审计(`http-event-listener` 插件,官方镜像
自带,不是第三方插件),但它只能把结构化的 `QueryCompletedEvent` JSON
通过 HTTP POST 发给一个外部服务,不能直接写 stdout/文件,也不支持直接对接
Loki 的 push API(Loki push API 要求特定的 `{"streams": [...]}` 格式,和
Trino 发出去的 JSON schema 对不上,中间需要一个转换层)。

评估过后没做,原因是这需要新建一个"接收 HTTP POST、转换格式、转发给
Loki"的小服务——本质上是新增一个组件,和这次"不新增平台、复用现有基础
设施"的决策方向矛盾。如果以后真的需要完整的 SQL 级别审计(比如合规要求
"谁在什么时候查过这张表"),再评估要不要为这一个目的单独建这个转发服务,
或者看 Loki/Alloy 生态有没有更新的官方组件能直接接收任意 JSON webhook
(这次没找到,不代表以后没有)。

## 后果

- Grafana 上的"平台审计"看板(如果建)能回答"谁登录过 Keycloak、什么时候、
  从哪个 IP""管理员改过什么配置""Trino 查询量/耗时趋势",但回答不了
  "谁具体查过哪张表的哪些字段"——后者目前只能靠 OpenMetadata 的 Lineage/
  Activity Feed(如果启用了对应数据源的采集)或者去 Hive Metastore 的
  访问记录里找,不是这次审计日志方案覆盖的范围。
- 这套方案完全依赖 Loki 的日志保留期——local-lite 阶段 Loki 没配置真正的
  retention 策略(见 ADR-020),审计日志同样没有长期保留保证,cloud-full/
  prod 阶段如果审计日志有合规保留期要求(比如"至少保留 90 天"),需要
  单独规划 Loki 的存储和 retention 配置,不能假设现在这套配置直接够用。
