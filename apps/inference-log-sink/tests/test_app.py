"""inference-log-sink 的测试。

重点在**解析 CloudEvent 的那几个容易搞错的地方**,以及"失败时不能静默吞掉"
—— 一个留痕组件最坏的行为不是挂掉,是安静地什么都不记。
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import app as sink  # noqa: E402


@pytest.fixture
def client():
    sink.app.config["TESTING"] = True
    with sink.app.test_client() as c:
        yield c


def ce_headers(**kw):
    base = {
        "ce-id": "req-123",
        "ce-type": "org.kubeflow.serving.inference.request",
        "ce-source": "http://demo-rf-classifier-predictor.kserve-demo.svc.cluster.local/",
        "ce-namespace": "kserve-demo",
        "ce-time": "2026-08-30T05:00:00Z",
        "Content-Type": "application/json",
    }
    base.update(kw)
    return base


class TestParseCloudEvent:
    def test_元数据从_ce_头上取_不是从_body(self):
        """KServe 用的是 **binary content mode** —— 元数据在头上,body 是原始
        payload。去 body 里找 id/type 是 structured mode 的形态,KServe 不用
        那个,照那么写会一条都解析不出来。"""
        r = sink.parse_event(ce_headers(), '{"instances": [[1, 2]]}')
        assert r["request_id"] == "req-123"
        assert r["namespace"] == "kserve-demo"
        assert r["event_ts"] == "2026-08-30T05:00:00Z"

    def test_从_ce_type_末段取出请求还是响应(self):
        assert sink.parse_event(ce_headers(), "{}")["event_type"] == "request"
        assert sink.parse_event(
            ce_headers(**{"ce-type": "org.kubeflow.serving.inference.response"}),
            "{}")["event_type"] == "response"

    def test_ce_头大小写不敏感(self):
        # HTTP 头本来就大小写不敏感,不同客户端发的大小写不一样。
        r = sink.parse_event({"CE-Id": "x", "CE-Type": "a.b.response"}, "{}")
        assert r["request_id"] == "x" and r["event_type"] == "response"

    def test_payload_原样保留_不拆列(self):
        # 不同模型的输入 schema 完全不同,拆成固定列意味着每加一个模型就
        # 要改表(ADR-085)。
        body = '{"instances": [[5.1, 3.5]], "extra": {"a": 1}}'
        assert json.loads(sink.parse_event(ce_headers(), body)["payload"]) == json.loads(body)

    def test_缺少_ce_type_时不崩(self):
        assert sink.parse_event({"ce-id": "x"}, "{}")["event_type"] == "unknown"


class TestReceive:
    def test_正常写进_kafka(self, client):
        prod = MagicMock()
        with patch.object(sink, "producer", return_value=prod):
            resp = client.post("/", headers=ce_headers(), data='{"instances": [[1]]}')
        assert resp.status_code == 202
        topic, record = prod.send.call_args[0]
        assert topic == sink.KAFKA_TOPIC
        assert record["request_id"] == "req-123"

    def test_没有_ce_id_也收下(self, client):
        # 丢掉的话连"有过这么一次请求"都不知道。
        prod = MagicMock()
        with patch.object(sink, "producer", return_value=prod):
            resp = client.post("/", headers={"ce-type": "a.b.request"}, data="{}")
        assert resp.status_code == 202
        prod.send.assert_called_once()

    def test_kafka_写失败返回_503_而不是吞掉(self, client):
        """**一个留痕组件最坏的行为不是挂掉,是安静地什么都不记。**
        KServe 的 logger 失败会打日志 —— 那是我们能发现"留痕断了"的唯一
        信号,返回 200 等于把这个信号也吞了。"""
        prod = MagicMock()
        prod.send.side_effect = RuntimeError("kafka down")
        with patch.object(sink, "producer", return_value=prod):
            resp = client.post("/", headers=ce_headers(), data="{}")
        assert resp.status_code == 503
        assert resp.get_json()["ok"] is False

    def test_任意路径都收(self, client):
        # KServe 往配置的 URL POST,路径不固定。
        prod = MagicMock()
        with patch.object(sink, "producer", return_value=prod):
            assert client.post("/whatever/path", headers=ce_headers(),
                               data="{}").status_code == 202


class TestHealth:
    def test_healthz_不探_kafka(self, client):
        """探 Kafka 的话,Kafka 一抖动这个 Pod 就被重启 —— 而重启解决不了
        Kafka 的问题,只会让接收端也不可用。"""
        with patch.object(sink, "producer", side_effect=RuntimeError("kafka down")):
            assert client.get("/healthz").status_code == 200

    def test_readyz_探_kafka(self, client):
        with patch.object(sink, "producer", side_effect=RuntimeError("kafka down")):
            assert client.get("/readyz").status_code == 503
        prod = MagicMock()
        with patch.object(sink, "producer", return_value=prod):
            assert client.get("/readyz").status_code == 200
