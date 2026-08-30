# ADR-071:平台自己的告警规则(在这之前一条都没有)

日期:2026-08-23
状态:**已部署并生效**(2026-08-19 起,规则抓到过真实问题)。2026-08-30 修正了 `FlinkMetricsMissing` 一条名不副实的注释

## 背景:通知设施是空转的

这个平台装了 kube-prometheus-stack(含 Alertmanager),
`platform/alertmanager-notification/` 里也留好了外部通知渠道的条件化配置
([ADR-060](060-conditional-rendering-and-tls-issuer.md))。

**但中间"什么情况该告警"这一层是空的。** 全仓库搜 `kind: PrometheusRule`,
只有 vendor 进来的 loki chart 自带的那几条,一条平台自己的规则都没有。
也就是说:那套通知设施从来没有东西可以送。

`docs/project/capability-matrix.md` 里"告警送达 ❌"那一格,一直被理解成"缺通知渠道凭据"。
**其实缺的不只是凭据,是规则本身。** 就算现在把企微 webhook 配上,也不会
有任何告警发出来。

## 选题标准:"坏了不会有任何人发现"

不是"哪个组件重要"。Trino 挂了会有一堆人来问,不需要告警。这五条的共同点
是**没有人天天盯着它们,坏了可以安静地坏几周**:

| 告警 | 坏了会怎样(而且没人会发现) |
|---|---|
| `AuditSinkJobNotRunning` | 审计事件还在往 Kafka 写,但没人消费落库,Kafka 保留期一到**永久丢失** |
| `FlinkMetricsMissing` | 盯不到指标 = 上面那条审计告警是瞎的 |
| `KueueWorkloadsInadmissible` | 作业永远排队,而且**不报错**——提交的人以为"还在排" |
| `SchemaRegistryDown` | 新 schema 注册不了,**兼容性校验也就不生效**,这段时间上游可以随便改字段 |
| `CostMetricsMissing` | 成本历史出现空洞,几个月后想看趋势才发现 |

`AuditSinkJobNotRunning` 是 [ADR-066](066-trino-query-audit.md) 那个取舍的
**必要配套**,不是可选项:Trino 的 event listener 配了
`terminate-on-initialization-failure=false`(否则 Kafka 挂了 Trino 起不来),
代价就是审计事件会静默丢失。**没有这条告警,那个取舍是不成立的。**

## 为什么 `for` 都给得很长(10~30 分钟)

这几件事都不是秒级要救的。目的是"别让它悄悄坏几周",不是"三分钟内响应"。
给短了只会制造噪音,而**被习惯性忽略的告警比没有告警更糟**——这条判断和
`scripts/check-image-arch.py` 不进 CI 是同一个理由。

## 顺带补上的:Flink 作业级指标

`AuditSinkJobNotRunning` 用的是 `flink_jobmanager_numRunningJobs`,不是
"Deployment 有几个副本可用"。**这个区分是刻意的**:Pod 层面的健康和作业
层面的健康是两回事,[ADR-062](062-flink-streaming-pipeline.md) 实测过
"JM/TM 都 Running 而作业已经 FAILED"。只监控 Pod 就是在盯错的那一层,正是
`docs/project/production-readiness-gaps.md` 第 2 条批评的东西。

为此加了两样:

1. 两个 FlinkDeployment 的 `flinkConfiguration` 里配上 Prometheus reporter
   (jar 在官方镜像 `/opt` 里自带,不用额外装)。
2. 容器上**显式声明命名端口 `metrics: 9249`**,并加一个 PodMonitor 按端口名
   选目标。少了这个命名端口的表现是:reporter 在跑、9249 有数据,但
   Prometheus 一个 target 都发现不了,**而且哪里都不报错**。

用 PodMonitor 而不是 ServiceMonitor,是因为 JM/TM 由 operator 动态创建,
TM 还随并行度增减,没有一个稳定的 Service 覆盖它们。

## 还没做的

1. **没部署验证。** PromQL 只做了结构检查(本机没有 promtool),真实表达式
   要在集群上用 Prometheus 的 `/api/v1/query` 验一次。
2. **数据质量 / 新鲜度断言失败没有进 Alertmanager。** 它们的结果在
   OpenMetadata 里,不是 Prometheus 指标。OpenMetadata 自带 Alerts &
   Notifications(webhook/邮件),但和 Alertmanager 的 payload 格式不兼容,
   直接对接需要一个转换层——为了避免再引一个自建组件,这件事留到确定通知
   渠道之后再一起设计。**三个来源(质量、新鲜度、审计断流)必须接同一个
   出口**,各接各的最后一定有一套没人维护。
3. 外部通知渠道仍然没有真实凭据(zhenghe:"等后面上生产来测试")。所以
   现在告警只会停在 Alertmanager 界面里。
