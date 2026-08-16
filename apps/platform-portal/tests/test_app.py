"""platform-portal 的测试——见 docs/BACKLOG.md P1"三个自建 Flask 工具补
测试"那条。这个 app 唯一有实际逻辑的部分是 probe()(现场探测工具是否
在线)和 index() 路由(按 category 分组、把探测结果塞进模板),测的是
这两块,不测 Jinja 模板渲染出的具体 HTML 细节(那属于样式,不是逻辑)。

跑法:
  cd apps/platform-portal && python3 -m pytest tests/ -v
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import app as portal  # noqa: E402


@pytest.fixture
def client():
    portal.app.config["TESTING"] = True
    with portal.app.test_client() as c:
        yield c


class TestProbe:
    """probe() 决定门户上每个工具显示绿点还是灰点,这是这个 app 唯一的
    "业务逻辑",错了会直接误导用户以为工具在线/离线。"""

    def test_probe_returns_true_when_reachable(self):
        tool = {"probe": "http://example.internal/health"}
        with patch.object(portal.requests, "get") as mock_get:
            mock_get.return_value = None  # 只要不抛异常就算能连上
            assert portal.probe(tool) is True
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            # 超时必须够短,不能让一个连不上的工具拖慢整个门户页面
            assert kwargs["timeout"] <= 2

    def test_probe_returns_false_on_connection_error(self):
        tool = {"probe": "http://example.internal/health"}
        with patch.object(portal.requests, "get", side_effect=requests.ConnectionError()):
            assert portal.probe(tool) is False

    def test_probe_returns_false_on_timeout(self):
        tool = {"probe": "http://example.internal/health"}
        with patch.object(portal.requests, "get", side_effect=requests.Timeout()):
            assert portal.probe(tool) is False

    def test_probe_respects_probe_verify_false(self):
        """Trino 那一项配了 probe_verify: False(自签证书,见 TOOLS 定义),
        确认这个开关真的传到了 requests.get,不是摆设。"""
        tool = {"probe": "https://trino.internal:8443/v1/info", "probe_verify": False}
        with patch.object(portal.requests, "get") as mock_get:
            portal.probe(tool)
            _, kwargs = mock_get.call_args
            assert kwargs["verify"] is False

    def test_probe_defaults_verify_true(self):
        """没配 probe_verify 的工具,默认应该校验证书,不能悄悄放松安全性。"""
        tool = {"probe": "https://superset.internal:8088/health"}
        with patch.object(portal.requests, "get") as mock_get:
            portal.probe(tool)
            _, kwargs = mock_get.call_args
            assert kwargs["verify"] is True


class TestToolsData:
    """TOOLS 这个清单本身的结构完整性——漏了字段会在渲染模板或者 probe()
    时才炸,不如提前测出来。"""

    def test_every_tool_has_required_fields(self):
        required = {"category", "name", "description", "url", "probe"}
        for tool in portal.TOOLS:
            missing = required - tool.keys()
            assert not missing, f"{tool.get('name', '<unnamed>')} 缺字段: {missing}"

    def test_no_duplicate_tool_names(self):
        names = [t["name"] for t in portal.TOOLS]
        assert len(names) == len(set(names)), "TOOLS 里有重名,门户上会显示两张一样的卡片"


class TestRoutes:
    def test_healthz(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}

    def test_index_groups_by_category_and_shows_username(self, client):
        with patch.object(portal, "probe", return_value=True):
            resp = client.get("/", headers={"X-Forwarded-User": "zhenghe"})
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "zhenghe" in body
        # 每个 category 至少出现一次,确认分组逻辑真的跑起来了,不是空的
        for category in {t["category"] for t in portal.TOOLS}:
            assert category in body

    def test_index_without_forwarded_user_header(self, client):
        """oauth2-proxy 没有正确配置转发头这种边界情况,不应该直接 500。"""
        with patch.object(portal, "probe", return_value=False):
            resp = client.get("/")
        assert resp.status_code == 200

    def test_index_marks_down_tools_correctly(self, client):
        """注意:'dot-up'/'dot-down' 这两个类名本来就写在页面顶部的 <style>
        CSS 规则里,不能拿字符串"在不在正文里"来判断状态——要看卡片元素
        (class="dot ...")实际用的是哪个类,不是整页搜字符串。"""
        with patch.object(portal, "probe", return_value=False):
            resp = client.get("/")
        body = resp.get_data(as_text=True)
        card_dots = [line for line in body.splitlines() if 'class="dot ' in line]
        assert card_dots, "页面里没找到任何卡片状态点"
        assert all("dot-down" in line for line in card_dots)
        assert not any("dot-up" in line for line in card_dots)
