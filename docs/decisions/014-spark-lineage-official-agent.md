# 014. Spark 血缘用官方 openmetadata-spark-agent,不用自己解析 SQL

- 状态: 已采纳(2026-08-09,用户已实测验证)

## 背景

[ADR-011](011-seatunnel-not-airbyte.md)、[ADR-012](012-dbt-analyst-platform.md) 讨论
SeaTunnel/Flink 的血缘都得自己动手做(读配置结构 + sqlglot 解析 SQL)。Spark 不用
走这条路——用户已经实际测试过官方方案。

## 决策

Spark 作业的血缘用官方 **`org.open-metadata:openmetadata-spark-agent`**(Maven Central),
以 Spark Listener 形式接入,不需要自己解析 SQL/读配置。

关键配置(等 Phase 2 的 Spark Operator 真正跑作业时落到 `SparkApplication` 的
`sparkConf` 里):

```
spark.extraListeners = io.openlineage.spark.agent.OpenLineageSparkListener
spark.openmetadata.transport.type = openmetadata
spark.openmetadata.transport.hostPort = <OpenMetadata 地址>
spark.openmetadata.transport.jwtToken = <token>
spark.openmetadata.transport.pipelineServiceName = <自定义>
spark.openmetadata.transport.pipelineName = <自定义>
```

底层实际是套了一层 [OpenLineage](https://openlineage.io/) 的 Spark listener,不是
OpenMetadata 自己独立实现的。

## 理由与已知限制

- **官方支持,不是自己拼的方案**——用户已实测跑通,比自建 SQL 解析可靠。
- **只能到表级血缘,到不了列级**——Spark SQL 语法复杂度高,agent 本身做不到
  列级解析,这是已知上限,不是没配置对。需要列级血缘的场景(比如具体是
  哪个字段算出来的)这条路走不通,可能还是要靠 dbt(如果转换逻辑挪到 dbt
  里做)或者干脆接受表级血缘够用。
- **对 S3/MinIO 兼容性有已知问题**([open-metadata/OpenMetadata#22843](https://github.com/open-metadata/OpenMetadata/issues/22843)):
  我们的湖仓存储正是 MinIO,不能假设这个 agent 装上就一定好使,真正接入
  Spark Operator 时要专门验证这一点,不能想当然。

## 后果

- Spark Operator 的 `SparkApplication` 模板(现在还没写,在
  `environments/cloud-full/pending-definitions/`)以后要预留这几个
  `sparkConf` 配置项的位置。
- 不需要为 Spark 单独开发血缘解析代码,和 SeaTunnel/Flink 的处境不同,
  省了一块工作量。
