"""jobs/feature-drift 的逻辑测试(ADR-087)。

作业本体跑在集群上(要 Trino 和 MLflow),所以这里测的是**不依赖它们的那
部分判断**:PSI 怎么算、payload 怎么拆、没有基线/没有数据时怎么办。

这几条恰恰是最危险的那类 —— **算错了在集群上不会报错**,只会安静地给出
一个看起来合理的数字。一个永远算出 0 的 PSI 和一个正常工作的 PSI,在日志
里长得一模一样。
"""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
JOB = REPO / "jobs" / "feature-drift" / "job.py"


def load_job(monkeypatch, rows=None, baselines=None, params=None):
    """把 platform_sdk / jobkit / mlflow 全换成假的,直接跑那个脚本。

    返回 (发过的 SQL, SystemExit 或 None)。
    """
    executed = []

    def fake_query(sql):
        executed.append(" ".join(sql.split()))
        if "FROM iceberg.ml.inference_log" in " ".join(sql.split()):
            return (["payload"], [(p,) for p in (rows or [])])
        return (["x"], [])

    sdk = types.ModuleType("platform_sdk")
    sdk.query = fake_query
    monkeypatch.setitem(sys.modules, "platform_sdk", sdk)

    kit = types.ModuleType("jobkit")
    kit.param = lambda name, default=None: (params or {}).get(name, default)
    kit.rows_of = lambda r: r[1]
    monkeypatch.setitem(sys.modules, "jobkit", kit)

    class FakeMV:
        def __init__(self, version, tags, run_id=None):
            self.version = version
            self.tags = tags
            self.run_id = run_id

    class FakeRM:
        def __init__(self, name):
            self.name = name

    _run_ids = {k: f"run{i}" for i, k in enumerate(sorted(baselines or {}))}
    _by_run = {_run_ids[k]: v for k, v in (baselines or {}).items()}

    class FakeClient:
        def search_registered_models(self):
            return [FakeRM(n) for n in {m for m, _ in (baselines or {})}]

        def search_model_versions(self, filt):
            name = filt.split("'")[1]
            # **基线放在 run tag 上,模型版本的 tags 是空的** —— 这就是
            # 真实情况:训练脚本用的 mlflow.set_tag() 写的是 run tag。
            #
            # run_id 用序号而不是拼模型名:模型名里有横线
            # (demo-rf-classifier),按横线拆会拆错 —— 第一版这么写,
            # 测试自己先挂了。
            return [FakeMV(v, {}, run_id=_run_ids[(m, v)])
                    for (m, v), b in (baselines or {}).items() if m == name]

        def get_run(self, run_id):
            b = _by_run.get(run_id)
            data = type("D", (), {"tags": {"feature_baseline": json.dumps(b)} if b else {}})
            return type("R", (), {"data": data})()

    mlflow = types.ModuleType("mlflow")
    mlflow.MlflowClient = FakeClient
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.invalid")

    spec = importlib.util.spec_from_file_location("drift_under_test", JOB)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit as e:
        return executed, e
    return executed, None


def load_module_only():
    """只拿到模块里的纯函数(psi / online_vectors),不跑 main()。"""
    src = JOB.read_text(encoding="utf-8").replace("\nmain()\n", "\n")
    mod = types.ModuleType("drift_funcs")
    mod.__dict__["__name__"] = "drift_funcs"
    sdk = types.ModuleType("platform_sdk")
    sdk.query = lambda sql: (["x"], [])
    kit = types.ModuleType("jobkit")
    kit.param = lambda name, default=None: default
    kit.rows_of = lambda r: r[1]
    sys.modules["platform_sdk"] = sdk
    sys.modules["jobkit"] = kit
    exec(compile(src, str(JOB), "exec"), mod.__dict__)
    return mod


# 10 个等频桶的边界:训练数据是 0..1 均匀分布
UNIFORM_EDGES = [float("-inf")] + [i / 10 for i in range(1, 10)] + [float("inf")]


