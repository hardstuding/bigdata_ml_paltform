"""
Kafka(trino-query-events)-> Flink -> Iceberg,把 Trino 的查询审计事件长期
留存下来。见 docs/decisions/066-trino-query-audit.md 的"还没做的"第 1 条。

**为什么必须落 Iceberg,Kafka 里放着不算完**:Kafka 有保留期
(cloud-full 30 天 / prod 90 天),而审计记录的用途恰恰是"事后很久才需要"
——出了数据泄露要回答"谁导出过这张表",员工离职要回答"他走之前碰过
什么"。保留期一到就滚掉了,而且**补不回来**。

## 为什么写成两张表,不是一张

一次查询可以访问多张表,所以事件里的 `tables` 是个数组。处理数组有两条路:
在 Iceberg 里存 `ARRAY<ROW<...>>`,或者展开成多行。这里选展开:

  1. `audit.query_events` —— **一次查询一行**。回答"谁在什么时候跑了什么
     SQL、成功没有"。没有任何数组/嵌套类型,Trino 直接查。
  2. `audit.query_table_access` —— **一次查询访问的每张表各一行**。回答
     **"谁查过这张表"**——这是合规场景真正要问的那个问题,展开成行之后
     它就是一句最普通的 `WHERE table_name = '...'`,不需要 `any_match`
     这类数组函数,也不依赖 Iceberg 对嵌套类型的支持程度。

代价是查询文本在第二张表里不重复存(只存 query_id,要看 SQL 原文 join
第一张表)。没有访问任何表的查询(比如 `SELECT 1`)不会出现在第二张表里
——这是对的,它本来就不是一次"表访问"。

## 时间字段为什么用 Kafka 的记录时间,不解析 payload 里的

事件 payload 自带 `createTime`/`endTime`,但**它们的字符串格式没有在真机上
核对过**。这个平台在时间格式上栽过一次(ADR-062:格式对不上会静默变
null,然后在两个算子之后以完全不相干的报错炸出来),不重蹈覆辙:排序和
分区用 Kafka 记录自带的时间戳元数据(`TIMESTAMP_LTZ` METADATA),它由
Kafka 自己写入,不依赖任何解析;payload 里那两个原样存成字符串,等以后
在真机上确认了格式再决定要不要转成 timestamp。

这份文件和 apps/flink-audit-sink/manifests/script-configmap.yaml 里的内容
保持同步(和 flink_device_events_stream.py 是同一个模式)。
"""
import time as _t

from pyflink.table import EnvironmentSettings, TableEnvironment

KAFKA_BOOTSTRAP_SERVERS = "platform-kafka-kafka-bootstrap.kafka.svc.cluster.local:9092"
KAFKA_TOPIC = "trino-query-events"
HIVE_METASTORE_URI = "thrift://hive-metastore.data.svc.cluster.local:9083"
ICEBERG_WAREHOUSE = "s3a://lakehouse/warehouse"


