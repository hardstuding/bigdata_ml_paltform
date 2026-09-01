"""特征漂移监控:线上推理输入的分布,和训练时的分布比,变了多少(ADR-087)。

**为什么这件事值得单独做一个作业。** 模型上线之后不会报错 —— 它对任何
输入都会给出一个预测。真正的失效方式是**输入的数据慢慢变了**(用户结构
变化、上游埋点改了口径、某个特征的采集坏了变成恒定值),而模型还在按旧
世界的规律给答案。这类失效**没有任何告警会响**:服务是 200,延迟正常,
Pod 健康。只有把线上分布和训练分布放在一起比,才看得见。

**怎么比:PSI(Population Stability Index,群体稳定性指数)。**

    PSI = Σ (线上占比 - 训练占比) × ln(线上占比 / 训练占比)

按训练集的分位数把每个特征分成 10 个等频桶,看线上样本落进各桶的比例
偏离了多少。业界的经验分界是 0.1 / 0.2(见 job.yaml 里 psi_threshold
的注释,那不是理论值)。

**为什么是 PSI 而不是均值方差**:只看均值方差的话,"分布形状变了但均值
没变"这种漂移完全看不出来 —— 比如一个特征从单峰变成双峰,均值可能纹丝
不动。分桶比较看得见形状。

**基线从哪来**:训练时写进 MLflow 的 `feature_baseline` tag(见
`scripts/train_demo_model.py`)。**不在这里重建训练集分布** —— 重建出来
的是"现在"的数据,不是训练时的,结论会是错的而且不会有任何地方报错。
没有这个 tag 的模型版本会被跳过并说明原因,不猜。
"""
import json
import math
import os

from platform_sdk import query

from jobkit import param, rows_of

WINDOW_DAYS = int(param("window_days", "7"))
PSI_THRESHOLD = float(param("psi_threshold", "0.2"))
ONLY_MODEL = (param("model") or "").strip()
DRY_RUN = bool(param("dry_run"))

DRIFT_TABLE = "iceberg.ml.feature_drift"


def ensure_drift_table():
    """结果表。**建在 `ml` 里而不是 `demo`** —— 它按特征列出线上分布的
    统计量,虽然是聚合值,仍然是从个人数据推出来的,该和推理留痕受同一套
    OPA 保护(敏感 schema 只有 platform-team 和拿到专门口子的服务账号能读)。
    """
    query(f"""
        CREATE TABLE IF NOT EXISTS {DRIFT_TABLE} (
            computed_at   TIMESTAMP(6) WITH TIME ZONE,
            model         VARCHAR,
            model_version VARCHAR,
            window_days   INTEGER,
            n_online      BIGINT,
            feature_index INTEGER,
            psi           DOUBLE,
            online_mean   DOUBLE,
            baseline_mean DOUBLE,
            drifted       BOOLEAN
        )
    """)


def baselines_from_mlflow():
    """每个注册模型版本的特征基线。返回 {(model, version): baseline}。

    走 mlflow 客户端的 REST 接口,不碰 artifact —— 平台镜像里是
    `mlflow-skinny`,没有 boto3,下载 artifact 会在运行时才炸
    (而那时候已经跑了一半)。基线本来就是存成 tag 的,见 ADR-087。
    """
    from mlflow import MlflowClient

    if not os.environ.get("MLFLOW_TRACKING_URI"):
        raise SystemExit("MLFLOW_TRACKING_URI 没设置 —— 平台应该注入它,"
                         "见 scripts/render-jobs.py")
    client = MlflowClient()
    out = {}
    for rm in client.search_registered_models():
        if ONLY_MODEL and rm.name != ONLY_MODEL:
            continue
        for mv in client.search_model_versions(f"name='{rm.name}'"):
            # **基线在「产生这个版本的那次 run」的 tag 上,不在模型版本的
            # tag 上。** 这两个是不同的东西,而 `mlflow.set_tag()`(训练脚本
            # 用的那个)写的是 run tag。
            #
            # 2026-09-01 自查时抓到:第一版这里读的是 `mv.tags`,那永远是空的
            # —— 作业会一路正常跑完,然后报"一个带 feature_baseline 的模型版本
            # 都没有",而训练脚本明明写了。**整个功能静默失效,而且报错信息
            # 指向的是训练那一侧。**
            #
            # 读 run tag 也更符合语义:基线是那次训练的产物,和那次 run 绑在
            # 一起;同一个 run 注册出多个版本时,它们共享同一份基线,本来就该
            # 是同一个值。
            raw = (mv.tags or {}).get("feature_baseline")
            if not raw and getattr(mv, "run_id", None):
                try:
                    raw = client.get_run(mv.run_id).data.tags.get("feature_baseline")
                except Exception as exc:   # noqa: BLE001
                    print(f"  读不到 {rm.name} v{mv.version} 对应的 run "
                          f"({mv.run_id}):{str(exc).splitlines()[0][:80]}")
                    raw = None
            if not raw:
                # 没有基线的版本(2026-08-30 之前训练的)不是错误,跳过并说明。
                continue
            try:
                out[(rm.name, str(mv.version))] = json.loads(raw)
            except ValueError as exc:
                print(f"  !! {rm.name} v{mv.version} 的 feature_baseline "
                      f"不是合法 JSON,跳过:{exc}")
    return out


