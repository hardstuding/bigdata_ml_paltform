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


class TestApplyPortSuffix:
    """2026-08-16 真实故障的回归测试:门户列出的工具链接全部没带端口,
    cloud-full 的 ingress-nginx 是 NodePort,点开必然 404。这组测试锁住
    "配了后缀就要出现在 host 和 path 之间、没配就原样不变"这个行为,
    避免以后改坏。"""

    def test_no_suffix_configured_leaves_url_unchanged(self):
        assert portal.apply_port_suffix("http://trino.local-lite.test", "", "") == "http://trino.local-lite.test"

    def test_http_suffix_inserted_after_host(self):
        assert portal.apply_port_suffix("http://argocd.local-lite.test", ":32460", "") == "http://argocd.local-lite.test:32460"

    def test_https_suffix_uses_https_suffix_not_http_suffix(self):
        result = portal.apply_port_suffix("https://trino.local-lite.test", ":32460", ":32535")
        assert result == "https://trino.local-lite.test:32535"

    def test_http_url_ignores_https_suffix(self):
        result = portal.apply_port_suffix("http://argocd.local-lite.test", "", ":32535")
        assert result == "http://argocd.local-lite.test"

    def test_every_tool_url_gets_correct_suffix_by_scheme(self):
        """模拟部署时设的环境变量组合(cloud-full 用的那两个值),挨个检查
        每个工具原始 url 套用 apply_port_suffix 之后,http 的带 :32460、
        https 的带 :32535——不是只测函数本身,是确认覆盖了完整的 13 个
        工具,不会有漏网之鱼。"""
        for tool in portal.TOOLS:
            result = portal.apply_port_suffix(tool["url"], ":32460", ":32535")
            expected_suffix = ":32535" if tool["url"].startswith("https://") else ":32460"
            assert result.endswith(expected_suffix), f"{tool['name']} 的 url 应该带上端口后缀"

    def test_trino_uses_https_scheme(self):
        """Trino 实际部署走 HTTPS(apps/trino-tls/),之前这里写成了
        http,是这次顺带发现修掉的真实 bug,不是这次的主线,单独锁住
        避免回退。"""
        trino = next(t for t in portal.TOOLS if t["name"] == "Trino")
        assert trino["url"].startswith("https://")
