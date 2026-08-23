# ADR-068:Schema Registry 选 Karapace

日期:2026-08-23
状态:manifest 完成,**未部署验证**(要等下次开机)

## 背景

[`docs/production-readiness-gaps.md`](../production-readiness-gaps.md) 第 5 条:
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

## 还没做的

1. **没部署验证过。** manifest 里的 entrypoint(`python3 -m karapace`)和
   环境变量名是从上游 `container/compose.yml` 的 6.2.2 tag 抄的,不是猜的,
   但"抄对了"和"跑起来了"是两回事。
2. **现有的 Flink 作业还没接**。装上 registry 只是有了地方放 schema,
   真正消掉"上游改字段下游炸"这个风险,要把
   `apps/flink-streaming-demo/` 的 SQL 从写死 schema 改成从 registry 拿。
   这是下一步,而且**要有一次真实的"改上游字段看下游是否被拦住"的验证**
   ——否则这个组件就只是装着好看。
3. local-lite 不启用(那一档连 Kafka 都没开)。
