# ADR-065:数据质量断言复用 OpenMetadata,不引入独立框架

日期:2026-08-23
状态:已实机验证(cloud-full)

## 背景

[`docs/project/production-readiness-gaps.md`](../project/production-readiness-gaps.md) 把
"数据质量断言完全没有"排在生产可用性缺口的第一位,理由是:**作业挂了会
告警、有人管;数据错了不会告警**,它一路流进报表和模型训练集,几周后业务
方发现数字不对时,已经回溯不出哪天开始错的、下游哪些结果被污染了。

zhenghe 提示"OpenMetadata 好像带有相关的功能,可能可以复用"。核实结果:
对的,1.13.3 自带 25 个 `testDefinition`(实机数过)和一整套 TestSuite /
TestCase / IngestionPipeline 的执行链路。

## 决策

用 OpenMetadata 自带的 Data Quality,不引入 Great Expectations / Soda /
dbt tests。

**理由不是"已经装了所以顺手":**

1. **断言结果和数据目录长在一起。** 分析师在查一张表之前就能看到"这张表
   昨天的质量检查过没过"。这是这套东西有没有用的分水岭——只把结果发进
   告警通道的话,只有运维看得到,而**真正会被脏数据坑到的是用数据的人**。
   独立框架要额外做一层回写目录才能达到同样效果,那层胶水本身又是新的
   维护负担。
2. **执行编排已经有了。** TestSuite 类型的 IngestionPipeline 走的是和
   scripts/29 元数据采集完全相同的 k8s 原生编排([ADR-015](015-openmetadata-architecture.md)),
   不用再引入第二个调度器。
3. **连接配置已经有了。** 复用同一个 `trino` DatabaseService,不用维护
   第二份 Trino 凭据。

## 代价(明确写出来,不藏着)

断言只能用内置的那 25 个类型,**表达能力不如自己写 SQL**。目前落地的三条
覆盖的是最常见的事故类型,不是全部:

| 断言 | 防的是什么 |
|---|---|
| `tableRowCountToBeBetween`(≥1) | 空表——上游断供、分区路径写错、过滤条件写反,现象都是"任务成功但表是空的" |
| `columnValuesToBeUnique`(order_id) | 主键重复,下游所有聚合数字翻倍且不报错 |
| `columnValuesToBeNotNull`(amount) | 关键字段变 null,这是本平台实测过的失效模式(见 [ADR-062](062-flink-streaming-pipeline.md) 里 `ignore-parse-errors` 那条) |

真需要复杂断言时再单独评估。**不要因为"框架不够强"就现在推翻这个选择——
先有防护比防护完美重要得多。**

## 实现里踩到的三个坑(都是实机试出来的,文档上看不出来)

1. 建基础测试套件的路径在 1.13.3 是 `POST /api/v1/dataQuality/testSuites/basic`,
   **不是 `/executable`**(后者返回 405),字段叫 `basicEntityReference`。
2. 建 testCase 时 **body 里不能带 `testSuite` 字段**——不管传 FQN 字符串
   还是 EntityReference 对象,一律 `400 Invalid request format`。套件是从
   `entityLink` 推断的。"少传一个字段反而对"这种事只能试出来。
3. 查结果 **不能按 `entityLink` 过滤**:那样只返回表级断言,列级断言的
   `entityLink` 带 `::columns::<列名>` 后缀,匹配不上。验证脚本第一版就是
   这么写的,两条列级断言明明已经 Success,脚本却等到超时报 FAIL——**又一次
   "验证脚本自己错了,把成功报成失败"**,和 scripts/30 是同一类跟头。

## 验证证据

- `om-job-orders-data-quality-74546f14` Complete,耗时 24s
- 三条断言全部 Success
- **额外造了一条一定会失败的探针**(要求行数至少 10 亿):实测报 `Failed`,
  同时三条真实断言仍是 `Success`。这证明的是**绿灯能变红**——只看"三条都
  绿"没法区分"数据是好的"和"这套机制根本没在检查"。探针验证后已删除。

## 还没做的

- **断言结果失败时没有告警通道**。现在要人主动去 OpenMetadata 看。接进
  Alertmanager 是下一步,和 `project/production-readiness-gaps.md` 第 2 条(数据
  新鲜度 SLO)是同一套出口,应该一起做,不要各接各的。
- 只覆盖了一张 demo 表。真实使用时"哪些表需要哪些断言"应该是建表流程的
  一部分(和 `apps/table-registration-app/` 的建表申请合流),而不是事后
  由平台组挨个补。
