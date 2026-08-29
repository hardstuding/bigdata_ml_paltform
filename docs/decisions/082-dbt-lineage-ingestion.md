# ADR-082:让 dbt 的产物真的被消费 —— 血缘接进 OpenMetadata

日期:2026-08-29
状态:**已实现,待实机验证**(验证脚本 `scripts/44-verify-openmetadata-dbt-lineage.sh`)

## 起因

`dbt_demo` 这个 Airflow DAG 从 2026-08-16 起就在跑 `dbt build` +
`dbt docs generate`,并把 `manifest.json` / `catalog.json` 上传到
`s3://lakehouse/dbt-artifacts/platform_demo/` —— 那个路径就是 OpenMetadata
的 dbt 连接器期望的位置。

**但没有任何东西去消费它们。** 这条在 `docs/roles.md` 里一直如实记着
("artifacts 已经上传到连接器期望的位置了,但没有任何东西去消费它们"),
也就是说:产物一直在生成、一直没人读,数据目录里的血缘那半一直是空的。

这是这个仓库反复出现的一类问题的又一个实例:**每一层看起来都正常**——
DAG 绿的、文件真的在 MinIO 上、OpenMetadata 也在跑,唯独中间少了一根线,
而没有任何一个绿灯会因此变红。

## 决策

在**已有的** `trino` DatabaseService 上再挂一条 `pipelineType: dbt` 的
IngestionPipeline,数据源指向 MinIO 上那两个文件。

**不新建 DatabaseService**:dbt 模型最终落地成的就是 Trino 里的表
(`iceberg.demo.stg_orders` / `iceberg.demo.daily_order_totals`),挂在
别的 service 下会让同一张表在目录里出现两次,血缘反而更乱。

### 字段是核实过的,不是猜的

从 OpenMetadata 的 JSON Schema 源码逐个确认:

| 字段 | 出处 |
|---|---|
| `sourceConfig.config.type = "DBT"` | `metadataIngestion/dbtPipeline.json` |
| `dbtConfigType = "s3"`、`dbtSecurityConfig`、`dbtPrefixConfig` | `metadataIngestion/dbtconfig/dbtS3Config.json` |
| `awsRegion`(**必填**)、`awsAccessKeyId`、`awsSecretAccessKey`、`endPointURL` | `security/credentials/awsCredentials.json` |

MinIO 不是真的 AWS:`awsRegion` 给 `us-east-1` 这个 S3 客户端通用默认值,
真正决定连到哪的是 `endPointURL`。

### 两个刻意关掉的开关

`dbtUpdateDescriptions` 和 `dbtUpdateOwners` 都设成 `false`。

表的负责人是 `table-registration-app` 那条**有审批的**登记流程写进去的
(ADR-043);dbt 里的 owner 只是 `schema.yml` 里随手写的一个字符串。
让 dbt 覆盖过去,等于用弱信息盖掉强信息。描述同理。

### 调度错开

三条采集管道分别是 `0 */6`(元数据)、`30 */6`(数据质量)、
`45 */6`(dbt)。**不是美观问题**:`openmetadata` 命名空间的内存配额有限,
2026-08-28 刚因为配额不够导致采集 Job 建不出 Pod(表现是 Job
`Running 0/1` 而一个 Pod 都没有),三条同时起来会重演。

## 怎么算验证通过

`scripts/44` 的判定条件**不是** "Job Completed",是**能从血缘接口查到这条边**:

```
iceberg.demo.orders --(source)--> stg_orders --(ref)--> daily_order_totals
```

查 `/api/v1/lineage/table/name/trino.iceberg.demo.daily_order_totals`,
上游节点里必须同时出现 `stg_orders` 和 `orders`,少一个就报 PARTIAL 并
非零退出。理由和这个仓库一贯的那条一样:deploy 返回 200 不等于采集跑过,
Job Completed 不等于血缘真的建起来了。

## 还没解决的

- **列级血缘**没有验证过。dbt 的 manifest 里有列级信息,OpenMetadata 也
  支持,但这次只把表级这条线打通,列级留到有真实需求时再说。
- `dbt_demo` DAG 仍然是 `schedule=None`(手动触发)。也就是说 dbt 产物
  不会自己更新,血缘会停在最后一次手动跑的状态。这条和"demo DAG 要不要
  转成常驻定时任务"是同一个更大的问题,不在这条 ADR 里解决。
