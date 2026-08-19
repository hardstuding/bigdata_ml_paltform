"""config.py 的纯逻辑测试,不需要真实集群。"""

import pytest

from platform_sdk import config


def test_require_missing_raises_with_hint():
    with pytest.raises(config.MissingCredential, match="PLATFORM_TEST_VAR"):
        config.require("PLATFORM_TEST_VAR_NOT_SET", "提示信息")


def test_require_present(monkeypatch):
    monkeypatch.setenv("PLATFORM_TEST_VAR", "hello")
    assert config.require("PLATFORM_TEST_VAR") == "hello"


def test_empty_string_treated_as_unset(monkeypatch):
    # K8s 的 env/Secret 降级路径经常产生空字符串,不能当成"已设置"。
    monkeypatch.setenv("PLATFORM_TEST_VAR", "")
    with pytest.raises(config.MissingCredential):
        config.require("PLATFORM_TEST_VAR")


def test_defaults_are_lazy_not_frozen_at_import(monkeypatch):
    # 这是 config.py 从"模块级常量"改成"函数"要防的那个坑本身:
    # 设置环境变量之后调用,必须拿到新值,不能是 import 那一刻的旧值。
    assert config.trino_host() == "trino.trino.svc.cluster.local"
    monkeypatch.setenv("PLATFORM_TRINO_HOST", "localhost")
    assert config.trino_host() == "localhost"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("false", False),
        ("0", False),
        ("", False),
        ("true", True),
        ("1", True),
        ("/etc/ssl/custom-ca.pem", "/etc/ssl/custom-ca.pem"),
    ],
)
def test_trino_verify_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("PLATFORM_TRINO_VERIFY", raw)
    assert config.trino_verify() == expected
