"""jobs/iceberg-maintenance 的逻辑测试。

这个作业跑在集群上(需要 Trino),所以这里测的是**不依赖 Trino 的那部分
判断**:动作顺序、失败处理、dry-run。这几条恰恰是最容易写错、而且写错了
在集群上不会立刻暴露的。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
JOB = REPO / "jobs" / "iceberg-maintenance" / "job.py"


def load_job(monkeypatch, queries=None, params=None, tables=None):
    """把 platform_sdk 和 jobkit 都换成假的,直接跑那个脚本,记录它发了哪些 SQL。"""
    import types
    executed = []

    def fake_query(sql):
        executed.append(" ".join(sql.split()))
        if "information_schema.tables" in sql:
            # 作业现在会加 `AND table_type = 'BASE TABLE'`(2026-08-30:视图
            # 用不了 ALTER TABLE EXECUTE,实机撞到过),所以这里要按第一个
            # 引号对取 schema,不能整段 split。
            schema = sql.split("table_schema = ")[1].split("'")[1]
            return (["table_name"], [(t,) for t in (tables or {}).get(schema, [])])
        for pat, exc in (queries or {}).items():
            if pat in sql:
                raise exc
        return (["x"], [])

    sdk = types.ModuleType("platform_sdk")
    sdk.query = fake_query
    monkeypatch.setitem(sys.modules, "platform_sdk", sdk)

    kit = types.ModuleType("jobkit")
    kit.param = lambda name, default=None: (params or {}).get(name, default)
    kit.rows_of = lambda r: r[1]
    monkeypatch.setitem(sys.modules, "jobkit", kit)

    spec = importlib.util.spec_from_file_location("job_under_test", JOB)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit as e:
        return executed, e
    return executed, None


class TestActionOrder:
    def test_先合并再过期最后清孤儿(self, monkeypatch):
        """**顺序不能换。** 先过期快照再合并的话,刚合并出来的旧文件还被
        快照引用着,清不掉;先清孤儿再合并同理。"""
        ex, _ = load_job(monkeypatch, tables={"audit": ["query_events"]})
        acts = [s for s in ex if "EXECUTE" in s]
        assert len(acts) == 3
        assert "optimize" in acts[0]
        assert "expire_snapshots" in acts[1]
        assert "remove_orphan_files" in acts[2]

    def test_保留天数进了_sql(self, monkeypatch):
        ex, _ = load_job(monkeypatch, params={"retain_days": "14"},
                         tables={"audit": ["query_events"]})
        assert any("'14d'" in s for s in ex)


class TestFailureHandling:
    def test_一张表某个动作失败不影响其它动作和其它表(self, monkeypatch):
        """一张表维护失败不该让其它表也不做 —— "这次没清干净"的后果只是
        下次多清一点。"""
        ex, exit_exc = load_job(
            monkeypatch,
            queries={"EXECUTE optimize": RuntimeError("table is being written")},
            tables={"audit": ["query_events", "query_table_access"]})
        # optimize 失败了,但后面两个动作照做,两张表都处理
        assert sum(1 for s in ex if "expire_snapshots" in s) == 2
        assert exit_exc is None or exit_exc.code is None

    def test_一张表都没处理到才算失败(self, monkeypatch):
        """全都没处理到说明 catalog 根本连不上 —— 那是真问题,要红。"""
        _, exit_exc = load_job(monkeypatch, tables={})
        assert exit_exc is not None and exit_exc.code

    def test_schema_不存在只是跳过(self, monkeypatch):
        # ml 这个 schema 要等推理留痕启用之后才有,不存在是正常的。
        ex, exit_exc = load_job(
            monkeypatch,
            queries={"table_schema = 'ml'": RuntimeError("Schema not found")},
            tables={"audit": ["query_events"]})
        assert exit_exc is None or exit_exc.code is None


class TestDryRun:
    def test_dry_run_不发任何_execute(self, monkeypatch):
        ex, _ = load_job(monkeypatch, params={"dry_run": "1"},
                         tables={"audit": ["query_events"]})
        assert not any("EXECUTE" in s for s in ex)


class TestScope:
    def test_只动平台自己的三个_schema(self, monkeypatch):
        """不去动 tpch/tpcds(只读基准数据,没有快照堆积),也不自作主张
        动用户自己建的表 —— 合并小文件会重写数据,那是有副作用的操作。"""
        ex, _ = load_job(monkeypatch, tables={"audit": ["t"], "ml": ["t"], "demo": ["t"]})
        scanned = {s.split("table_schema = ")[1].split("'")[1]
                   for s in ex if "information_schema" in s}
        assert scanned == {"audit", "ml", "demo"}


class TestSkipViews:
    def test_只查真正的表_不查视图(self, monkeypatch):
        """**2026-08-30 实机第一次跑就撞到**:`iceberg.demo.stg_orders` 是
        dbt 建的视图,`ALTER TABLE ... EXECUTE` 对它直接报 NOT_SUPPORTED,
        三个动作全失败。视图没有数据文件也没有快照,本来就不需要维护。"""
        ex, _ = load_job(monkeypatch, tables={"audit": ["t"]})
        q = [s for s in ex if "information_schema" in s]
        assert q and all("BASE TABLE" in s for s in q)
