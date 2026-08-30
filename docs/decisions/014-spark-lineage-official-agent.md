# 014. Spark 血缘用官方 openmetadata-spark-agent,不用自己解析 SQL

- 状态: **决策本身仍然成立(不自己解析 SQL),但选定的那个 artifact 已经
  不能用了** —— 2026-08-30 核实,见下面「2026-08-30:这个方案被 Spark 4
  推翻了一半」。**Spark 血缘至今没有落地。**

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


---

## 2026-08-30:这个方案被 Spark 4 推翻了一半

这份 ADR 定的是"用官方 `org.open-metadata:openmetadata-spark-agent`"。
**2026-08-30 真去 Maven Central 核实,这个 artifact 只有一个版本**:

| | |
|---|---|
| 版本 | `1.0-beta` —— 唯一一个,没有后续 |
| 发布 | 2024-02-13 |
| 编译目标 | Java 11(`maven.compiler.target: 11`) |
| 内嵌 | OpenLineage 1.7.0 |

而这个平台 2026-08-29 已经升到 **Spark 4.1.3 / Scala 2.13 / Java 17**
([ADR-076](076-spark-4-evaluation.md))。一个针对 Java 11 + OpenLineage
1.7 的 2024 年 beta,几乎确定不认 Spark 4 的 API —— OpenLineage 的 Spark
集成为了 Spark 4 做过大改。

**没有盲目把这个 jar 打进镜像去试**:`spark.extraListeners` 里的类加载不到
会在 SparkContext 初始化时抛异常,**直接打死现在能跑的批处理链路**
(`13-run-spark-iceberg-demo.sh` 是已验证的能力)。拿一个已知可用的东西去
赌一个大概率不兼容的依赖,不划算。

### 决策里仍然成立的部分

**"不自己解析 SQL"这条不变。** 理由和当初一样,而且更强了 ——
2026-08-30 给 Trino 配查询血缘时也是同一个判断:用 OpenMetadata 自带的
`DatabaseLineage`,不自建解析器([ADR-085](085-inference-payload-logging.md)
旁边那条 `scripts/47`)。

### 现在的选项(都没验证,不要照着直接做)

1. **改用上游 OpenLineage 自己的 Spark 集成**
   (`io.openlineage:openlineage-spark_2.13`,已经到 1.34.x),transport 指
   向 OpenMetadata 的 OpenLineage 接收端点。**要先确认两件事**:那个版本
   支不支持 Spark 4.1;OpenMetadata 2.0 收不收 OpenLineage 事件。
2. **等 OpenMetadata 发一个支持 Spark 4 的 agent。** 成本最低,但时间不
   由我们控制,而且这个 artifact 两年没更新过了。
3. **降级 Spark 迁就 agent。** ✗ —— Spark 4 是为了解开 Iceberg 1.10 那个
   结才升的(ADR-076),退回去得不偿失。

### 为什么现在不做

Spark 血缘的价值明确,但它**不是当前的瓶颈**:今天平台上 Spark 作业只有
一个 demo,而 Trino(分析师和 dbt 的主路径)的血缘 2026-08-30 已经配上。
等真的有多个 Spark 作业在跑、"改一张表影响哪些 Spark 作业"成为真问题时,
再按上面选项 1 去验证 —— 那时也才有真实作业可以验证它到底通不通。

**这条挂在 roadmap P4 的 B 线里,状态是"设计已定、artifact 需重选"。**