class TestPSI:
    def test_分布没变时_PSI_接近零(self):
        m = load_module_only()
        # 每个桶恰好 10 个样本 —— 和训练时的等频分箱一致
        vals = [b / 10 + 0.05 for b in range(10) for _ in range(10)]
        assert m.psi(UNIFORM_EDGES, vals) < 0.01

    def test_分布整体平移时_PSI_明显变大(self):
        m = load_module_only()
        same = [b / 10 + 0.05 for b in range(10) for _ in range(10)]
        shifted = [v + 0.5 for v in same]
        assert m.psi(UNIFORM_EDGES, shifted) > m.psi(UNIFORM_EDGES, same)
        assert m.psi(UNIFORM_EDGES, shifted) > 0.2

    def test_全部挤进一个桶是最强的漂移信号(self):
        """**这条盯的是空桶的处理**,而且阈值是特意算过的。

        线上 100 个样本全落进第 0 桶时:
          - 空桶按 1e-6 算(现在的实现):2.07 + 9 × 1.151 ≈ **12.4**
          - 空桶跳过(错误实现):只剩第 0 桶那一项 ≈ **2.07**

        所以断言必须卡在两者之间 —— 第一版写的是 `> 1.0`,**两种实现都能
        过**,等于没测。这类"看起来在测、其实不区分"的断言比没有更糟:
        它会让人以为这条边界有人守着。
        """
        m = load_module_only()
        collapsed = [0.05] * 100
        assert m.psi(UNIFORM_EDGES, collapsed) > 5.0

    def test_训练集范围外的值也算得进去(self):
        """首尾用 ±inf 就是为了这个:线上出现训练时没见过的量级时,那些值
        必须落进某个桶并贡献 PSI,不能被静默丢掉。

        阈值同上一条:5.0 才能区分"空桶算不算"。
        """
        m = load_module_only()
        out_of_range = [999.0] * 100
        score = m.psi(UNIFORM_EDGES, out_of_range)
        assert score is not None and score > 5.0

    def test_没有线上样本时返回_None_而不是零(self):
        """0 会被读成"没有漂移",None 才是"算不了"。这两件事在告警上是
        完全相反的结论。"""
        m = load_module_only()
        assert m.psi(UNIFORM_EDGES, []) is None


class TestPayload解析:
    def test_按_shape_切回多行(self):
        m = load_module_only()
        payload = json.dumps({"inputs": [{"name": "input-0", "shape": [2, 3],
                                          "data": [1, 2, 3, 4, 5, 6]}]})
        m.query = lambda sql: (["payload"], [(payload,)])
        vs = m.online_vectors("demo-rf-classifier")
        assert vs == [[1, 2, 3], [4, 5, 6]]

    def test_坏的_payload_不让整批失败(self):
        m = load_module_only()
        good = json.dumps({"inputs": [{"shape": [1, 2], "data": [1, 2]}]})
        m.query = lambda sql: (
            ["payload"], [(good,), ("{不是 json",), (None,)])
        vs = m.online_vectors("demo-rf-classifier")
        assert vs == [[1, 2]]

    def test_非数值的输入被跳过(self):
        """文本特征混进来时不能让 PSI 计算炸在类型上。"""
        m = load_module_only()
        payload = json.dumps({"inputs": [{"shape": [1, 2], "data": ["a", "b"]}]})
        m.query = lambda sql: (["payload"], [(payload,)])
        assert m.online_vectors("demo-rf-classifier") == []


class Test没有基线时的行为:
    def test_一个带基线的版本都没有就明确失败(self, monkeypatch):
        """**不能安静地跳过。** "没有基线"和"没有漂移"是完全不同的结论,
        而前者会让人以为模型很健康。"""
        ex, exc = load_job(monkeypatch, baselines={})
        assert isinstance(exc, SystemExit)
        assert "算不了" in str(exc)

    def test_窗口内没有推理请求不算失败(self, monkeypatch):
        """demo 环境里模型没流量是常态,把作业标红会让人学会忽略它。"""
        base = {"bin_edges": [UNIFORM_EDGES], "mean": [0.5], "std": [0.3]}
        ex, exc = load_job(monkeypatch, rows=[],
                           baselines={("demo-rf-classifier", "1"): base})
        assert exc is None
        assert not any(s.startswith("INSERT INTO") for s in ex)


