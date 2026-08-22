"""
往 Kafka 的 device-events topic 灌 device event 数据——给 Flink 流式作业
(apps/flink-streaming-demo/)当数据源用,验证"流式数据接入"这条链路
真的有数据在流动,不是空 topic。

事件 schema 和 apps/airflow/dags/seatunnel_device_events.py 里
SeaTunnel FakeSource 用的那套完全一致(event_id/device_id/event_type/
value/event_time),不是另发明一套——这个平台已经有一条"批量接入"参照,
流式接入复用同一个数据形状,方便以后核对两条链路的产出。

这份文件是给人看/方便本地阅读的那份,和
apps/kafka-producer/manifests/script-configmap.yaml 里的内容保持同步
——运行时真正挂载进 CronJob pod 的是 ConfigMap 里那份,见
apps/spark-iceberg-demo/manifests/script-configmap.yaml 顶部注释里
解释过的同一个模式。

用 scripts/29-run-kafka-flink-streaming-demo.sh 触发对应的
CronJob(手动 kubectl create job --from=cronjob 立即跑一次),不要直接
跑这个文件。
"""
import json
import os
import random
import time
import uuid
from datetime import datetime, timezone

from confluent_kafka import Producer

BOOTSTRAP_SERVERS = os.environ.get(
    "KAFKA_BOOTSTRAP_SERVERS",
    "platform-kafka-kafka-bootstrap.kafka.svc.cluster.local:9092",
)
TOPIC = os.environ.get("KAFKA_TOPIC", "device-events")
EVENTS_PER_RUN = int(os.environ.get("EVENTS_PER_RUN", "20"))

EVENT_TYPES = ["heartbeat", "temperature", "humidity", "battery", "error"]
DEVICE_IDS = [f"device-{i:03d}" for i in range(1, 11)]


def _delivery_report(err, msg):
    if err is not None:
        # 让 CronJob 的这次运行以非零退出码结束,不要吞掉真实的投递失败
        raise RuntimeError(f"消息投递失败: {err}")
    print(f"已投递: partition={msg.partition()} offset={msg.offset()}")


def main() -> None:
    producer = Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})

    run_id = uuid.uuid4().hex[:8]
    print(f"开始灌 {EVENTS_PER_RUN} 条 device event,topic={TOPIC},run_id={run_id}")

    for i in range(EVENTS_PER_RUN):
        event = {
            "event_id": int(time.time() * 1000) * 1000 + i,
            "device_id": random.choice(DEVICE_IDS),
            "event_type": random.choice(EVENT_TYPES),
            "value": round(random.uniform(0, 100), 2),
            # ISO-8601,和 Flink SQL 侧 'json.timestamp-format.standard'='ISO-8601'
            # 配套,见 apps/flink-streaming-demo/manifests/script-configmap.yaml
            # **时间格式必须是 `yyyy-MM-dd HH:mm:ss.SSS`,不能用 ISO 的
            # `T` 分隔符和结尾的 `Z`。** 2026-08-22 夜实测:写成
            # `2026-08-22T19:05:20.187Z` 之后,Flink 的 JSON format 默认按
            # SQL 标准解析 TIMESTAMP(3),`T`/`Z` 解析失败得到 null,作业在
            # 消费到第一条真实消息时就崩:
            #   RuntimeException: RowTime field should not be null,
            #   please convert it to a non-null long value.
            # 而且报错发生在下游算子,和"格式不对"这个根因看着毫不相干。
            # (另一条路是给 Kafka source 加
            # `'json.timestamp-format.standard' = 'ISO-8601'`,但那个模式
            # 也不接受结尾的 Z,还得再改一次格式,不如直接产出 SQL 标准。)
            "event_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        }
        producer.produce(
            TOPIC,
            key=event["device_id"].encode("utf-8"),
            value=json.dumps(event).encode("utf-8"),
            callback=_delivery_report,
        )
        producer.poll(0)

    producer.flush(30)
    print(f"完成,已投递 {EVENTS_PER_RUN} 条事件(run_id={run_id})")


if __name__ == "__main__":
    main()