def main() -> None:
    t_env = TableEnvironment.create(EnvironmentSettings.in_streaming_mode())

    # Iceberg sink 靠 checkpoint 提交,不开 checkpoint 数据永远不落盘
    # (ADR-062 的教训)。审计不需要秒级新鲜度,间隔给得比 demo 那条链路
    # 长一些,减少小文件。
    t_env.get_config().set("execution.checkpointing.interval", "60 s")
    t_env.get_config().set("execution.checkpointing.mode", "EXACTLY_ONCE")

    # `catalog` / `schema` / `table` / `user` 在 Calcite 里都是保留字,
    # 必须反引号——和 device_events 那条链路里 `value` 是同一类坑
    # (ADR-062),这次是提前避开,不是踩到之后再补。
    t_env.execute_sql(f"""
        CREATE TABLE kafka_query_events (
            eventPayload ROW<
                metadata ROW<
                    queryId    STRING,
                    `query`    STRING,
                    queryState STRING,
                    `tables`   ARRAY<ROW<
                        `catalog` STRING,
                        `schema`  STRING,
                        `table`   STRING
                    >>
                >,
                context ROW<
                    `user`              STRING,
                    principal           STRING,
                    `source`            STRING,
                    remoteClientAddress STRING
                >,
                createTime STRING,
                endTime    STRING
            >,
            event_ts TIMESTAMP_LTZ(3) METADATA FROM 'timestamp' VIRTUAL
        ) WITH (
            'connector' = 'kafka',
            'topic' = '{KAFKA_TOPIC}',
            'properties.bootstrap.servers' = '{KAFKA_BOOTSTRAP_SERVERS}',
            'properties.group.id' = 'flink-trino-audit-sink',
            'scan.startup.mode' = 'earliest-offset',
            'format' = 'json',
            -- **审计流这里必须 false,而且比别处更不能妥协。** 开着的话
            -- 解析失败会被静默丢弃,表现是"审计表里少了一些记录",而
            -- 少记录这件事在审计场景里是最不能接受的失败模式——它不会
            -- 报错,只会在很久以后你想查某个人时发现"查不到"。
            'json.ignore-parse-errors' = 'false'
        )
    """)

    t_env.execute_sql(f"""
        CREATE CATALOG iceberg_catalog WITH (
            'type' = 'iceberg',
            'catalog-type' = 'hive',
            'uri' = '{HIVE_METASTORE_URI}',
            'warehouse' = '{ICEBERG_WAREHOUSE}'
        )
    """)

    # 等 Hive Metastore 真的可用再建库——CREATE DATABASE 失败属于**作业
    # 提交阶段**的失败,restart-strategy 管不着,集群重启一次这条链路就
    # 永久停了(ADR-062 实测过)。
    for attempt in range(60):
        try:
            t_env.execute_sql("CREATE DATABASE IF NOT EXISTS iceberg_catalog.audit")
            break
        except Exception as exc:           # noqa: BLE001 - 就是要兜住所有连不上的情况
            print(f"[{attempt + 1}/60] Hive Metastore 还连不上,10 秒后重试: {exc}", flush=True)
            _t.sleep(10)
    else:
        raise RuntimeError("等了 10 分钟 Hive Metastore 仍然不可用,放弃")

    t_env.execute_sql("""
        CREATE TABLE IF NOT EXISTS iceberg_catalog.audit.query_events (
            query_id       STRING,
            query_user     STRING,
            principal      STRING,
            client_source  STRING,
            client_address STRING,
            query_state    STRING,
            query_text     STRING,
            create_time_raw STRING,
            end_time_raw    STRING,
            event_ts       TIMESTAMP_LTZ(3)
        )
    """)

    t_env.execute_sql("""
        CREATE TABLE IF NOT EXISTS iceberg_catalog.audit.query_table_access (
            query_id     STRING,
            query_user   STRING,
            catalog_name STRING,
            schema_name  STRING,
            table_name   STRING,
            query_state  STRING,
            event_ts     TIMESTAMP_LTZ(3)
        )
    """)

    statement_set = t_env.create_statement_set()

    statement_set.add_insert_sql("""
        INSERT INTO iceberg_catalog.audit.query_events
        SELECT
            eventPayload.metadata.queryId,
            eventPayload.context.`user`,
            eventPayload.context.principal,
            eventPayload.context.`source`,
            eventPayload.context.remoteClientAddress,
            eventPayload.metadata.queryState,
            eventPayload.metadata.`query`,
            eventPayload.createTime,
            eventPayload.endTime,
            event_ts
        FROM kafka_query_events
    """)

    # CROSS JOIN UNNEST:把 tables 数组展开成多行。用 CROSS 而不是 LEFT
    # 是刻意的——没访问任何表的查询(`SELECT 1`)不该出现在"表访问日志"
    # 里,它在第一张表里已经完整记着了。
    statement_set.add_insert_sql("""
        INSERT INTO iceberg_catalog.audit.query_table_access
        SELECT
            e.eventPayload.metadata.queryId,
            e.eventPayload.context.`user`,
            t.`catalog`,
            t.`schema`,
            t.`table`,
            e.eventPayload.metadata.queryState,
            e.event_ts
        FROM kafka_query_events AS e
        CROSS JOIN UNNEST(e.eventPayload.metadata.`tables`) AS t (`catalog`, `schema`, `table`)
    """)

    statement_set.execute()


if __name__ == "__main__":
    main()
