"""企业微信告警通知的 payload 形状(ADR-090)。

**为什么值得单独测。** 这段代码住在 `apps/components/superset.yaml` 的
`configOverrides` 里 —— 一段注入进 Superset 的 Python。它有两个特点让它
特别容易坏掉而没人发现:

1. **它不在任何 import 路径上**,静态检查扫不到,IDE 也不会提示。
2. **它的失败是远端的**:payload 形状错了,企微返回 `errcode != 0`,而告警
   本身"发出去了" —— 从 Superset 这边看不出问题。

所以这里把那段逻辑抽出来直接测形状。企微机器人只认这一种:

    {"msgtype": "markdown", "markdown": {"content": "..."}}

字段名错一个,消息就发不出去。
"""
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
COMPONENT = REPO / "apps" / "components" / "superset.yaml"


def load_notification_class():
    """把 configOverrides 里那个通知类抽出来,用一个假的父类跑起来。

    **不 import Superset** —— 那需要一整套 Flask app 上下文,而这里要测的
    只是"payload 长什么样"这一件事。
    """
    text = COMPONENT.read_text(encoding="utf-8")
    doc = yaml.safe_load(text.replace("{{RES:", "PLACEHOLDER_").replace("}}", ""))
    src = doc["spec"]["source"]["helm"]["valuesObject"]["configOverrides"]["wecom_alert"]

    # 把 FLASK_APP_MUTATOR 里那个类摘出来:去掉函数壳和那行 import,
    # 换成一个假的父类。
    # **YAML 的块标量已经把公共缩进剥掉了** —— 类定义那行顶格,方法体 8 格。
    # 不要再 dedent 一次(第一版那么干,报 IndentationError)。
    body = src[src.index("class WeComWebhookNotification"):]
    end = body.find("# Webhook 通知默认是关的")
    if end > 0:
        body = body[:end]
    body = body.replace("(WebhookNotification)", "(_FakeParent)")

    ns = {"_FakeParent": type("_FakeParent", (), {})}
    exec(compile(body, str(COMPONENT), "exec"), ns)
    return ns["WeComWebhookNotification"]


class _Content:
    def __init__(self, **kw):
        self.name = kw.get("name", "")
        self.description = kw.get("description", "")
        self.text = kw.get("text", "")
        self.url = kw.get("url", "")


def make(**kw):
    cls = load_notification_class()
    obj = cls.__new__(cls)
    obj._content = _Content(**kw)
    return obj


class Test企微payload形状:
    def test_必须是_markdown_消息(self):
        """字段名错一个企微就拒收,而 Superset 这边看不出问题。"""
        p = make(name="告警").  _get_req_payload()
        assert p["msgtype"] == "markdown"
        assert "markdown" in p and "content" in p["markdown"]

    def test_标题加粗(self):
        p = make(name="磁盘使用率告警")._get_req_payload()
        assert "**磁盘使用率告警**" in p["markdown"]["content"]

    def test_描述和正文都带上(self):
        p = make(name="n", description="盘要满了", text="pct=91%")._get_req_payload()
        c = p["markdown"]["content"]
        assert "盘要满了" in c and "pct=91%" in c

    def test_链接能跳回_superset(self):
        p = make(name="n", url="http://superset.test/alert/list/")._get_req_payload()
        assert "http://superset.test/alert/list/" in p["markdown"]["content"]

    def test_正文超长要截断(self):
        """**企微单条上限 4096 字节,超长是整条发送失败,不是被截断** ——
        那样告警就丢了,而且丢得没有声音。"""
        p = make(name="n", text="x" * 9000)._get_req_payload()
        assert len(p["markdown"]["content"]) < 4096

    def test_可选字段为空时不出现空行(self):
        p = make(name="只有标题")._get_req_payload()
        assert p["markdown"]["content"] == "**只有标题**"


class Test附件:
    def test_不返回附件(self):
        """**企微机器人不接受 multipart。** 父类在有 files 时会改用 multipart
        发送,企微直接拒收 —— 返回 None 让它始终走 JSON 那条分支。

        代价是截图/CSV 发不出去,只能靠消息里的链接跳回 Superset 看。
        这是企微机器人本身的限制,不是实现问题。
        """
        assert make(name="n")._get_files() is None
