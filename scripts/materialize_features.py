"""
多步骤 DAG 的第一步:把 Feast 的离线特征物化进在线存储(Redis)。

**为什么不直接用 `feast materialize-incremental` 这个 CLI**:CLI 会按
`feature_store.yaml` 里的 `spark.jars.packages` 让 Spark 运行时去 Maven
Central 现拉 Iceberg/hadoop-aws/aws-sdk 三个 jar——cloud-full 云主机直连
Maven Central 会真的卡死不动(2026-08-20 实测,几百 MB 的
aws-java-sdk-bundle 下载进度停在 0 字节超过 8 分钟,加阿里云镜像候选源
也没用)。这个训练镜像已经在构建期把三个 jar 打进 `/opt/spark-jars/`
(见 `apps/argo-workflows-training-image/Dockerfile`),所以这里走
Python API + 自己建 SparkSession 指向本地 jar,完全不联网。

用 `SparkSession.builder...getOrCreate()` 先建好会话、再让 feast 复用它
这个手法,和 `scripts/train_from_feast_features.py` 是同一套(那份文件
里有完整的原理说明,这里不重复抄一遍,只标明是刻意一致的)。
"""
import os
from datetime import datetime, timedelta, timezone

for required in ("MLFLOW_S3_ENDPOINT_URL",):
    if not os.environ.get(required):
        raise SystemExit(f"环境变量 {required} 没设置,调用方必须显式指定,不用默认值兜底")

FEAST_REPO_PATH = os.environ.get("FEAST_REPO_PATH", "/feature_repo")
# 物化窗口:默认回看 3650 天,和 definitions.py 里 FeatureView 的 ttl 对齐
# (demo 数据是 2026-07 的固定几笔历史订单,窗口给小了会一条都物化不到)。
lookback_days = int(os.environ.get("FEAST_MATERIALIZE_LOOKBACK_DAYS", "3650"))

from feast import FeatureStore  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402

store = FeatureStore(repo_path=FEAST_REPO_PATH)
_conf = dict(store.config.offline_store.spark_conf)
_conf.pop("spark.jars.packages", None)
_conf.pop("spark.jars.repositories", None)
_conf["spark.jars"] = ",".join(
    [
        "/opt/spark-jars/iceberg-spark-runtime-3.5_2.12-1.10.0.jar",
        "/opt/spark-jars/hadoop-aws-3.3.4.jar",
        "/opt/spark-jars/aws-java-sdk-bundle-1.12.262.jar",
    ]
)
_builder = SparkSession.builder
for _k, _v in _conf.items():
    _builder = _builder.config(_k, _v)
_builder.getOrCreate()

end = datetime.now(timezone.utc)
start = end - timedelta(days=lookback_days)
print(f"物化窗口:{start.isoformat()} → {end.isoformat()}")

store.materialize(start_date=start, end_date=end)

print(f"完成:离线特征已物化进在线存储(Redis),窗口 {lookback_days} 天。")
