"""把 KServe 的推理留痕从 Kafka 落到 Iceberg(ADR-085)。

结构和 `scripts/flink_trino_audit_sink.py` 一样,因为解决的是同一类问题
(Kafka JSON → Iceberg),**刻意保持一致**:排障套路、监控指标、踩过的坑
全部复用。

两个从审计那条链路直接继承过来的决定:

1. **`json.ignore-parse-errors` 必须是 false。** 开着的话解析失败会被静默
   丢弃,表现是"表里少了一些记录" —— 而在留痕场景里这是最不能接受的失败
   模式:它不报错,只在很久以后你想查某一次推理时发现查不到。
2. **字段名避开 Calcite 保留字。** `value`/`table`/`user` 这类在 Flink SQL
   里要反引号,踩过两次(ADR-062、ADR-066)。
   **这次仍然漏了一个:`model`。** 上面这行字当时就写着"提前避开",然后
   实机第一次提交就报
     SqlParserException: Encountered "model" at line 7, column 13
   ——**"我记得要避开保留字"和"我知道哪些是保留字"是两回事**。Calcite 的
   保留字表有几百个,靠印象挑不出来。真正可靠的做法只有一个:**所有字段
   名一律加反引号**,不去判断哪个需要。这里现在就是这么做的。
"""
import os

from pyflink.table import EnvironmentSettings, TableEnvironment

KAFKA_BOOTSTRAP_SERVERS = os.environ.get(
    "KAFKA_BOOTSTRAP_SERVERS", "platform-kafka-kafka-bootstrap.kafka.svc.cluster.local:9092")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "inference-log")
WAREHOUSE = os.environ.get("ICEBERG_WAREHOUSE", "s3a://lakehouse/")
HMS_URI = os.environ.get("HIVE_METASTORE_URI", "thrift://hive-metastore.data.svc.cluster.local:9083")


def main():
    t_env = TableEnvironment.create(EnvironmentSettings.in_streaming_mode())
    t_env.get_config().set("execution.checkpointing.interval", "60s")
    t_env.get_config().set("execution.checkpointing.mode", "EXACTLY_ONCE")

    t_env.execute_sql(f"""
        CREATE TABLE kafka_inference_log (
            `request_id`        STRING,
            `inference_service` STRING,
            `namespace`         STRING,
            `event_type`        STRING,
            `model`             STRING,
            `payload`           STRING,
            `event_ts`          STRING,
            `ingest_ts` TIMESTAMP_LTZ(3) METADATA FROM 'timestamp' VIRTUAL
        ) WITH (
            'connector' = 'kafka',
            'topic' = '{KAFKA_TOPIC}',
            'properties.bootstrap.servers' = '{KAFKA_BOOTSTRAP_SERVERS}',
            'properties.group.id' = 'flink-inference-log-sink',
            'scan.startup.mode' = 'earliest-offset',
            'format' = 'json',
            -- 见模块顶部第 1 条:留痕场景下静默丢记录是最坏的失败模式。
            'json.ignore-parse-errors' = 'false'
        )
    """)

    t_env.execute_sql(f"""
        CREATE CATALOG iceberg_catalog WITH (
            'type' = 'iceberg',
            'catalog-type' = 'hive',
            'uri' = '{HMS_URI}',
            'warehouse' = '{WAREHOUSE}'
        )
    """)

    # `ml` 这个 schema 和 `audit` 一样,默认只有 platform-team 能读
    # (OPA 策略里配)—— 推理输入很可能包含个人信息。
    t_env.execute_sql("CREATE DATABASE IF NOT EXISTS iceberg_catalog.ml")

    t_env.execute_sql("""
        CREATE TABLE IF NOT EXISTS iceberg_catalog.ml.inference_log (
            `request_id`        STRING,
            `inference_service` STRING,
            `namespace_name`    STRING,
            `event_type`        STRING,
            `model`             STRING,
            `payload`           STRING,
            `event_time_raw`    STRING,
            `ingest_ts`         TIMESTAMP_LTZ(3)
        )
    """)

    t_env.execute_sql("""
        INSERT INTO iceberg_catalog.ml.inference_log
        SELECT `request_id`, `inference_service`, `namespace`, `event_type`,
               `model`, `payload`, `event_ts`, `ingest_ts`
        FROM kafka_inference_log
    """).wait()


if __name__ == "__main__":
    main()
