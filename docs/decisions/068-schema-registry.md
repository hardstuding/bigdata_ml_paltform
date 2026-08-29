# ADR-068:Schema Registry 选 Karapace

日期:2026-08-23
状态:manifest 完成,**未部署验证**(要等下次开机)

## 背景

[`docs/project/production-readiness-gaps.md`](../project/production-readiness-gaps.md) 第 5 条:
Kafka 部署了,但没有 Schema Registry,Flink 作业里的 schema **写死在 SQL
里**。上游改一个字段名或类型,消费端只有运行时才知道——而且这个平台实测过,
**报错位置往往离根因隔着一两层**([ADR-062](062-flink-streaming-pipeline.md)
里 `ignore-parse-errors` 那个坑:解析失败静默变 null,两个算子之后才以完全
不相干的报错炸出来)。

## 决策:Karapace 6.2.2

| 候选 | 授权 | 部署形态 | 结论 |
|---|---|---|---|
| Confluent Schema Registry | **Confluent Community License** | 官方镜像 | ❌ 不是 OSI 开源。**这个项目存在的理由之一就是躲开 CDH 那类授权陷阱**,不能在核心链路上再引入一个 |
| Apicurio Registry 3.3.1 | Apache 2.0 | operator + CRD | 🟡 授权干净、Red Hat 背书,但要为一个单进程服务装一套 operator + CRD,**不成比例**;而且这个仓库已经在 CRD 太大这件事上栽过四次 |
| **Karapace 6.2.2** | Apache 2.0 | 一个 Deployment | ✅ 选它 |

Karapace 胜出的三条:

1. **原生兼容 Confluent 的 wire protocol**,Flink 的 `avro-confluent` 格式、
   Spark 的 schema registry 集成都不用改任何东西——这一点很关键,因为
   "换个 registry 要改所有作业"会直接让这件事推不动。
2. **没有额外的存储依赖**:状态全在 Kafka 的 `_schemas` topic 里,不需要
   再挂一个 Postgres。
3. Apache 2.0,Aiven 维护,而且是他们托管 Kafka 服务在用的东西——不是一个
   没人真的跑在生产上的项目。

## 三个值得单独说的配置

### `KARAPACE_COMPATIBILITY=BACKWARD`(不是上游默认的 FULL)

**这是这个组件最重要的一个参数**,它决定什么样的 schema 变更会被拒绝:

- `BACKWARD`:新版本的消费者能读老数据(加可选字段、删字段都 OK)
- `FULL`:双向都要兼容,连"加一个可选字段"都要两边同时成立

数据平台的现实是**消费端先升级、生产端后升级**,`BACKWARD` 正是为这个
顺序设计的。一上来就 `FULL` 会把大量正常演进挡在门外,结果是人绕过
registry 直接改 —— **一个严格到被绕过的校验,等于没有校验。**

### 没有 Ingress

消费者全部在集群内(Flink / Spark / 生产端)。**谁能往 registry 里写
schema,谁就能决定下游怎么解析数据**,对外开一个没有认证的写接口没有任何
好处。人工查看 schema 用 `kubectl port-forward`。

### `_schemas` topic 的 partitions=1 + cleanup.policy=compact

这两个不是可调项,所以**没有做成 `{{RES:...}}`**:它是一个要顺序重放的
日志,多分区会让 schema 的先后顺序失去意义;不 compact 的话老版本 schema
会被保留期删掉,注册表就残缺了。

### 副本数

cloud-full 单副本。理由不是"省资源",是**它不在查询链路上**:registry 短暂
不可用只影响新 schema 注册,不影响已有作业读写数据,而它无状态、重启几秒
就从 `_schemas` 恢复。prod 给 2 副本,也不是为了扛量(量很小),是为了滚动
升级和节点故障时注册接口不中断——Karapace 自己有 master 选举,多副本时
只有 master 负责写,这是它设计支持的形态。

## 实机验证(cloud-full,2026-08-23)

Karapace 起来之后直接打它的 REST API:

| 动作 | 结果 |
|---|---|
| 注册 v1 schema | `200 {"id": 1}` |
| 加一个带默认值的可选字段 | `200 {"id": 2}` —— BACKWARD 下这是合法演进 |
| 把 `double` 改成 `string` | **`409 Incompatible schema, compatibility_mode=BACKWARD`** |

最后一行就是这个组件存在的全部理由:**"上游改字段"现在拦得住了**。

## 第二段:让流式链路真的用上它(2026-08-23,未验证)

装了 registry 只是有了放 schema 的地方。上面那条 409 是我用 curl 手动触发
的——**如果真实的 producer 不走 registry,它就只是一个没人用的服务**。

所以把 `apps/kafka-producer/` 从 JSON 换成了 Avro:用 confluent-kafka 的
`AvroSerializer`,schema 在第一次发送时注册到 `<topic>-value`,不兼容就
409、消息根本发不出去——**把问题挡在发送这一侧**,而不是等下游解析炸。
Flink 侧对应改成 `'format' = 'avro-confluent'`。

### 顺带消掉一整类 bug

时间字段从字符串变成 Avro 的 `timestamp-millis`(一个 long),**不再有
"格式"这个概念**。2026-08-22 那次"格式差一个 `T` 就静默变 null、两个算子
之后才以 `RowTime field should not be null` 炸出来"的坑
([ADR-062](062-flink-streaming-pipeline.md)),从设计上不存在了。

这不是附带好处,值得单独说:**引入 schema 之后,一整类"约定靠人记住"的
bug 变成了"类型系统保证"**,这比多一道校验更有价值。

### topic 改名,不是原地换格式

`device-events` → `device-events-avro`。同一个 topic 里混着 JSON 和 Avro,
任何从 `earliest-offset` 重放的消费端读到老消息都会炸——而这条 Flink 作业
正是从 earliest 消费的。改名是最省事也最不会出错的迁移方式。

### 三个配套改动(都写了"少了会怎样")

- kafka-producer 镜像:`confluent-kafka[avro]`,不加 extra 直接 ImportError
- flink-iceberg 镜像:`flink-sql-avro-confluent-registry-1.20.5.jar`,少了会
  报 `Could not find any format factory for identifier 'avro-confluent'`
- CronJob 加 `SCHEMA_REGISTRY_URL`

## 还没做的

1. **第二段没有验证过。** 下次开机要验三件事:producer 发得出去、Flink
   消费得到、Iceberg 行数增长;以及**故意改一个不兼容的字段类型,确认它在
   producer 侧就被拦住**——最后这条才是这次改动的意义所在,不做等于没做。
2. **审计链路的 schema 没进 registry。** Trino 的 event listener 只发 JSON,
   格式由 Trino 决定,我们改不了。这是可以接受的:那条链路的"上游"是
   Trino 自己,不是会随手改字段的业务方。
3. local-lite 不启用(那一档连 Kafka 都没开)。
