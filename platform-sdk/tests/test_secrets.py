"""platform_sdk.secret() 的测试(ADR-089)。

这里盯的是几条**错了不会报错、只会静默给出错东西**的分支 —— 那类 bug 在
集群上表现为"连不上数据库",查起来会一路查到网络和防火墙上去。
"""
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from platform_sdk import secrets as S  # noqa: E402
from platform_sdk.config import MissingCredential  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    S._cached_token = None
    for k in list(__import__("os").environ):
        if k.startswith(("PLATFORM_SECRET_", "PLATFORM_GROUPS", "PLATFORM_USER",
                         "JUPYTERHUB_USER", "PLATFORM_OIDC_TOKEN", "OPENBAO_ADDR")):
            monkeypatch.delenv(k, raising=False)
    yield
    S._cached_token = None


class Test环境变量优先:
    def test_本机开发用环境变量绕过_OpenBao(self, monkeypatch):
        """**放在最前面是有意的**:本机 IDE 连不上集群里的 OpenBao,而
        "想试一段代码还得先起个集群"会让人绕开整套机制、把密码写回代码里。
        """
        monkeypatch.setenv("PLATFORM_SECRET_MYSQL_CRM", "本机的值")
        assert S.secret("mysql_crm") == "本机的值"

    def test_名字里的横线转成下划线(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_SECRET_MY_DB", "v")
        assert S.secret("my-db") == "v"

    def test_环境变量命中时不去连_OpenBao(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("不该发起登录")
        monkeypatch.setattr(S, "_login", boom)
        monkeypatch.setenv("PLATFORM_SECRET_X", "v")
        assert S.secret("x") == "v"


class Test查找顺序:
    def _fake(self, monkeypatch, store):
        monkeypatch.setattr(S, "_login", lambda: "tok")
        monkeypatch.setattr(S, "_read", lambda tok, path: store.get(path))

    def test_先查个人再查组(self, monkeypatch):
        self._fake(monkeypatch, {
            "users/alice/db": {"value": "个人的"},
            "shared/data-analysts/db": {"value": "组里的"},
        })
        monkeypatch.setenv("PLATFORM_USER", "alice")
        monkeypatch.setenv("PLATFORM_GROUPS", "data-analysts")
        assert S.secret("db") == "个人的"

    def test_个人没有就用组里的(self, monkeypatch):
        self._fake(monkeypatch, {"shared/data-analysts/db": {"value": "组里的"}})
        monkeypatch.setenv("PLATFORM_USER", "alice")
        monkeypatch.setenv("PLATFORM_GROUPS", "data-analysts")
        assert S.secret("db") == "组里的"

    def test_显式指定组时不查个人路径(self, monkeypatch):
        """**这条防的是静默拿错。** 个人路径下有同名的一份时,"我要的就是
        团队那份共享账号"这个意图会被悄悄盖掉,而人完全不知道。
        """
        self._fake(monkeypatch, {
            "users/alice/db": {"value": "个人的"},
            "shared/algorithm-team/db": {"value": "组里的"},
        })
        monkeypatch.setenv("PLATFORM_USER", "alice")
        monkeypatch.setenv("PLATFORM_GROUPS", "data-analysts,algorithm-team")
        assert S.secret("db", group="algorithm-team") == "组里的"

    def test_按_PLATFORM_GROUPS_的顺序找(self, monkeypatch):
        self._fake(monkeypatch, {
            "shared/b/db": {"value": "B"},
            "shared/a/db": {"value": "A"},
        })
        monkeypatch.setenv("PLATFORM_GROUPS", "a,b")
        assert S.secret("db") == "A"


class Test找不到时的行为:
    def test_抛异常而不是返回_None(self, monkeypatch):
        """返回 None 的话调用方多半会拿它去连数据库,报出来的错是
        "认证失败",和"凭据没配"差着十万八千里。"""
        monkeypatch.setattr(S, "_login", lambda: "tok")
        monkeypatch.setattr(S, "_read", lambda tok, path: None)
        monkeypatch.setenv("PLATFORM_USER", "alice")
        with pytest.raises(MissingCredential) as e:
            S.secret("nope")
        assert "users/alice/nope" in str(e.value)

    def test_一个组都没有时明确提示(self, monkeypatch):
        """PLATFORM_GROUPS 空 = 组共享凭据全查不了,而症状只是"找不到"。
        这是 notebook 起得太早时的真实情况,提示里要说清楚。"""
        monkeypatch.setattr(S, "_login", lambda: "tok")
        monkeypatch.setattr(S, "_read", lambda tok, path: None)
        monkeypatch.setenv("PLATFORM_USER", "alice")
        with pytest.raises(MissingCredential) as e:
            S.secret("nope")
        assert "PLATFORM_GROUPS 是空的" in str(e.value)


class Test值的形状:
    def test_取_value_键(self):
        assert S._single_value({"value": "v"}, "n") == "v"

    def test_只有一个键时不管叫什么都用它(self):
        assert S._single_value({"password": "v"}, "n") == "v"

    def test_多个键时报错而不是猜一个(self):
        """猜一个返回,会让人拿到错的那份而毫无察觉。"""
        with pytest.raises(MissingCredential) as e:
            S._single_value({"user": "a", "password": "b"}, "n")
        assert "多个字段" in str(e.value)


class Test登录失败的提示:
    def test_没有_token_时说清楚三种原因(self, monkeypatch):
        with pytest.raises(MissingCredential) as e:
            S._login()
        msg = str(e.value)
        assert "PLATFORM_OIDC_TOKEN" in msg
        assert "本机" in msg and "重启" in msg

    def _http_error(self, code, body):
        return urllib.error.HTTPError("u", code, "e", {},
                                      __import__("io").BytesIO(body.encode()))

    def test_audience_对不上时不要往权限上引(self, monkeypatch):
        """**这条是排障方向问题。** bound_audiences 写错的症状是
        "invalid audience",而人第一反应会去查策略 —— 差得很远。"""
        monkeypatch.setenv("PLATFORM_OIDC_TOKEN", "t")
        def boom(*a, **k):
            raise self._http_error(400, '{"errors":["invalid audience"]}')
        monkeypatch.setattr(S, "_post", boom)
        with pytest.raises(MissingCredential) as e:
            S._login()
        assert "audience 对不上" in str(e.value)
        assert "bound_audiences" in str(e.value)

    def test_token_过期时提示重启内核(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_OIDC_TOKEN", "t")
        def boom(*a, **k):
            raise self._http_error(403, '{"errors":["permission denied"]}')
        monkeypatch.setattr(S, "_post", boom)
        with pytest.raises(MissingCredential) as e:
            S._login()
        assert "过期" in str(e.value) and "Restart" in str(e.value)


class Test读取的容错:
    def test_403_和_404_当成没有而不是抛(self, monkeypatch):
        """一个人对别人的路径是 403。查找过程里遇到 403 该继续找下一个,
        不是直接炸 —— 否则"我有组里那份"会被"我没有个人那份"挡住。"""
        import io
        calls = []
        def fake_get(path, token):
            calls.append(path)
            raise urllib.error.HTTPError("u", 403, "e", {}, io.BytesIO(b"{}"))
        monkeypatch.setattr(S, "_get", fake_get)
        assert S._read("tok", "users/bob/x") is None
