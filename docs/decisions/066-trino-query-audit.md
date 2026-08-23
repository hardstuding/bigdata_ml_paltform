# ADR-066:Trino 查询审计走 Kafka event listener

日期:2026-08-23
状态:**第一段(开始留痕)已实机验证**;第二段(持久化到 Iceberg)未做

## 背景:这条缺口的性质和别的不一样

[`docs/production-readiness-gaps.md`](../production-readiness-gaps.md) 第 3 条:
权限侧有 OPA 做**准入**判断(能不能查),但查询本身**没有留痕**。

这一条**做不到事后补齐**。别的缺口晚做几周,补上就行;审计没记就是没记,
现在晚装一天 event listener 就永久少一天记录。而涉及个人信息的数据平台,
合规上要能回答"谁导出过这张表"、"这个离职的人走之前碰过什么"——这些问题
提出来的时候,需要的恰恰是**过去**的记录。

## 决策

`event-listener.name=kafka`,Trino 把查询完成事件发到 Kafka topic
`trino-query-events`,下游再落到 Iceberg 长期留存。

**为什么先落 Kafka 而不是直接写库**:Trino 内置的事件监听器只有
`http` / `kafka` / `mysql` 三种,写 Iceberg 没有现成的。而这个平台已经跑通
Kafka → Flink → Iceberg([ADR-062](062-flink-streaming-pipeline.md)),
复用它比自己写一个 HTTP 接收端再自己管持久化少一个自研组件。

## 三个有意识的取舍

### 1. 只发 completed,不发 created(`publish-created-event=false`)

created 事件让消息量翻倍,而它能回答的问题 completed 事件全都包含——
**唯一例外是"查询发起后 Trino 崩了,没有 completed 事件"**。不为这个边缘
场景付双倍存储;真要追这种场景再打开。

### 2. Kafka 挂了不能让 Trino 起不来(`terminate-on-initialization-failure=false`)

这个参数**默认是 true**,必须显式关掉。不关的话,Kafka 连不上时 Trino 直接
启动失败——**拿整个平台的查询能力去换一个合规需求,不划算**。

代价必须写清楚:**Kafka 挂掉期间的查询不会有审计记录,而且是静默的**。
所以这条配套一个待办:要有"审计事件流断了"的告警。只靠配置文件里的一行
注释是不够的,注释不会在半夜叫醒任何人。

### 3. 排掉性能剖析字段(`excluded-fields=payload,plan,jsonPlan,statistics`)

**实测:不排的话,一个 `select count(*)` 的审计事件 125KB。** 拆开看:
`payload`(完整执行计划 JSON)76KB、`operatorSummaries` 26KB、
`taskStatistics` 9KB、`jsonPlan`/`plan` 各约 2.8KB——**全是性能剖析数据,
不是审计数据**。

审计要回答的是"谁、什么时候、查了哪张表、成功没有",这些在
`metadata` / `context` / `ioMetadata` 里,加起来不到 1KB。排掉之后实测
2125 字节,**小了 58 倍**。想看查询性能有 Trino 自己的 Web UI,不该由审计
流兼职。

## 验证证据(cloud-full,2026-08-23)

`/etc/trino/event-listener.properties` 已生效,coordinator 正常 Running
(配置写错会导致它起不来,所以"还活着"本身就是配置合法的证据),跑两条
真实查询后从 Kafka 直接消费:

```
   2125B | table_registration_service | FINISHED | ['iceberg.demo.orders'] | select region, count(*) from ...
 123162B | table_registration_service | FINISHED | ['iceberg.demo.orders'] | select count(*) from ...
```

(第二条是加 `excluded-fields` 之前那条,留在这里正好是前后对比。)

审计需要的字段全在:`context.user` / `context.remoteClientAddress` /
`context.source`、`metadata.query`(完整 SQL)、`metadata.queryState`、
`metadata.tables`(catalog.schema.table 逐张列出)。

## 还没做的(按优先级)

1. **持久化到 Iceberg**。现在只在 Kafka 里,保留期 cloud-full 30 天 /
   prod 90 天(`environments/resource-profiles.yaml` 的
   `audit_topic_retention_ms`)。超过保留期就滚掉了。做法是照
   [ADR-062](062-flink-streaming-pipeline.md) 那条链路再来一遍,写进
   `iceberg.audit.query_events`,然后**这张表本身要按最严格的权限管起来**
   ——审计表泄露比业务表泄露更糟。
2. **"审计流断了"的告警**。见上面取舍 2,这是那个取舍的必要配套,不是
   可选项。
3. 落表之后顺带能回答"哪些表其实没人用",正是
   [`docs/BACKLOG.md`](../BACKLOG.md) P5 项目瘦身审计缺的数据。
