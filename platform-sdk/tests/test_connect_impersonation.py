"""身份代理(impersonation)的行为测试。

**为什么这几条值得写测试**:这里错了不会有人看到报错,只会**安静地用服务
账号的权限去查数据**——而服务账号在 OPA 里是无条件放行的。也就是说写错
的后果是"行列级权限对 notebook 静默失效",分析师在 Superset 里被脱敏的
手机号在 notebook 里能查出明文。这类"错了也不报错"的地方,是最该有测试的。

背景见 ADR-074(Superset 那条路先修的)和 SDK 里 acting_user() 的注释。
"""
import os
from unittest.mock import patch

import pytest

from platform_sdk import connect as c


@pytest.fixture(autouse=True)
def clean_env():
    """每条用例都从干净的环境开始 —— 否则前一条设的变量会污染后一条,
    而这类污染在"身份"这种主题上会得出完全相反的结论。"""
    saved = {k: os.environ.pop(k, None)
             for k in ("PLATFORM_ACTING_USER", "JUPYTERHUB_USER")}
    yield
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v


def test_notebook_里自动取登录用户():
    # JupyterHub 给每个 notebook pod 注入 JUPYTERHUB_USER,不用任何配置。
    os.environ["JUPYTERHUB_USER"] = "analyst001"
    assert c.acting_user() == "analyst001"


def test_显式指定优先于_jupyterhub():
    os.environ["JUPYTERHUB_USER"] = "analyst001"
    os.environ["PLATFORM_ACTING_USER"] = "algo001"
    assert c.acting_user() == "algo001"


def test_定时作业里没有当前用户():
    # 定时作业本来就没有"当前用户"这个概念,返回 None 是对的,
    # 不该硬造一个身份出来。
    assert c.acting_user() is None


def _headers_of(**kw):
    """跑一遍 trino_connection,把它最终传给 trino 客户端的 http_headers 抓出来。"""
    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return object()

    with patch("trino.dbapi.connect", fake_connect), \
         patch("trino.auth.BasicAuthentication", lambda *a: None):
        c.trino_connection(user="notebook_service", password="x", **kw)
    return captured.get("http_headers")


def test_有当前用户时带上代理头():
    os.environ["JUPYTERHUB_USER"] = "analyst001"
    h = _headers_of()
    assert h == {"X-Trino-Authorization-User": "analyst001"}


def test_没有当前用户时不带代理头():
    # **不要退化成"带一个空头"或者"带服务账号自己"** —— 那会让 Trino 收到
    # 一个语义不明的请求。没有就是不带。
    assert _headers_of() is None


def test_代理目标就是自己时不带头():
    # 服务账号代表自己查,不需要走代理路径;带了反而要求它有
    # ImpersonateUser 权限,平白多一个失败点。
    os.environ["JUPYTERHUB_USER"] = "notebook_service"
    assert _headers_of() is None


def test_act_as_参数优先于环境变量():
    os.environ["JUPYTERHUB_USER"] = "analyst001"
    h = _headers_of(act_as="ceo001")
    assert h == {"X-Trino-Authorization-User": "ceo001"}