class TestDryRun:
    def test_dry_run_不建表也不写表(self, monkeypatch):
        base = {"bin_edges": [UNIFORM_EDGES], "mean": [0.5], "std": [0.3]}
        payload = json.dumps({"inputs": [{"shape": [1, 1], "data": [0.5]}]})
        ex, exc = load_job(monkeypatch, rows=[payload] * 20,
                           baselines={("demo-rf-classifier", "1"): base},
                           params={"dry_run": "1"})
        assert exc is None
        assert not any("CREATE TABLE" in s for s in ex)
        assert not any(s.startswith("INSERT INTO") for s in ex)

    def test_正常模式会建表并写结果(self, monkeypatch):
        base = {"bin_edges": [UNIFORM_EDGES], "mean": [0.5], "std": [0.3]}
        payload = json.dumps({"inputs": [{"shape": [1, 1], "data": [0.5]}]})
        ex, exc = load_job(monkeypatch, rows=[payload] * 20,
                           baselines={("demo-rf-classifier", "1"): base})
        assert exc is None
        assert any("CREATE TABLE IF NOT EXISTS iceberg.ml.feature_drift" in s for s in ex)
        assert any(s.startswith("INSERT INTO iceberg.ml.feature_drift") for s in ex)


class Test窗口参数:
    def test_window_days_进了_SQL(self, monkeypatch):
        base = {"bin_edges": [UNIFORM_EDGES], "mean": [0.5], "std": [0.3]}
        ex, _ = load_job(monkeypatch, rows=[],
                         baselines={("demo-rf-classifier", "1"): base},
                         params={"window_days": "30"})
        assert any("INTERVAL '30' DAY" in s for s in ex)

    def test_只查_request_不查_response(self, monkeypatch):
        """漂移看的是进来的数据。混进 response 的话,分布里会掺进预测值,
        算出来的东西没有任何意义 —— 而且不会报错。"""
        base = {"bin_edges": [UNIFORM_EDGES], "mean": [0.5], "std": [0.3]}
        ex, _ = load_job(monkeypatch, rows=[],
                         baselines={("demo-rf-classifier", "1"): base})
        sel = [s for s in ex if "inference_log" in s]
        assert sel and all("event_type = 'request'" in s for s in sel)


class Test基线来源:
    """**基线在 run 的 tag 上,不在模型版本的 tag 上。**

    这两个是不同的东西,而训练脚本用的 `mlflow.set_tag()` 写的是 run tag。
    2026-09-01 自查抓到第一版读的是 `mv.tags` —— 那永远是空的,作业会一路
    正常跑完然后报"一个带 feature_baseline 的模型版本都没有",而训练脚本
    明明写了。**整个功能静默失效,报错还指向训练那一侧。**

    上面 load_job 里的假客户端就是按真实情况建的(模型版本 tags 为空,
    基线只在 run 上),所以其它测试能通过本身就说明读对了地方。这里再单独
    钉一条:模型版本上什么都没有时,仍然要能从 run 拿到基线。
    """

    def test_模型版本_tags_为空时仍能从_run_拿到基线(self, monkeypatch):
        base = {"bin_edges": [UNIFORM_EDGES], "mean": [0.5], "std": [0.3]}
        payload = json.dumps({"inputs": [{"shape": [1, 1], "data": [0.5]}]})
        ex, exc = load_job(monkeypatch, rows=[payload] * 20,
                           baselines={("demo-rf-classifier", "1"): base})
        assert exc is None, f"应该算得出来,实际:{exc}"
        assert any(s.startswith("INSERT INTO iceberg.ml.feature_drift") for s in ex)

    def test_run_也没有基线时才算_没有基线(self, monkeypatch):
        ex, exc = load_job(monkeypatch, baselines={})
        assert isinstance(exc, SystemExit)
        assert "算不了" in str(exc)
