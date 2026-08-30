# ADR-066:Trino 查询审计走 Kafka event listener

日期:2026-08-23
状态:**已实机验证**(2026-08-24):Trino → Kafka → Flink → Iceberg 全链路。`audit.query_events` 一次查询一行、`audit.query_table_access` 一次表访问一行,实测 3 条查询 → 5 行事件 / 4 行表访问。2026-08-30 补了 `audit` 黄金链路探针(待实机验证)

## 背景:这条缺口的性质和别的不一样

[`docs/project/production-readiness-gaps.md`](../project/production-readiness-gaps.md) 第 3 条:
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

## 第二段:持久化到 Iceberg(2026-08-23 加,未部署验证)

`apps/flink-audit-sink/` —— Kafka → Flink → Iceberg,照
[ADR-062](062-flink-streaming-pipeline.md) 那条已经跑通两轮的链路做,
不重新发明。三个设计点值得单独说:

### 写成两张表,不是一张

一次查询可以访问多张表,事件里的 `tables` 是个数组。展开成两张表而不是
在 Iceberg 里存 `ARRAY<ROW<...>>`:

- `audit.query_events` —— **一次查询一行**,回答"谁在什么时候跑了什么 SQL"
- `audit.query_table_access` —— **一次查询访问的每张表各一行**,回答
  **"谁查过这张表"**

第二张表存在的全部理由是:那才是合规场景真正要问的问题,展开成行之后
它就是一句最普通的 `WHERE table_name = '...'`,不需要数组函数,也不依赖
Iceberg 对嵌套类型的支持程度。

### 时间用 Kafka 的记录时间戳,不解析 payload 里的

payload 自带 `createTime`/`endTime`,但**它们的字符串格式没在真机上核对
过**。这个平台在时间格式上栽过一次(ADR-062:格式对不上会静默变 null,
然后在两个算子之后以完全不相干的报错炸出来)。所以排序用 Kafka 记录
自带的时间戳元数据(由 Kafka 写入,不依赖任何解析),payload 那两个原样
存成字符串,等真机确认格式后再决定要不要转。

### `upgradeMode: stateless` 是一笔明确的技术债

stateless 升级 = 丢掉消费位点重启,配合 `earliest-offset` 会**从头重放
整个 topic**,产生重复记录。仍然选它,是因为 savepoint 模式要配持久化
目录和恢复流程(另一件要单独验证的事),而**重复记录对审计的危害远小于
漏记录**——漏了永远补不回来,重了至少能按 `query_id` 去重。查审计表本来
就该按 query_id 去重。

## 审计表的权限:补了一条 OPA 规则

原来的策略里 `is_service_account` 是**无条件放行**,意味着任何人只要能在
Superset 里建一个查询,就能借 `superset_service` 这个账号读到全部审计
记录。**审计表记着每个人查过什么、导出过什么,是一份"谁对什么感兴趣"的
完整画像**,不能沿用普通表那套。

这不是这次新引入的问题,是"服务账号无差别放行"这个既有设计撞上一张新的
敏感表——但撞上了就得处理。加的规则按 **schema 名**判断(`audit`),不
枚举表名:以后往里加新的审计表,不用记得回来改策略。`opa test` 33/33 通过,
其中 5 条是这次新加的,专门锁住"服务账号那条口子对审计表不生效"。

## 还没做的(按优先级)

1. **上面这一段没有部署验证过。** Flink SQL 里的嵌套 ROW 取值、
   `CROSS JOIN UNNEST`、`METADATA FROM 'timestamp'` 都是照文档写的,
   跑没跑得起来是另一回事。
2. **"审计流断了"的告警**。见上面取舍 2,这是那个取舍的必要配套,不是
   可选项。
3. 落表之后顺带能回答"哪些表其实没人用",正是
   [`docs/project/roadmap.md`](../project/roadmap.md) P5 项目瘦身审计缺的数据。