def online_vectors(model_name):
    """窗口内这个模型收到的推理输入,拆成一个个特征向量。

    **只取 request,不取 response** —— 漂移看的是进来的数据。
    payload 是 KServe V2 的 JSON:
        {"inputs": [{"name": ..., "shape": [n, f], "data": [...]}]}
    `data` 是**按行展平的**,要按 shape 的第二维切回去。
    """
    rows = rows_of(query(f"""
        SELECT payload
        FROM iceberg.ml.inference_log
        WHERE event_type = 'request'
          AND inference_service = '{model_name}'
          AND ingest_ts > current_timestamp - INTERVAL '{WINDOW_DAYS}' DAY
    """))
    vectors = []
    for (payload,) in rows:
        try:
            body = json.loads(payload)
        except (ValueError, TypeError):
            # 解析不了的单条不该让整个作业失败,但要能看出来有多少条坏的。
            vectors.append(None)
            continue
        for inp in body.get("inputs") or []:
            data = inp.get("data") or []
            shape = inp.get("shape") or []
            width = int(shape[-1]) if shape else len(data)
            if width <= 0:
                continue
            for i in range(0, len(data) - width + 1, width):
                chunk = data[i:i + width]
                if all(isinstance(v, (int, float)) for v in chunk):
                    vectors.append(chunk)
    bad = sum(1 for v in vectors if v is None)
    good = [v for v in vectors if v is not None]
    if bad:
        print(f"  {bad} 条 payload 解析不了(已跳过)")
    return good


def psi(baseline_edges, online_values):
    """一个特征的 PSI。

    两个刻意的选择:

    1. **空桶不跳过,按一个很小的占比算**。跳过的话,"线上完全没有落进
       某个桶"这种最强的漂移信号会被算成 0 贡献 —— 而它恰恰是最该报的。
       用 1e-6 代替 0,是 PSI 的标准处理(否则 ln(0) 是 -inf)。
    2. **训练侧的占比按等频桶固定是 1/桶数**,不重新数 —— 边界就是按分位数
       切出来的,每桶本来就是 10%。重新数只会引入分箱边界上的取整噪声。
    """
    n_bins = len(baseline_edges) - 1
    if n_bins <= 0 or not online_values:
        return None
    counts = [0] * n_bins
    for v in online_values:
        # 线性扫描:桶只有 10 个,二分不值得,而且边界含 ±inf,二分要多写
        # 一堆边界处理。
        for b in range(n_bins):
            if baseline_edges[b] <= v < baseline_edges[b + 1]:
                counts[b] += 1
                break
    total = len(online_values)
    expected = 1.0 / n_bins
    score = 0.0
    for c in counts:
        actual = max(c / total, 1e-6)
        score += (actual - expected) * math.log(actual / expected)
    return score


def main():
    baselines = baselines_from_mlflow()
    if not baselines:
        raise SystemExit(
            "一个带 feature_baseline 的模型版本都没有。\n"
            "  这不是「没有漂移」,是**算不了** —— 基线是训练时写进 MLflow "
            "tag 的,\n"
            "  2026-08-30 之前训练的版本没有它。重新训一次(scripts/"
            "train_demo_model.py)就有了。")

    if not DRY_RUN:
        ensure_drift_table()

    rows_to_write = []
    for (model_name, version), baseline in sorted(baselines.items()):
        vectors = online_vectors(model_name)
        print(f"\n{model_name} v{version}:窗口内 {len(vectors)} 条推理输入")
        if not vectors:
            print("  没有推理请求 —— 跳过(不是「没漂移」,是没数据)")
            continue

        edges = baseline.get("bin_edges") or []
        n_feat = min(len(edges), min(len(v) for v in vectors))
        if n_feat < len(edges):
            print(f"  !! 基线有 {len(edges)} 个特征,线上只有 "
                  f"{min(len(v) for v in vectors)} 个 —— 只比前 {n_feat} 个。"
                  f"**这本身就值得查**:输入形状变了。")

        drifted = []
        for i in range(n_feat):
            col = [v[i] for v in vectors]
            score = psi(edges[i], col)
            if score is None:
                continue
            online_mean = sum(col) / len(col)
            base_mean = (baseline.get("mean") or [None] * n_feat)[i]
            is_drift = score > PSI_THRESHOLD
            if is_drift:
                drifted.append((i, score))
            rows_to_write.append((model_name, version, len(vectors), i, score,
                                  online_mean, base_mean, is_drift))

        if drifted:
            print(f"  ** {len(drifted)} 个特征 PSI 超过 {PSI_THRESHOLD}:")
            for i, score in sorted(drifted, key=lambda t: -t[1]):
                print(f"       特征 {i}: PSI={score:.3f}")
        else:
            print(f"  所有特征 PSI 都在 {PSI_THRESHOLD} 以内")

    if DRY_RUN:
        print(f"\n[dry-run] 算出 {len(rows_to_write)} 行,没有写表")
        return
    if not rows_to_write:
        # **不当成失败。** 窗口内没有推理请求是完全正常的(demo 环境、
        # 或者这个模型本来就没流量),把作业标红会让人学会忽略它。
        print("\n没有可写的结果(窗口内没有推理请求)")
        return

    values = ",".join(
        f"(current_timestamp, '{m}', '{v}', {WINDOW_DAYS}, {n}, {i}, "
        f"{s}, {om}, {'NULL' if bm is None else bm}, {str(d).lower()})"
        for m, v, n, i, s, om, bm, d in rows_to_write)
    query(f"INSERT INTO {DRIFT_TABLE} VALUES {values}")
    print(f"\n写入 {DRIFT_TABLE}:{len(rows_to_write)} 行")


main()
