# 056. Flink 在这套架构里的角色——设计分析,不是部署决定

- 状态: 设计完成,**没有部署任何东西**——这是 project/roadmap.md P1.5 里"Flink
  确认必要但还没设计"这条要求回答的三个问题(角色定位、和 Kafka 怎么
  配合、什么时候引入),真正动手装 Flink 是后续独立的工作,不在这份 ADR
  的范围内。

## 背景

2026-08-15 使用方明确说过"Flink 作为新的大数据平台有它的必要性",
`docs/architecture.md` 路线图里 Flink 的定位从"Phase 4 按需"这种偏可选
的描述,改成了"确认要做,还没排期设计"。这份 ADR 就是那次"专门设计"。

## 现状盘点(先搞清楚已经有什么,不是从空白画布开始设计)

- **SeaTunnel**(`apps/seatunnel/`):已经部署,用的是自带的 **Zeta 引擎**
  (单节点 Hybrid 模式),不是跑在 Flink 上——虽然 ADR-011 当初提过
  SeaTunnel"支持跑在 Flink 上做真正的低延迟流式同步",但实际选的执行
  引擎是 Zeta,没有真的接 Flink。SeaTunnel 本身已经覆盖批量集成 + 基础
  CDC(自动建表/改表、全库同步)。
- **Kafka**(`apps/kafka/`):manifest 已经写好,但**现在完全没有真实
  下游消费者**——`apps/kafka/manifests/kafka-cluster.yaml` 自己的注释
  明确写着"这个仓库目前没有任何 SeaTunnel job/Kafka Connect 之类的真实
  消费者接进来"。`docs/architecture.md` 里"Kafka 已验证部署,真实生产/
  消费一条消息跑通(2026-08-13)"说的是最基础的连通性冒烟测试,不是一条
  真实的业务数据管道。
- **Kafka Connect + Iceberg Sink Connector**:ADR-011 早就点名这是
  "纯流式/CDC 直接写入 Iceberg 的场景"要走的路径,**独立于 SeaTunnel
  这次批量集成工具的选择**——但同样,这条路径目前只停留在 ADR 文字层面,
  从没真正搭起来过。

一句话总结现状:这套架构里"把数据从 A 搬到 B"(批量集成、基础 CDC)已经
有 SeaTunnel 覆盖,"把 Kafka 流写进 Iceberg"有一条已经设计好但没搭建的
轻量路径(Kafka Connect),**唯一完全空白、没有任何东西覆盖的能力是
"对着流数据做实时计算"**(窗口聚合、多流 join、会话切分、实时异常检测
这类,不是单纯的数据搬运)。

## Flink 的角色定位:只做"实时计算",不做"数据搬运"

**结论:Flink 在这套架构里应该定位成"流式计算引擎",不是"另一个数据
集成工具"。** 理由:

- 数据搬运(批量 ETL、CDC 全库同步、Kafka→Iceberg 直接落地)这几类需求,
  SeaTunnel + 未来的 Kafka Connect + Iceberg Sink Connector 已经够用,
  没有证据表明这两条路径撑不住,引入 Flink 去做同样的事只是多一套要
  运维的系统,没有新增能力。
- 真正只有 Flink(或者同类流计算引擎)才能做、SeaTunnel/Kafka Connect
  做不到的,是"数据经过时顺便算点什么"——比如实时聚合出一个滚动窗口
  指标、多个 Kafka topic 做实时 join、给算法侧做实时特征计算(和 Feast
  在线特征存储可能有天然的配合点,见下面"和现有组件配合")。这才是
  值得为它单独引入一整套新系统的理由。
- 不建议让 Flink 顶替 SeaTunnel 的执行引擎(把 Zeta 换成 Flink)——现在
  单节点 Zeta 没有暴露出任何真实的性能/能力瓶颈,替换执行引擎是纯粹的
  横向迁移,只有成本没有收益,不是"确认必要"这句话真正指向的需求。

## 和 Kafka 怎么配合

Flink 消费 Kafka topic 做实时计算,计算结果二选一(看具体场景,不是
非此即彼的架构决定,留给真正做需求设计时再定):
- 写回一个新的 Kafka topic(给下游继续消费,比如告警系统)。
- 直接用 Flink 自己的 Iceberg connector 落进 Iceberg 表(和 Trino/Spark
  读到的是同一份数据,保持这个项目"湖仓统一"的一贯原则)。

**不建议**用 Flink 承担"原始数据从 Kafka 搬进 Iceberg,不做任何计算"
这种纯搬运场景——那正是 Kafka Connect + Iceberg Sink Connector 该干的
事,用 Flink 做纯搬运是杀鸡用牛刀,平白多一套集群要运维。

## 什么时候引入:现在不是时候,而且前面还有没做完的事

**不建议现在就着手部署 Flink**,原因不是"资源开销大"这一条(虽然架构表
确实标了"重"量级组件,local-lite 16GB 大概率装不下),更根本的原因是
**这套架构里比 Flink 更基础的两块东西现在都还没有真实跑通**:

1. Kafka 现在是零真实消费者的状态,只做过最基础的连通性冒烟测试。
2. Kafka Connect + Iceberg Sink Connector 这条 ADR-011 就设计好的轻量
   路径,从来没有真正搭建过。

**合理的引入顺序**(不是这次要做的事,是记录下来的路线图判断):
1. 先给 Kafka 接一个真实的生产者/消费者,验证这条路径不是摆设。
2. 搭 Kafka Connect + Iceberg Sink Connector,验证"Kafka 流数据能落进
   Iceberg"这条最基础的能力。
3. 只有当第 1/2 步都跑通、并且真的出现"需要对流数据做实时计算,不只是
   搬运"这个具体需求时,才评估引入 Flink——到那时候应该已经有了真实的
   计算场景可以拿来验证,不是凭空搭一个没有实际负载的 Flink 集群。

这个顺序本身也符合这个项目一贯的取舍(ADR-010"按需引入"):不为了
"架构图看起来完整"而提前引入一个还没有真实场景撑住的重量级组件。

## 明确不在这份 ADR 范围内的事

- 不涉及具体部署(Flink Operator 选型、K8s 资源规格、镜像版本)——那些
  要等真正决定引入的时候再定,现在定了也是猜的。
- 不涉及 Kafka Connect + Iceberg Sink Connector 的具体搭建——虽然上面
  提到这条路径应该先于 Flink 做,但这次没有展开设计,是另一项独立工作。
- 不改变当前 CURRENT(cloud-full 部署上线)的范围,这份 ADR 是纯设计
  产出,不代表接下来要切进去实现。
