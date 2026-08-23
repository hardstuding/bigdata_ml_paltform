"""
往 Kafka 灌 device event 数据——给 Flink 流式作业(apps/flink-streaming-demo/)
当数据源用,验证"流式数据接入"这条链路真的有数据在流动,不是空 topic。

**2026-08-23 从 JSON 改成 Avro + Schema Registry(ADR-068 第二段)。**

改的理由不是"Avro 更好",是**JSON 这条路上没有任何东西能拦住上游改字段**:
生产端改一个字段名或类型,消费端只有运行时才知道,而且这个平台实测过报错
位置离根因隔着一两层(ADR-062 那个 `ignore-parse-errors` 静默变 null 的坑)。
接上 Schema Registry 之后,不兼容的 schema 在**注册阶段**就被 409 拒掉,
根本发不出去。

**顺带消掉了一整类 bug**:时间字段以前是字符串,格式必须和 Flink 的
`json.timestamp-format.standard` 逐字符对上,对不上就静默变 null——这个坑
2026-08-22 真踩过,报错出现在两个算子之后、和格式完全不相干。Avro 用
`timestamp-millis` 逻辑类型,时间就是一个 long,**没有"格式"这个概念了**,
这类 bug 从设计上不存在。

topic 从 `device-events` 改名成 `device-events-avro`,不是原地换格式:
同一个 topic 里混着 JSON 和 Avro 的话,任何从 earliest-offset 重放的消费端
都会在读到老消息时炸。改名是最省事、也最不会出错的迁移方式。

这份文件是给人看/方便本地阅读的那份,和
apps/kafka-producer/manifests/script-configmap.yaml 里的内容保持同步
——运行时真正挂载进 CronJob pod 的是 ConfigMap 里那份。

用 scripts/31-run-flink-streaming-demo.sh 触发对应的 CronJob,不要直接跑
这个文件。
"""
import os
import random
import time
import uuid

from confluent_kafka import SerializingProducer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import StringSerializer

BOOTSTRAP_SERVERS = os.environ.get(
    "KAFKA_BOOTSTRAP_SERVERS",
    "platform-kafka-kafka-bootstrap.kafka.svc.cluster.local:9092",
)
SCHEMA_REGISTRY_URL = os.environ.get(
    "SCHEMA_REGISTRY_URL",
    "http://karapace.schema-registry.svc.cluster.local:8081",
)
TOPIC = os.environ.get("KAFKA_TOPIC", "device-events-avro")
EVENTS_PER_RUN = int(os.environ.get("EVENTS_PER_RUN", "20"))

EVENT_TYPES = ["heartbeat", "temperature", "humidity", "battery", "error"]
DEVICE_IDS = [f"device-{i:03d}" for i in range(1, 11)]

# 字段构成和之前的 JSON 版完全一致,也和
# apps/airflow/dags/seatunnel_device_events.py 那条批量链路保持一致——
# 这个平台有意让批量和流式两条接入路径产出同一个数据形状,方便互相核对。
#
# **`event_time` 是 timestamp-millis 而不是字符串**,见文件头部的说明。
# Flink 的 avro-confluent format 会把它映射成 TIMESTAMP(3),不需要在
# SQL 侧配任何时间格式。
SCHEMA = """
{
  "type": "record",
  "name": "DeviceEvent",
  "namespace": "platform.demo",
  "fields": [
    {"name": "event_id",   "type": "long"},
    {"name": "device_id",  "type": "string"},
    {"name": "event_type", "type": "string"},
    {"name": "value",      "type": "double"},
    {"name": "event_time", "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}
"""


def _delivery_report(err, msg):
    if err is not None:
        # 让 CronJob 的这次运行以非零退出码结束,不要吞掉真实的投递失败
        raise RuntimeError(f"消息投递失败: {err}")
    print(f"已投递: partition={msg.partition()} offset={msg.offset()}")


def main() -> None:
    registry = SchemaRegistryClient({"url": SCHEMA_REGISTRY_URL})
    # AvroSerializer 会在第一次序列化时把 schema 注册到
    # `<topic>-value` 这个 subject 下,并把返回的 schema id 写进消息头部
    # (Confluent wire format:1 字节 magic + 4 字节 schema id + Avro 负载)。
    # **如果这个 schema 和已注册的版本不兼容,注册就会失败,消息发不出去**
    # ——这正是引入 registry 想要的效果:把"上游改字段"挡在发送这一侧,
    # 而不是等下游炸。
    serializer = AvroSerializer(registry, SCHEMA)
    producer = SerializingProducer({
        "bootstrap.servers": BOOTSTRAP_SERVERS,
        "key.serializer": StringSerializer("utf_8"),
        "value.serializer": serializer,
    })

    run_id = uuid.uuid4().hex[:8]
    print(f"开始灌 {EVENTS_PER_RUN} 条 device event,topic={TOPIC},"
          f"registry={SCHEMA_REGISTRY_URL},run_id={run_id}")

    for i in range(EVENTS_PER_RUN):
        now_ms = int(time.time() * 1000)
        event = {
            "event_id": now_ms * 1000 + i,
            "device_id": random.choice(DEVICE_IDS),
            "event_type": random.choice(EVENT_TYPES),
            "value": round(random.uniform(0, 100), 2),
            "event_time": now_ms,
        }
        producer.produce(topic=TOPIC, key=event["device_id"], value=event,
                         on_delivery=_delivery_report)
        producer.poll(0)

    producer.flush(30)
    print(f"完成,已投递 {EVENTS_PER_RUN} 条事件(run_id={run_id})")


if __name__ == "__main__":
    main()
