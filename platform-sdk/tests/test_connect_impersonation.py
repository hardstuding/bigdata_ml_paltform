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


def _connect_args(**kw):
    """跑一遍 trino_connection,把它最终传给 trino 客户端的参数抓出来。

    关注两个:`user`(会话身份,OPA 按它算权限)和 auth 里的账号
    (认证身份)。**这两个必须是分开的** —— 2026-08-29 第一版把代理写成加
    `X-Trino-Authorization-User` 头,实测那个头压根不生效,而且不报错:
    查询照常跑、权限照常按服务账号算。差点用一个同样静默失效的实现去"修"
    一个静默失效的洞。
    """
    captured = {}

    class FakeAuth:
        def __init__(self, u, p):
            captured["auth_user"] = u

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return object()

    with patch("trino.dbapi.connect", fake_connect), \
         patch("trino.auth.BasicAuthentication", FakeAuth):
        c.trino_connection(user="notebook_service", password="x", **kw)
    return captured


def test_有当前用户时会话身份是那个人():
    os.environ["JUPYTERHUB_USER"] = "analyst001"
    a = _connect_args()
    assert a["user"] == "analyst001", "会话身份必须是被代理的人,OPA 按它算权限"
    assert a["auth_user"] == "notebook_service", "认证身份必须还是服务账号"


def test_没有当前用户时会话身份就是服务账号():
    a = _connect_args()
    assert a["user"] == "notebook_service"
    assert a["auth_user"] == "notebook_service"


def test_不再依赖任何自定义_header():
    # 显式锁死:别再有人"顺手"把 X-Trino-Authorization-User 加回来——
    # 它不生效,而且不生效时不报错。
    os.environ["JUPYTERHUB_USER"] = "analyst001"
    a = _connect_args()
    assert not a.get("http_headers"), "impersonation 不靠 header,靠 user 字段"


def test_act_as_参数优先于环境变量():
    os.environ["JUPYTERHUB_USER"] = "analyst001"
    assert _connect_args(act_as="ceo001")["user"] == "ceo001"
