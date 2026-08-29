"""ArgoCD 自定义健康检查(Lua)的单元测试。

**为什么值得给一段 25 行的 Lua 写测试**:它跑在 ArgoCD 控制器里,写错的
表现不是报错,是**所有 Job 的健康判断都变形**——比如漏了 `Failed` 分支,
db-init 失败就再也不会让 Application 变红。而这段 Lua 平时没人会去看。

用 lupa(Python 里的 Lua 运行时)真跑一遍,不是数括号。

跑法:python3 -m pytest tests/test_argocd_health_lua.py -v
"""
from pathlib import Path

import pytest
import yaml

lupa = pytest.importorskip("lupa", reason="没装 lupa 就跳过(pip install lupa)")

VALUES = Path(__file__).resolve().parent.parent / "platform" / "bootstrap" / "argocd-values.yaml"
KEY = "resource.customizations.health.batch_Job"
CRONJOB_KEY = "resource.customizations.health.batch_CronJob"


def _make_runner(key):
    src = yaml.safe_load(VALUES.read_text())["configs"]["cm"][key]
    L = lupa.LuaRuntime(unpack_returned_tuples=True)

    def to_lua(v):
        # 必须递归转成真正的 Lua table:直接塞 Python dict 的话,缺失的 key
        # 会抛 KeyError 而不是像 Lua 那样返回 nil,测出来的行为和线上不一样
        # (第一版就是这么写的,结果第二个用例直接崩了)。
        if isinstance(v, dict):
            return L.table_from({k: to_lua(x) for k, x in v.items()})
        if isinstance(v, list):
            return L.table_from([to_lua(x) for x in v])
        return v

    def run(obj):
        L.globals().obj = to_lua(obj)
        return L.execute("return (function() " + src + " end)()")

    return run


@pytest.fixture(scope="module")
def run_health():
    return _make_runner(KEY)


@pytest.fixture(scope="module")
def run_cronjob_health():
    return _make_runner(CRONJOB_KEY)


PROBE = {"platform/golden-path": "probe"}


def _job(labels=None, conditions=None):
    return {"metadata": {"labels": labels} if labels else {},
            "status": {"conditions": conditions} if conditions else {}}


def test_探针失败不让应用变红(run_health):
    # 这是这段 Lua 存在的全部理由:探针失败说明"它抓到了东西",
    # 不说明"探针这个组件坏了"。
    hs = run_health(_job(PROBE, [{"type": "Failed", "status": "True", "message": "x"}]))
    assert hs["status"] == "Healthy"


def test_普通_job_失败仍然变红(run_health):
    # **这条比上一条更重要**:上一条写错了只是少一个告警,这条写错了
    # 意味着 db-init 这类真失败再也不会让 Application 变红。
    hs = run_health(_job({"app": "db-init"},
                         [{"type": "Failed", "status": "True", "message": "建库失败"}]))
    assert hs["status"] == "Degraded"
    assert hs["message"] == "建库失败"


def test_普通_job_成功是健康(run_health):
    hs = run_health(_job({"app": "db-init"},
                         [{"type": "Complete", "status": "True", "message": "done"}]))
    assert hs["status"] == "Healthy"


def test_没有_labels_也不能崩(run_health):
    # 刚创建的 Job 可能既没有 labels 也没有 conditions。Lua 里对 nil 取索引
    # 会直接报错,那会让 ArgoCD 判不出健康状态。
    assert run_health(_job())["status"] == "Progressing"


def test_探针_job_刚创建也是健康(run_health):
    assert run_health(_job(PROBE))["status"] == "Healthy"



# ---- CronJob 那条(2026-08-29 加)----
# 这才是真正让 Application 变黄的对象。实测:golden-path-probes 变 Degraded
# 既不是 Job 也不是 Pod 冒上来的,是 CronJob —— ArgoCD 对 batch/CronJob 有
# 内置健康判定,某条探针最近一次失败就会让对应 CronJob 变 Degraded。把失败
# 的 Job 和 Pod 全删干净都没用,手工跑一次成功的探针之后才回到 Healthy。


def _cronjob(labels=None):
    return {"metadata": {"labels": labels} if labels else {}, "status": {}}


def test_探针cronjob永远健康(run_cronjob_health):
    assert run_cronjob_health(_cronjob(PROBE))["status"] == "Healthy"


def test_普通cronjob交回内置判定(run_cronjob_health):
    # 返回空 status 是 ArgoCD 约定的"我不管,你按默认来"。**不能在这里
    # 自己实现一套** —— 内置的比几行 Lua 周全。
    assert run_cronjob_health(_cronjob({"app": "postgres-backup"}))["status"] == ""


def test_cronjob没有labels也不能崩(run_cronjob_health):
    assert run_cronjob_health(_cronjob())["status"] == ""
