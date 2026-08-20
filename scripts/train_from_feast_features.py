"""
BACKLOG P1.7 的最后一段空白:"notebook → Feast 特征 → Argo Workflows 训练
→ MLflow 记录"这条完整链路,之前每一段都单独验证过,但训练用的是
`train_demo_model.py`(`sklearn.datasets.make_classification` 生成的
合成数据),从来没有真的从 Feast 取过特征——这个脚本才是那条链路里
缺的一环:用 `FeatureStore.get_historical_features()` 取 point-in-time
正确的历史特征,不是自己另外拼一份数据。

复用 `iceberg.demo.orders` 这份 demo 数据(和 `scripts/feast_feature_repo/
definitions.py` 定义的 `customer_order_features` 是同一批数据),训练
一个玩具分类器:用 `amount`(订单金额)预测 `region`(地区)。这不是一个
有业务意义的任务,和 `train_demo_model.py` 用合成数据一样,目的是验证
"链路通不通",不是"模型有没有用"。

和 `train_demo_model.py` 同样的约定:完全靠环境变量配置,不硬编码
localhost,同一份文件能在人手动跑(port-forward)和 Argo Workflows
里跑(集群内部 Service DNS)两种场景下工作,调用方负责设置这些变量。

依赖(见 apps/argo-workflows-training-image/Dockerfile):
    feast[spark]==0.65.0 pyspark==3.5.9 mlflow-skinny scikit-learn pandas
"""
import os

import mlflow
import mlflow.sklearn
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

for required in ("MLFLOW_TRACKING_URI", "MLFLOW_S3_ENDPOINT_URL"):
    if not os.environ.get(required):
        raise SystemExit(f"环境变量 {required} 没设置,调用方必须显式指定,不用默认值兜底")

FEAST_REPO_PATH = os.environ.get("FEAST_REPO_PATH", "/feature_repo")
experiment_name = os.environ.get("MLFLOW_EXPERIMENT_NAME", "demo-feast-classification")
registered_model_name = os.environ.get("MLFLOW_REGISTERED_MODEL_NAME", "demo-region-classifier")
n_estimators = int(os.environ.get("TRAIN_N_ESTIMATORS", "50"))
random_state = int(os.environ.get("TRAIN_RANDOM_STATE", "42"))

mlflow.set_experiment(experiment_name)

# feature_store.yaml 里的 offline_store 是 Spark(读 iceberg.demo.orders)。
# 用 Feast 自己提供的 get_spark_session_or_start_new_with_repoconfig()
# 拿 SparkSession(而不是自己另起一个、重复一遍 spark_conf 里那些 Iceberg/
# S3A 配置)——这是 SparkOfflineStore 内部实际调用来创建/复用会话的同一个
# 函数,保证这里用来查 entity_df 的会话和 get_historical_features() 内部
# 用的是同一套 catalog/S3 配置,不会出现"两个 Spark 会话配置不一致"的
# 隐藏坑。
from feast import FeatureStore  # noqa: E402  (放在环境变量检查之后,懒加载重依赖)
from feast.infra.offline_stores.contrib.spark_offline_store.spark import (  # noqa: E402
    get_spark_session_or_start_new_with_repoconfig,
)

store = FeatureStore(repo_path=FEAST_REPO_PATH)
spark = get_spark_session_or_start_new_with_repoconfig(store.config.offline_store)

# entity_df 需要每个实体一行 + 一个 event_timestamp(point-in-time 正确性
# 靠这个字段保证,不是"取最新值"这种简化写法)——直接从同一份 Spark
# offline store 里查 iceberg.demo.orders 本身拿到,不手写死一批客户名单,
# 这份 demo 数据以后如果变了,这个脚本不用跟着改。
entity_df = spark.sql(
    "SELECT customer_name, CAST(order_date AS TIMESTAMP) AS event_timestamp "
    "FROM iceberg.demo.orders"
).toPandas()

features_df = store.get_historical_features(
    entity_df=entity_df,
    features=[
        "customer_order_features:region",
        "customer_order_features:product",
        "customer_order_features:amount",
    ],
).to_df()

if len(features_df) < 4 or features_df["region"].nunique() < 2:
    raise SystemExit(
        f"从 Feast 取到的历史特征只有 {len(features_df)} 行、"
        f"{features_df['region'].nunique()} 个 region 取值,数据太少训不出一个有意义的"
        "分类任务——先确认 iceberg.demo.orders 里的 demo 数据是不是还在"
        "(scripts/08-create-demo-data.sh),不是这个脚本本身的 bug。"
    )

product_encoder = LabelEncoder()
region_encoder = LabelEncoder()
X = pd.DataFrame(
    {
        "amount": features_df["amount"],
        "product_encoded": product_encoder.fit_transform(features_df["product"]),
    }
)
y = region_encoder.fit_transform(features_df["region"])

# 数据量很小(demo 数据本来就只有几笔订单),test_size 用比例会导致某个
# 切分里某个类别一个样本都没有——固定切 1 条做测试集,够验证"链路能跑完
# 并且算出一个 accuracy 数字"就行,不是在追求这个数字有统计意义。
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=1, random_state=random_state
)

with mlflow.start_run():
    mlflow.log_param("n_estimators", n_estimators)
    mlflow.log_param("feature_source", "feast:customer_order_features")
    mlflow.log_param("training_rows", len(features_df))

    model = RandomForestClassifier(n_estimators=n_estimators, random_state=random_state)
    model.fit(X_train, y_train)
    accuracy = accuracy_score(y_test, model.predict(X_test))
    mlflow.log_metric("accuracy", accuracy)

    mlflow.sklearn.log_model(
        model,
        name="model",
        registered_model_name=registered_model_name,
    )
    print(
        f"完成:从 Feast 取了 {len(features_df)} 行历史特征,训练完成,"
        f"accuracy={accuracy:.3f},已注册进 MLflow Model Registry"
        f"({registered_model_name})。"
    )
