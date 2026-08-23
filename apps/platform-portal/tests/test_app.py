"""platform-portal 的测试——见 docs/BACKLOG.md P1"三个自建 Flask 工具补
测试"那条。这个 app 唯一有实际逻辑的部分是 probe()(现场探测工具是否
在线)和 index() 路由(按 category 分组、把探测结果塞进模板),测的是
这两块,不测 Jinja 模板渲染出的具体 HTML 细节(那属于样式,不是逻辑)。

跑法:
  cd apps/platform-portal && python3 -m pytest tests/ -v
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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


# =============================================================== ADR-067
# 门户从"链接目录"改成"工作台"之后新增的三块:并发探测、队列用量、
# 我的作业。这三块的共同风险不是"算错",是**取不到数据时把整个页面搞挂**
# ——门户是"哪里都进不去时最后还能打开的那个页面",所以下面的测试有一半
# 在测降级路径,不是测正常路径。


class TestProbeAll:
    def test_并发探测覆盖每一个工具且结果对得上号(self):
        with patch.object(portal, "probe", side_effect=lambda t: t["name"] == "Trino"):
            result = portal.probe_all(portal.TOOLS)
        assert set(result) == {t["name"] for t in portal.TOOLS}
        assert result["Trino"] is True
        assert result["Superset"] is False

    def test_单个工具探测抛异常不能拖垮整页(self):
        """probe() 自己已经吞了 requests 的异常,但如果将来有人在里面加了
        别的会抛异常的逻辑,线程池会把异常抛回主线程、整个页面 500。
        这条测试锁住"一个工具炸了,其它工具照常出结果"。"""
        def flaky(tool):
            if tool["name"] == "Trino":
                raise RuntimeError("探测逻辑里出了意料之外的错")
            return True
        with patch.object(portal, "probe", side_effect=flaky):
            result = portal.probe_all(portal.TOOLS)
        assert result["Trino"] is False
        assert result["Superset"] is True


class TestQueueUsage:
    def test_读不到集群时返回错误说明而不是抛异常(self):
        with patch.object(portal, "_k8s", side_effect=RuntimeError("没有 kubeconfig")):
            out = portal.queue_usage()
        assert out["error"] and out["rows"] == []

    def test_把标称配额已用和借用量都解析出来(self):
        fake = {"items": [{
            "metadata": {"name": "platform-team"},
            "spec": {"resourceGroups": [{"flavors": [{"resources": [
                {"name": "cpu", "nominalQuota": "2"},
                {"name": "memory", "nominalQuota": "4Gi"}]}]}]},
            "status": {"pendingWorkloads": 1, "flavorsUsage": [{"resources": [
                {"name": "cpu", "total": "3", "borrowed": "1"},
                {"name": "memory", "total": "4Gi", "borrowed": "0"}]}]},
        }]}
        api = MagicMock()
        api.list_cluster_custom_object.return_value = fake
        with patch.object(portal, "_k8s", return_value=api):
            out = portal.queue_usage()
        row = out["rows"][0]
        # 借用量是这一栏存在的全部意义:用户要看的就是"我们组现在是不是
        # 在借别人的",配额和已用两个数字自己是看不出这件事的
        assert (row["cpu_quota"], row["cpu_used"], row["cpu_borrowed"]) == ("2", "3", "1")
        assert row["label"] == "平台组"
        assert row["pending"] == 1


class TestMyJobs:
    def test_没识别出用户时不去查集群(self):
        with patch.object(portal, "_k8s") as k8s:
            out = portal.my_jobs("")
        assert out["rows"] == [] and out["error"]
        k8s.assert_not_called()

    def test_按提交人标签过滤而不是列出所有作业(self):
        api = MagicMock()
        api.list_namespaced_custom_object.return_value = {"items": []}
        with patch.object(portal, "_k8s", return_value=api):
            portal.my_jobs("zhenghe")
        _, kwargs = api.list_namespaced_custom_object.call_args
        assert kwargs["label_selector"] == "platform-sdk/submitted-by=zhenghe"

    def test_按时间倒序且没有_status_时算_Pending(self):
        api = MagicMock()
        api.list_namespaced_custom_object.return_value = {"items": [
            {"metadata": {"name": "old", "creationTimestamp": "2026-08-01T00:00:00Z"},
             "status": {"phase": "Succeeded"}},
            {"metadata": {"name": "new", "creationTimestamp": "2026-08-22T00:00:00Z"}},
        ]}
        with patch.object(portal, "_k8s", return_value=api):
            out = portal.my_jobs("zhenghe")
        assert [r["name"] for r in out["rows"]] == ["new", "old"]
        assert out["rows"][0]["phase"] == "Pending"

    def test_读不到时返回错误说明而不是抛异常(self):
        with patch.object(portal, "_k8s", side_effect=RuntimeError("RBAC 没配")):
            out = portal.my_jobs("zhenghe")
        assert out["error"] and out["rows"] == []


class TestIndexDegradation:
    """**这组是这次改造最重要的测试。** 门户加了两个依赖集群 API 的区块,
    如果它们挂掉会连累整个页面打不开,那这次改造就是净损失——原来那个
    链接目录至少永远能打开。"""

    def test_集群完全连不上时首页仍然是_200_且工具入口还在(self, client):
        with patch.object(portal, "probe", return_value=True), \
             patch.object(portal, "_k8s", side_effect=RuntimeError("集群连不上")):
            resp = client.get("/", headers={"X-Forwarded-User": "zhenghe"})
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Trino" in body and "Superset" in body   # 工具入口没受影响
        assert "读不到" in body                          # 而且明说了取不到,不是假装没这回事
