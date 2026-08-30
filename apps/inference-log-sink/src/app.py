"""收 KServe payload logger 发来的 CloudEvent,丢进 Kafka。

**只做一件事**:HTTP 收 → Kafka 写。不解析业务字段、不落库、不做聚合 ——
那些是下游 Flink 作业(`streams/inference-log/`)和分析作业的事。
职责单一是这个组件能存在的理由(ADR-085 里讨论过要不要新增它)。

KServe 的 logger 把请求和响应各发一次,靠 CloudEvent 的 `ce-id` 关联:
同一次推理的 request 和 response 是**同一个 id**。这个平台把它原样带下去,
下游用它把两条记录拼起来。

**推理输入很可能包含个人信息** —— 这条链路记录的东西是敏感的。表的访问
控制、保留期、以及"默认不开"这几件事写在 ADR-085 里。
"""
from __future__ import annotations

import json
import os
import sys

from flask import Flask, request

KAFKA_BOOTSTRAP = os.environ.get(
    "KAFKA_BOOTSTRAP_SERVERS", "kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "inference-log")

app = Flask(__name__)
_producer = None


def producer():
    """懒加载 Kafka producer。

    **不在 import 时连** —— 那样 Kafka 没起来这个 Pod 就永远起不来,而它
    的健康和 Kafka 的健康不该绑在一起(推理服务发过来的东西丢了是坏事,
    但让接收端起不来只会让丢得更彻底)。
    """
    global _producer
    if _producer is None:
        from kafka import KafkaProducer
        _producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP.split(","),
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode(),
            # acks=1:leader 确认即可。全量 acks=all 对推理留痕这个场景不值
            # 得 —— 它不是账本,丢极少数几条不改变分布统计的结论。
            acks=1,
            retries=3,
            max_block_ms=5000,
        )
    return _producer


def parse_event(headers, body):
    """把 CloudEvent 的头 + body 变成一条要写进 Kafka 的记录。

    KServe 用的是 **binary content mode**:元数据在 `ce-*` 头上,body 是
    原始 payload。不要去 body 里找 `id`/`type` —— 那是 structured mode 的
    形态,KServe 不用那个。
    """
    ce = {k[3:].lower(): v for k, v in headers.items() if k.lower().startswith("ce-")}
    # ce-type 形如 org.kubeflow.serving.inference.request / .response
    ev_type = (ce.get("type") or "").rsplit(".", 1)[-1] or "unknown"
    return {
        "request_id": ce.get("id", ""),
        "inference_service": ce.get("source", ""),
        "namespace": ce.get("namespace", ""),
        "event_type": ev_type,
        "model": ce.get("modelid", ""),
        # 原样存 JSON 字符串。**不拆成列** —— 不同模型的输入 schema 完全
        # 不同,拆成固定列意味着每加一个模型就要改表(ADR-085)。
        "payload": body if isinstance(body, str) else json.dumps(body, ensure_ascii=False),
        "event_ts": ce.get("time", ""),
    }


@app.route("/", defaults={"path": ""}, methods=["POST"])
@app.route("/<path:path>", methods=["POST"])
def receive(path):
    """KServe 的 logger 会往配置的 URL POST,路径不固定,所以全收。"""
    record = parse_event(dict(request.headers), request.get_data(as_text=True))
    if not record["request_id"]:
        # 没有 ce-id 就没法把 request/response 关联起来,这条记录的价值
        # 大打折扣 —— 但**仍然收下**:丢掉的话连"有过这么一次请求"都不知道。
        app.logger.warning("收到没有 ce-id 的事件,仍然记录")
    try:
        producer().send(KAFKA_TOPIC, record)
        return {"ok": True}, 202
    except Exception as exc:   # noqa: BLE001
        # **返回 5xx 而不是吞掉。** KServe 的 logger 失败会打日志,那是我们
        # 能发现"留痕断了"的唯一信号 —— 吞掉等于静默丢数据。
        app.logger.error("写 Kafka 失败:%s", exc)
        return {"ok": False, "error": str(exc)}, 503


@app.route("/healthz")
def healthz():
    """**只报告进程活着,不探 Kafka。**

    探 Kafka 的话,Kafka 一抖动这个 Pod 就被重启,而重启解决不了 Kafka 的
    问题、只会让接收端也不可用。Kafka 通不通由 `/readyz` 回答。
    """
    return {"status": "ok"}


@app.route("/readyz")
def readyz():
    try:
        producer().partitions_for(KAFKA_TOPIC)
        return {"status": "ok", "kafka": "connected"}
    except Exception as exc:   # noqa: BLE001
        return {"status": "degraded", "kafka": str(exc)}, 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
