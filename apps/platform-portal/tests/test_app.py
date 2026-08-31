"""platform-portal 的测试——见 docs/project/roadmap.md P1"三个自建 Flask 工具补
测试"那条。这个 app 唯一有实际逻辑的部分是 probe()(现场探测工具是否
在线)和 index() 路由(按 category 分组、把探测结果塞进模板),测的是
这两块,不测 Jinja 模板渲染出的具体 HTML 细节(那属于样式,不是逻辑)。

跑法:
  cd apps/platform-portal && python3 -m pytest tests/ -v
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import contextlib

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
        assert all("dot down" in line for line in card_dots)
        assert not any("dot up" in line for line in card_dots)


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


# ------------------------------------------------------------ 黄金链路
# 门户原来只回答"工具在不在线",这一块回答"一件真实的事做不做得成"。
# 下面几条锁住它的**降级行为**:这一栏的数据来自 Prometheus,而门户是
# "哪里都进不去时最后还能打开的那个页面"——它绝不能因为 Prometheus 挂了
# 就打不开。

class TestGoldenPaths:
    def test_prometheus_连不上时整块降级_不抛异常(self):
        with patch.object(portal.urllib.request, "urlopen",
                          side_effect=OSError("connection refused")):
            g = portal.golden_paths()
        assert g["error"] and g["rows"] == []

    def test_三态要分开_从来没跑过不等于断了(self):
        # 只返回 query 一条,另外两条没有数据
        payload = json.dumps({"data": {"result": [
            {"metric": {"cronjob": "goldenpath-query"}, "value": [0, "120"]}]}}).encode()
        with patch.object(portal.urllib.request, "urlopen",
                          return_value=_FakeResp(payload)):
            rows = {r["label"]: r for r in portal.golden_paths()["rows"]}
        assert rows["查数据"]["state"] == "ok"
        # 没有数据的两条是 unknown 而不是 broken:多半是刚部署/探针没起来,
        # 和"链路真的断了"该分开显示。
        assert rows["实时数据"]["state"] == "unknown"
        assert rows["数据目录"]["state"] == "unknown"

    def test_超过阈值算断了_而且阈值和告警一致(self):
        payload = json.dumps({"data": {"result": [
            {"metric": {"cronjob": "goldenpath-query"},
             "value": [0, str(portal.GOLDEN_PATH_STALE_SEC + 1)]}]}}).encode()
        with patch.object(portal.urllib.request, "urlopen",
                          return_value=_FakeResp(payload)):
            rows = {r["label"]: r for r in portal.golden_paths()["rows"]}
        assert rows["查数据"]["state"] == "broken"
        # 门户显红而 GoldenPathBroken 告警不响(或反过来)会让人不知道信哪个
        assert portal.GOLDEN_PATH_STALE_SEC == 3600

    def test_时间显示是人话(self):
        assert portal._human_ago(30) == "刚刚"
        assert portal._human_ago(120) == "2 分钟前"
        assert portal._human_ago(3660) == "1 小时 1 分钟前"


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestRenderedContent:
    """**渲染级测试:断言值真的出现在 HTML 里,不只是函数返回对了。**

    加这组的直接原因(2026-08-29):模板里写的是 `j.status` / `j.at`,而
    `my_jobs()` 返回的是 `phase` / `started`。**Jinja 对未定义变量渲染成空
    字符串、不报错**,所以页面上"我的作业"的状态和时间一直是空白的,而
    当时 30 个测试全绿——它们测的是 `my_jobs()` 这个函数,不是页面。

    教训一般化:**只要模板和后端之间靠字段名约定,就必须有一条测试跨过
    这个边界。**下面每条都是"某个真实值必须出现在最终 HTML 里"。
    """

    def _html(self, client, **patches):
        """渲染一次首页。没显式给的那几个数据源都打桩成空,免得测试依赖集群。"""
        defaults = {
            "golden_paths": {"error": None, "rows": [], "healthy": 0, "total": 0},
            "queue_usage": {"error": None, "rows": [], "pending_total": 0},
            "my_jobs": {"error": None, "rows": []},
            "streams": {"error": None, "rows": []},
        }
        defaults.update(patches)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(portal, "probe", return_value=True))
            for name, value in defaults.items():
                stack.enter_context(patch.object(portal, name, return_value=value))
            return client.get("/").get_data(as_text=True)

    def test_我的作业的状态和时间真的出现在页面上(self, client):
        jobs = {"error": None, "rows": [
            {"name": "train-abc123", "phase": "Succeeded",
             "started": "2026-08-29T10:00:00Z", "queue": "train-model"},
        ]}
        html = self._html(client, my_jobs=jobs)
        assert "train-abc123" in html
        assert "Succeeded" in html, "作业状态没有渲染出来(模板字段名和后端对不上?)"
        assert "2026-08-29T10:00:00Z" in html, "提交时间没有渲染出来"

    def test_队列配额的数字真的出现在页面上(self, client):
        queues = {"error": None, "pending_total": 3, "rows": [
            {"label": "分析组", "name": "data-analysts", "cpu_quota": "2",
             "cpu_used": "1", "cpu_borrowed": "0", "mem_quota": "8Gi", "pending": 3},
        ]}
        html = self._html(client, queue_usage=queues)
        assert "data-analysts" in html
        assert "8Gi" in html, "配额数字没渲染出来"

    def test_链路状态的标签和时间真的出现在页面上(self, client):
        golden = {"error": None, "healthy": 1, "total": 1, "rows": [
            {"label": "查数据", "chain": "Trino → Iceberg", "state": "ok", "ago": "3 分钟前"},
        ]}
        html = self._html(client, golden_paths=golden)
        assert "查数据" in html
        assert "3 分钟前" in html, "链路的时间没渲染出来"

    def test_流作业的状态真的出现在页面上(self, client):
        st = {"error": None, "rows": [
            {"name": "device-events-stream", "job_state": "RUNNING",
             "jm_state": "READY", "ok": True},
        ]}
        html = self._html(client, streams=st)
        assert "device-events-stream" in html
        assert "RUNNING" in html, "流作业状态没渲染出来"

    def test_后端换字段名会让测试变红(self, client):
        """**这条是元测试**:确认上面那几条不是摆设。

        故意给一个字段名不对的作业记录,页面上就不该出现那个值。如果这条
        挂了,说明断言写得太松(比如断言的字符串恰好在页面别处出现)。
        """
        jobs = {"error": None, "rows": [
            {"name": "j1", "wrong_field": "SHOULD_NOT_APPEAR", "phase": "", "started": ""},
        ]}
        html = self._html(client, my_jobs=jobs)
        assert "SHOULD_NOT_APPEAR" not in html


class TestUrlsComeFromConfig:
    """**工具地址必须由环境配置拼出来,不能写死域名。**

    2026-08-29 之前 TOOLS 里每一项的 url 都是 `xxx.local-lite.test` 硬编码
    (16 处)。后果:prod 部署之后门户上**每一个链接都指向一个不存在的域名**
    ——而门户恰恰是新用户进平台看到的第一个页面,第一印象就是全是死链。

    这组测试直接用三档环境的真实配置值跑一遍。
    """

    # 和 environments/<env>/config.yaml 保持一致。写死在这里是有意的:
    # 如果哪天那些配置变了而这里没变,测试会因为"预期值对不上"而红,
    # 那正是要的——它逼人回来确认改动是不是有意的。
    ENVS = {
        "local-lite": dict(domain="local-lite.test", scheme="http",
                           http_suffix=":32460", https_suffix=":32535"),
        "cloud-full": dict(domain="local-lite.test", scheme="http",
                           http_suffix=":32460", https_suffix=":32535"),
        "prod": dict(domain="your-real-domain.example.com", scheme="https",
                     http_suffix="", https_suffix=""),
    }

    def test_三档环境各自拼出正确的地址(self):
        for env, cfg in self.ENVS.items():
            for tool in portal.TOOLS:
                url = portal.build_url(tool, **cfg)
                assert cfg["domain"] in url, f"{env} 的 {tool['name']} 地址里没有该环境的域名:{url}"
                assert url.startswith(("http://", "https://")), url

    def test_prod_里不能出现_local_lite(self):
        """**Codex 评审明确点名的验收条件。**"""
        for tool in portal.TOOLS:
            url = portal.build_url(tool, **self.ENVS["prod"])
            assert "local-lite" not in url, f"prod 地址里还有 local-lite:{url}"

    def test_prod_不带_NodePort_端口后缀(self):
        """prod 走标准 443,带上 :32460 这种端口是错的。"""
        for tool in portal.TOOLS:
            url = portal.build_url(tool, **self.ENVS["prod"])
            assert ":32460" not in url and ":32535" not in url, url

    def test_trino_永远是_https(self):
        """Trino 自己有一份 TLS Ingress(apps/trino-tls/),和环境的
        external_scheme 无关。2026-08-16 真实踩过:这里写成 http,点开就是错的。"""
        trino = [t for t in portal.TOOLS if t["name"] == "Trino"][0]
        for env, cfg in self.ENVS.items():
            assert portal.build_url(trino, **cfg).startswith("https://"), env

    def test_每个工具都声明了_host(self):
        """漏了 host 的话 build_url 会 KeyError —— 让它在测试里炸,
        而不是在用户打开门户的时候炸。"""
        for tool in portal.TOOLS:
            assert tool.get("host"), f"{tool['name']} 没有 host 字段"

    def test_渲染出来的页面里没有硬编码域名(self, client):
        """跨过模板边界再验一次:页面 HTML 里出现的地址应该是配置拼的。"""
        with patch.object(portal, "probe", return_value=True), \
             patch.object(portal, "golden_paths", return_value={"error": None, "rows": [], "healthy": 0, "total": 0}), \
             patch.object(portal, "queue_usage", return_value={"error": None, "rows": [], "pending_total": 0}), \
             patch.object(portal, "my_jobs", return_value={"error": None, "rows": []}), \
             patch.object(portal, "streams", return_value={"error": None, "rows": []}):
            html = client.get("/").get_data(as_text=True)
        for tool in portal.TOOLS:
            assert tool["url"] in html, f"{tool['name']} 的链接没出现在页面上"


class TestLogos:
    """图标要么真的内联进页面,要么明确回退成首字母 —— 不能悄悄空着。

    2026-08-29 加图标时的具体风险:文件加进 src/logos/ 但忘了在 Dockerfile
    里 COPY,本地跑得好好的、镜像里那一栏全是空白,**而且不报错**。
    """

    def _html(self, client):
        with patch.object(portal, "probe", return_value=True), \
             patch.object(portal, "golden_paths", return_value={"error": None, "rows": [], "healthy": 0, "total": 0}), \
             patch.object(portal, "queue_usage", return_value={"error": None, "rows": [], "pending_total": 0}), \
             patch.object(portal, "my_jobs", return_value={"error": None, "rows": []}), \
             patch.object(portal, "streams", return_value={"error": None, "rows": []}):
            return client.get("/").get_data(as_text=True)

    def test_有图标的工具真的内联了_svg(self, client):
        html = self._html(client)
        with_logo = [t for t in portal.TOOLS if t.get("logo") and portal.LOGOS.get(t["logo"])]
        assert with_logo, "一个图标都没加载到(src/logos/ 空了?Dockerfile 没 COPY?)"
        assert html.count('<svg class="logo"') >= len(with_logo)

    def test_没有图标的工具回退成首字母(self, client):
        html = self._html(client)
        for t in portal.TOOLS:
            if not (t.get("logo") and portal.LOGOS.get(t["logo"])):
                assert f'<span class="mono">{t["name"][0]}</span>' in html, \
                    f"{t['name']} 既没有图标也没有回退首字母,那一格是空的"

    def test_图标用_currentColor_不写死颜色(self):
        """写死颜色的话深色模式下会糊成一团。"""
        for name, svg in portal.LOGOS.items():
            assert 'fill="currentColor"' in svg, f"{name} 没有跟随主题"

    def test_dockerfile_里_copy_了_logos_目录(self):
        """**这条防的是"加了文件但镜像里没有"**:本地全绿、线上空白、不报错。"""
        dockerfile = (Path(portal.__file__).resolve().parent.parent / "Dockerfile").read_text()
        assert "COPY src/logos/" in dockerfile, "Dockerfile 里没有 COPY logos 目录"


class TestSqlWorkbenchEntry:
    """ADR-084:门户曾经把 Trino Web UI 当 SQL 工作台介绍,那是错的。

    Trino 的界面没有 SQL 编辑器,进去写不了 SQL。下面几条锁住的不是措辞
    好不好听,是"门户上写的东西和它实际是什么相符"——分析师是照着这句
    话点进去的。
    """

    def _tool(self, name):
        return next(t for t in portal.TOOLS if t["name"] == name)

    def test_有一个真正的_sql_工作台入口(self):
        t = self._tool("SQL 工作台")
        assert t["host"] == "superset"
        assert t["path"] == "/sqllab/"

    @pytest.mark.parametrize("谎话", ["交互式 SQL", "SQL 编辑", "写 SQL"])
    def test_trino_不再自称能写_sql(self, 谎话):
        assert 谎话 not in self._tool("Trino")["description"]

    def test_带_path_的工具_端口后缀插在_path_前面(self):
        # 拼反了会得到 http://superset.x/sqllab/:32460 这种打不开的地址,
        # 而且在 local-lite(没有端口后缀)上根本测不出来——只有 cloud-full
        # 这种 NodePort 环境才会暴露,正是 2026-08-16 那次"点哪个链接都
        # 404"的翻版。
        url = portal.build_url(self._tool("SQL 工作台"), domain="example.test",
                               scheme="http", http_suffix=":32460", https_suffix="")
        assert url == "http://superset.example.test:32460/sqllab/"

    def test_不带_path_的工具不受影响(self):
        url = portal.build_url(self._tool("Airflow"), domain="example.test",
                               scheme="http", http_suffix=":32460", https_suffix="")
        assert url == "http://airflow.example.test:32460"


class TestQueryTableRedirect:
    """/query/<catalog>/<schema>/<table> —— 数据目录一键跳查询的落脚点。"""

    def test_深链造不出来时降级到空的_sql_lab_而不是报错(self):
        # 凭据没配是常态(local-lite 上就没配),这条路径必须是 302 不是 500。
        with patch.object(portal.sqllab, "table_query_link",
                          side_effect=portal.sqllab.SqlLabLinkUnavailable("没配")):
            resp = portal.app.test_client().get("/query/iceberg/demo/orders")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/sqllab/")

    def test_深链可用时跳到_permalink(self):
        with patch.object(portal.sqllab, "table_query_link",
                          return_value="/sqllab/p/abc/"):
            resp = portal.app.test_client().get("/query/iceberg/demo/orders")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/sqllab/p/abc/")

    def test_跳转地址跟随环境配置_不写死域名(self):
        # 门户上每个链接都必须来自环境配置,2026-08-16 那次"点哪个链接都
        # 404"就是写死域名导致的。
        with patch.object(portal.sqllab, "table_query_link",
                          return_value="/sqllab/p/abc/"):
            resp = portal.app.test_client().get("/query/iceberg/demo/orders")
        loc = resp.headers["Location"]
        assert loc.startswith(portal._SQLLAB_BASE)
        assert "superset" in loc

    def test_表名原样传给深链构造_不被路由吃掉(self):
        with patch.object(portal.sqllab, "table_query_link",
                          return_value="/sqllab/p/abc/") as m:
            portal.app.test_client().get("/query/iceberg/demo/daily_order_totals")
        assert m.call_args[0] == ("iceberg", "demo", "daily_order_totals")


class TestDockerfileShipsEverything:
    def test_sqllab_模块被拷进镜像(self):
        # app.py 顶上 `import sqllab`,漏拷这行镜像起不来。和 logos 那条
        # 一个道理:本地全绿、镜像里炸。
        df = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text()
        assert "COPY src/sqllab.py" in df


class TestRoleWorkbench:
    """角色工作台第一块:我的表权限 / 待我审批(roadmap P1.5)。

    **这些测试断言的是渲染出来的 HTML,不是函数返回值。** 2026-08-29 踩过
    一次:后端返回 `phase`/`started`、模板读 `status`/`at`,Jinja 对未定义
    变量渲染成空字符串、不报错,30 个测试全绿而页面上那一栏一直是空白。
    只测函数返回值挡不住这类 bug。
    """

    def _render(self, permissions=None, approvals=None, username="alice"):
        with patch.object(portal, "my_permissions",
                          return_value=permissions or {"available": False, "grants": [],
                                                       "expiring_soon": []}), \
             patch.object(portal, "my_approvals",
                          return_value=approvals or {"available": False, "pending": [],
                                                     "overdue": []}), \
             patch.object(portal, "probe_all", return_value={}), \
             patch.object(portal, "queue_usage", return_value={"error": None, "rows": [],
                                                               "pending_total": 0}), \
             patch.object(portal, "my_jobs", return_value={"error": None, "rows": []}), \
             patch.object(portal, "golden_paths", return_value={"error": None, "rows": [],
                                                                "ok": 0, "total": 0}), \
             patch.object(portal, "streams", return_value={"error": None, "rows": []}):
            portal.app.config["TESTING"] = True
            resp = portal.app.test_client().get("/", headers={"X-Forwarded-User": username})
        return resp.get_data(as_text=True)

    def test_权限数据渲染进页面_不是空白(self):
        html = self._render(permissions={
            "available": True,
            "grants": [{"table": "iceberg.demo.orders", "security_level": "2",
                        "expires_at": "2026-12-01T00:00:00+00:00", "days_left": 94,
                        "expiring_soon": False}],
            "expiring_soon": [],
        })
        assert "我的表权限" in html
        assert "iceberg.demo.orders" in html      # 表名真的出现在 HTML 里
        assert "2026-12-01" in html               # 到期日期也是

    def test_快到期的会被标出来(self):
        html = self._render(permissions={
            "available": True,
            "grants": [{"table": "t.soon", "security_level": "1", "expires_at": "",
                        "days_left": 5, "expiring_soon": True}],
            "expiring_soon": [{"table": "t.soon"}],
        })
        assert "1 项即将到期" in html
        assert "还剩 5 天" in html
        assert "warn-row" in html

    def test_长期授权显示为长期_不显示_none(self):
        # days_left 是 None 时直接渲染会印出 "None",很难看也让人以为是 bug
        html = self._render(permissions={
            "available": True,
            "grants": [{"table": "t.forever", "security_level": "1", "expires_at": "",
                        "days_left": None, "expiring_soon": False}],
            "expiring_soon": [],
        })
        assert "长期" in html
        assert "None" not in html

    def test_不是审批人就没有待我审批这一栏(self):
        html = self._render()
        assert "待我审批" not in html

    def test_审批人看得到待办和已等多久(self):
        html = self._render(approvals={
            "available": True,
            "pending": [{"applicant": "bob", "table": "iceberg.demo.secret",
                         "role": "直属上级", "waiting_hours": 72, "overdue": True,
                         "step_id": 1, "request_id": 1, "security_level": 3,
                         "reason": "r"}],
            "overdue": [{"step_id": 1}],
        })
        assert "待我审批" in html
        assert "bob" in html
        assert "iceberg.demo.secret" in html
        assert "3 天" in html          # 72 小时要显示成天,不是 "72 小时"
        assert "1 项已超时" in html

    def test_权限服务挂了整块不显示_而不是整页报错(self):
        # permission-request-app 不可用是常态(local-lite 上就没有 token)
        html = self._render()
        assert "我的表权限" not in html
        assert "平台" in html          # 页面本身照常渲染


class TestPermApiClient:
    def test_没配_token_直接返回_none_不发请求(self):
        with patch.object(portal, "PERM_APP_TOKEN", ""):
            with patch.object(portal.urllib.request, "urlopen") as m:
                assert portal._perm_api("/api/my-permissions", "alice") is None
                m.assert_not_called()

    def test_上游报错时降级成不可用_不抛异常(self):
        with patch.object(portal, "PERM_APP_TOKEN", "t"), \
             patch.object(portal.urllib.request, "urlopen",
                          side_effect=OSError("connection refused")):
            assert portal.my_permissions("alice")["available"] is False
            assert portal.my_approvals("alice")["available"] is False

    def test_带上内部_token_并且用户名做了_url_编码(self):
        import io
        captured = {}

        def fake(req, timeout=None):
            captured["url"] = req.full_url
            captured["token"] = req.get_header("X-internal-token")
            return io.BytesIO(b'{"grants": [], "expiring_soon": []}')

        with patch.object(portal, "PERM_APP_TOKEN", "tok"), \
             patch.object(portal.urllib.request, "urlopen", fake):
            portal.my_permissions("li ming")
        assert captured["token"] == "tok"
        assert "li%20ming" in captured["url"]


# ---------------------------------------------------------------------------
# 作业详情页(roadmap P1.5「门户升级成角色工作台」的后半)
#
# 这一组测试里**最重要的是归属检查那几条**。门户加了 workflows 的
# patch/create 和 pods/log 的读权限,而门户是所有登录用户都能打开的页面 ——
# 挡住"操作别人作业"的唯一一道闸就在应用层。
# ---------------------------------------------------------------------------
def _wf(name="job-abc", owner="alice", phase="Failed", message="exit code 1"):
    return {
        "metadata": {"name": name, "labels": {"platform-sdk/submitted-by": owner}},
        "spec": {
            "arguments": {"parameters": [{"name": "date", "value": "2026-08-29"}]},
            "serviceAccountName": "argo-workflow",
            "templates": [{
                "name": "main",
                "metadata": {"labels": {"kueue.x-k8s.io/queue-name": "data-analysts"}},
                "container": {"image": "local/platform-runtime:0.1.0",
                              "command": ["python3", "/scripts/x.py"],
                              "resources": {"requests": {"cpu": "500m"}}},
            }],
        },
        "status": {
            "phase": phase, "message": message,
            "startedAt": "2026-08-29T01:00:00Z", "finishedAt": "2026-08-29T01:02:00Z",
            "nodes": {
                "n1": {"type": "DAG", "displayName": "job-abc", "phase": phase},
                "n2": {"type": "Pod", "id": "job-abc-123", "displayName": "main",
                       "phase": phase, "message": message,
                       "startedAt": "2026-08-29T01:00:10Z"},
            },
        },
    }


class TestJobDetailOwnership:
    def _get(self, path, user="alice", wf=None, exc=None):
        api = MagicMock()
        if exc:
            api.get_namespaced_custom_object.side_effect = exc
        else:
            api.get_namespaced_custom_object.return_value = wf
        with patch.object(portal, "_k8s", return_value=api):
            portal.app.config["TESTING"] = True
            return portal.app.test_client().get(path, headers={"X-Forwarded-User": user}), api

    def test_自己的作业能打开(self):
        resp, _ = self._get("/job/job-abc", "alice", _wf())
        assert resp.status_code == 200

    def test_别人的作业打不开(self):
        resp, _ = self._get("/job/job-abc", "bob", _wf(owner="alice"))
        assert resp.status_code == 404

    def test_不存在和不是你的_给同一句话(self):
        # 不区分这两种情况,免得拿这个接口去探测别人的作业名。
        a, _ = self._get("/job/job-abc", "bob", _wf(owner="alice"))
        b, _ = self._get("/job/nope", "bob", exc=RuntimeError("not found"))
        assert a.get_data(as_text=True).count("不是你提交的") == 1
        assert b.get_data(as_text=True).count("不是你提交的") == 1

    def test_没识别出登录用户时不放行(self):
        resp, _ = self._get("/job/job-abc", "", _wf())
        assert resp.status_code == 404

    def test_别人的日志读不到(self):
        resp, _ = self._get("/job/job-abc/logs/job-abc-123", "bob", _wf(owner="alice"))
        assert resp.status_code == 404

    def test_日志按原始字节解码_不是_bytes_的_repr(self, monkeypatch):
        """2026-08-30 实机踩到:kubernetes 客户端默认返回的是一个 **str,
        内容却是 bytes 的 repr** —— 页面上显示成
        `b'\\xe5\\xa4\\x84...'` 这种一整坨转义序列。纯英文日志看着只是
        "多了个 b' 前缀",**中文日志完全不可读**,而这个平台的作业输出
        基本都是中文。

        所以这条盯的是"走没走 _preload_content=False 那条路":
        断言拿到的是真正的中文,而且没有 bytes 字面量的痕迹。
        """
        # 测试环境里没装 kubernetes 客户端(`_pod_logs` 是函数内 import),
        # 塞一个假模块进去 —— 不装真依赖,是为了让这套测试在任何机器上
        # 都能跑,和这个仓库里其它 Flask 应用的测试一致。
        fake_resp = MagicMock()
        fake_resp.data = "处理了 13 张表\n".encode("utf-8")
        core = MagicMock()
        core.read_namespaced_pod_log.return_value = fake_resp
        fake_client = MagicMock()
        fake_client.CoreV1Api.return_value = core
        fake_k8s = MagicMock()
        fake_k8s.client = fake_client
        monkeypatch.setitem(sys.modules, "kubernetes", fake_k8s)
        monkeypatch.setitem(sys.modules, "kubernetes.client", fake_client)
        monkeypatch.setattr(portal, "_k8s", lambda: MagicMock())
        out = portal._pod_logs("some-pod")
        assert out == "处理了 13 张表\n"
        assert not out.startswith("b'")
        # 关键:必须显式关掉 preload,否则客户端会把 bytes str() 掉
        assert core.read_namespaced_pod_log.call_args.kwargs["_preload_content"] is False

    def test_不能拿日志接口读任意_pod(self):
        # pod 名必须真的属于这个 workflow,否则这就是"读 argo 命名空间下
        # 任意 pod 日志"的入口了。
        resp, _ = self._get("/job/job-abc/logs/some-other-pod", "alice", _wf())
        assert resp.status_code == 404


class TestJobDetailContent:
    def _html(self, wf):
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = wf
        with patch.object(portal, "_k8s", return_value=api):
            portal.app.config["TESTING"] = True
            r = portal.app.test_client().get("/job/job-abc",
                                             headers={"X-Forwarded-User": "alice"})
        return r.get_data(as_text=True)

    def test_失败原因渲染出来了(self):
        html = self._html(_wf(message="OOMKilled: container exceeded memory"))
        assert "OOMKilled" in html

    def test_镜像_资源_参数_队列都在页面上(self):
        html = self._html(_wf())
        assert "local/platform-runtime:0.1.0" in html
        assert "500m" in html
        assert "date = 2026-08-29" in html
        assert "data-analysts" in html      # 队列标签在 template 上不在 workflow 上

    def test_只列_pod_节点_不列编排节点(self):
        # DAG/Steps 节点的失败信息是下面某个 Pod 的转述,列出来是同一件事看两遍
        steps = portal._wf_steps(_wf())
        assert [s["pod"] for s in steps] == ["job-abc-123"]

    def test_跑着的作业才显示取消按钮(self):
        assert "取消" in self._html(_wf(phase="Running", message=""))
        assert "取消" not in self._html(_wf(phase="Succeeded", message=""))

    def test_已结束的作业仍然能重跑(self):
        assert "重跑" in self._html(_wf(phase="Succeeded", message=""))


class TestJobActions:
    def _post(self, path, user="alice", wf=None):
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = wf if wf else _wf()
        api.create_namespaced_custom_object.return_value = {
            "metadata": {"name": "job-xyz"}}
        with patch.object(portal, "_k8s", return_value=api):
            portal.app.config["TESTING"] = True
            r = portal.app.test_client().post(path, headers={"X-Forwarded-User": user})
        return r, api

    def test_取消是打_shutdown_不是删除(self):
        # 删掉会连带丢失这次运行的记录和日志,而取消的人经常正是要去查它。
        resp, api = self._post("/job/job-abc/cancel", wf=_wf(phase="Running"))
        assert resp.status_code == 302
        api.delete_namespaced_custom_object.assert_not_called()
        body = api.patch_namespaced_custom_object.call_args[0][-1]
        assert body == {"spec": {"shutdown": "Terminate"}}

    def test_不能取消别人的作业(self):
        resp, api = self._post("/job/job-abc/cancel", "bob", _wf(owner="alice"))
        assert resp.status_code == 404
        api.patch_namespaced_custom_object.assert_not_called()

    def test_不能重跑别人的作业(self):
        resp, api = self._post("/job/job-abc/rerun", "bob", _wf(owner="alice"))
        assert resp.status_code == 404
        api.create_namespaced_custom_object.assert_not_called()

    def test_重跑用_generateName_不自己拼名字(self):
        _, api = self._post("/job/job-abc/rerun")
        body = api.create_namespaced_custom_object.call_args[0][-1]
        assert "generateName" in body["metadata"]
        assert "name" not in body["metadata"]

    def test_重跑出来的作业归当前用户(self):
        # 否则它不会出现在他自己的列表里,也就没人能再管它。
        _, api = self._post("/job/job-abc/rerun")
        body = api.create_namespaced_custom_object.call_args[0][-1]
        assert body["metadata"]["labels"]["platform-sdk/submitted-by"] == "alice"
        assert body["metadata"]["labels"]["platform-portal/rerun-of"] == "job-abc"

    def test_重跑不会把取消状态一起复制过去(self):
        wf = _wf(phase="Failed")
        wf["spec"]["shutdown"] = "Terminate"
        _, api = self._post("/job/job-abc/rerun", wf=wf)
        body = api.create_namespaced_custom_object.call_args[0][-1]
        assert "shutdown" not in body["spec"]

    def test_重跑不动原来那个(self):
        _, api = self._post("/job/job-abc/rerun")
        api.patch_namespaced_custom_object.assert_not_called()
        api.delete_namespaced_custom_object.assert_not_called()

    def test_k8s_报错时返回_500_而不是抛栈(self):
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = _wf(phase="Running")
        api.patch_namespaced_custom_object.side_effect = RuntimeError("boom")
        with patch.object(portal, "_k8s", return_value=api):
            portal.app.config["TESTING"] = True
            r = portal.app.test_client().post("/job/job-abc/cancel",
                                              headers={"X-Forwarded-User": "alice"})
        assert r.status_code == 500 and "取消失败" in r.get_json()["error"]


class TestRoleAwareToolList:
    """按角色显示工具(roadmap P1.5「不再对所有角色一视同仁地暴露」)。

    **这不是权限控制,是降噪** —— 真正拦得住的是每个组件自己的 SSO 和 OPA。
    这里做的是别把 14 个入口一股脑摆在一个只需要其中三个的人面前。
    """

    def _html(self, groups, source="claim_present", user="alice"):
        with patch.object(portal.identity, "parse_identity",
                          return_value=(user, groups, source)), \
             patch.object(portal, "probe_all", return_value={}), \
             patch.object(portal, "queue_usage", return_value={"error": None, "rows": [], "pending_total": 0}), \
             patch.object(portal, "my_jobs", return_value={"error": None, "rows": []}), \
             patch.object(portal, "golden_paths", return_value={"error": None, "rows": [], "ok": 0, "total": 0}), \
             patch.object(portal, "streams", return_value={"error": None, "rows": []}), \
             patch.object(portal, "my_permissions", return_value={"available": False, "grants": [], "expiring_soon": []}), \
             patch.object(portal, "my_approvals", return_value={"available": False, "pending": [], "overdue": []}):
            portal.app.config["TESTING"] = True
            return portal.app.test_client().get("/").get_data(as_text=True)

    def test_分析师看不到运维和身份那两栏(self):
        html = self._html(["data-analysts"])
        assert "ArgoCD" not in html
        assert "Keycloak" not in html

    def test_分析师看得到数据类工具(self):
        html = self._html(["data-analysts"])
        assert "SQL 工作台" in html and "Superset" in html

    def test_平台组看得到全部(self):
        html = self._html(["platform-team"])
        assert "ArgoCD" in html and "Keycloak" in html and "Superset" in html

    def test_没列进规则的分类对所有人显示(self):
        # 规则只写"限制谁能看",没写的分类默认所有人可见 —— 否则每加一个
        # 工具都要记得去改那张表,漏了就是新工具对谁都不显示。
        html = self._html(["algorithm-team"])
        assert "JupyterHub" in html and "MLflow" in html

    def test_拿不到组信息时显示全部_而不是显示空(self):
        # 宁可多显示几个进不去的入口,也不能因为一个配置没配对就让所有人
        # 看到一个空门户。
        html = self._html([], source="claim_missing")
        assert "ArgoCD" in html and "Superset" in html

    def test_拿不到组信息时页面上说清楚是配置问题(self):
        html = self._html([], source="claim_missing")
        assert "配置问题不是权限问题" in html
        assert "03-configure-keycloak.sh" in html

    def test_真的不在任何组时不报警(self):
        # groups 字段存在、内容为空 = 这个人确实不在任何组,是正常状态。
        html = self._html([], source="claim_present")
        assert "配置问题不是权限问题" not in html

    def test_工具计数跟着可见的走_不是总数(self):
        few = self._html(["data-analysts"])
        all_ = self._html(["platform-team"])
        import re
        n_few = int(re.search(r"(\d+) 个工具", few).group(1))
        n_all = int(re.search(r"(\d+) 个工具", all_).group(1))
        assert n_few < n_all


class TestGrantsUnavailableIsNotEmptyGrants:
    """"读不到 grants"不能显示成"你没有任何表权限"。

    **2026-08-30 开机验收当场抓到的**:permission-request-app 那边 GIT_TOKEN
    没配、又没有别的数据源,于是接口永远返回空 grants,门户上那一栏永远
    不显示 —— 而"这个人没有权限"和"读不到数据"返回的**是一模一样的空列表**。
    """

    def test_上游说读不到时_门户给出警告(self):
        with patch.object(portal, "_perm_api",
                          return_value={"grants": [], "expiring_soon": [],
                                        "available": False, "source": "unavailable"}):
            r = portal.my_permissions("alice")
        assert r["warning"] and "不代表你没有权限" in r["warning"]

    def test_上游正常但这个人确实没权限_不报警(self):
        with patch.object(portal, "_perm_api",
                          return_value={"grants": [], "expiring_soon": [],
                                        "available": True, "source": "raw"}):
            r = portal.my_permissions("alice")
        assert r["warning"] is None

    def test_老版本上游没有_available_字段时不报警(self):
        # 兼容:字段缺失当成正常,不要因为上游还没升级就吓唬人。
        with patch.object(portal, "_perm_api",
                          return_value={"grants": [], "expiring_soon": []}):
            assert portal.my_permissions("alice")["warning"] is None


class TestAuditGoldenPath:
    def test_门户认识_audit_这条链路(self):
        # 探针加了新链路而门户不认的话,首页上它会显示成原始的 key
        # (`audit`)而不是人话 —— 而且"N/M 条通"的分母也会对不上。
        assert "audit" in portal.GOLDEN_PATHS
        label, chain = portal.GOLDEN_PATHS["audit"]
        assert "留痕" in label and "Kafka" in chain

    def test_六条以外的新链路也会被算进总数(self):
        # 这条不是测常量,是测"加链路不用改别处"这个性质。
        assert len(portal.GOLDEN_PATHS) >= 7


class TestMinIO控制台卡片:
    """2026-08-31 新增(ADR-088)。

    在这之前 MinIO 控制台**没有任何对外入口**,门户上自然也没有链接 ——
    zhenghe 发现的。补的时候有一条安全约束必须由测试守住,见下。
    """

    def _card(self):
        return next((t for t in portal.TOOLS if t["name"] == "MinIO 控制台"), None)

    def test_卡片存在且指向_minio_主机(self):
        card = self._card()
        assert card is not None
        assert card["host"] == "minio"

    def test_必须在运维分类里(self):
        """**这条是安全约束,不是排版偏好。**

        「运维」这一类在 CATEGORY_GROUPS 里只对 platform-team 可见,而
        MinIO 的策略也只给了 platform-team(apps/components/minio.yaml)。
        两边必须一致 —— 把这张卡片挪进别的分类,等于在门户上给所有人
        显示一个他们点进去会被拒的链接,而更糟的情况是有人顺手把 MinIO
        策略也放开,那就**绕过了整套 OPA 行列级权限**(MinIO 里是 Iceberg
        的 parquet 原始文件)。
        """
        card = self._card()
        assert card["category"] == "运维"
        assert portal.CATEGORY_AUDIENCE["运维"] == {"platform-team"}

    def test_探的是存储健康不是控制台页面(self):
        """控制台是个前端,它"能打开"不代表对象存储是好的。"""
        card = self._card()
        assert "9000" in card["probe"] and "health" in card["probe"]

    def test_非_platform_team_看不到这张卡片(self):
        vis = portal.visible_categories({"data-analysts"})
        assert "运维" not in vis
