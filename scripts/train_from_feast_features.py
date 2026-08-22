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
    feast==0.65.0 pyspark==3.5.9 mlflow-skinny scikit-learn pandas redis
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
#
# **2026-08-20 真实踩坑**:本来想直接用 Feast 自己的
# get_spark_session_or_start_new_with_repoconfig() 拿 SparkSession——这样
# 能保证用来查 entity_df 的会话和 get_historical_features() 内部用的是
# 同一套配置。但 feature_store.yaml 里的 `spark.jars.packages` 靠 Spark
# 运行时从 Maven Central 现拉 Iceberg/hadoop-aws/aws-sdk 这三个 jar,
# cloud-full 云主机直连 Maven Central 会真的卡死不动(和这个项目到处
# 踩过的"直连境外源卡死"是同一类问题,加 spark.jars.repositories 指向
# 阿里云镜像也没用,Ivy 的默认解析器链路不会因为多了候选源就绕开卡住的
# 那个)。改成在镜像构建期(GitHub Actions,境外 runner,直连 Maven
# Central 没问题)把这三个 jar 下载好打进镜像(见
# apps/argo-workflows-training-image/Dockerfile 的 /opt/spark-jars/),
# 运行时直接从本地加载,不联网。
#
# 用自己建的 SparkSession(不是走 feast 那个函数)是关键——PySpark 的
# `SparkSession.builder.config(...).getOrCreate()` 如果 JVM 里已经有一个
# 活跃的 session,会直接复用那个已有的,新的 config 不会生效。利用这条
# 特性:提前用本地 jar 路径建好 session,feast 内部再调用它自己那个
# "构建/复用 session" 的函数时,拿到的就是这个已经配好本地 jar 的
# session,不会再触发一次网络下载。其它 catalog/S3A 配置项直接从
# feature_store.yaml 解析出来的 store.config.offline_store.spark_conf
# 里读,不在这个脚本里重复写一遍容易漂移的配置。
from feast import FeatureStore  # noqa: E402  (放在环境变量检查之后,懒加载重依赖)
from pyspark.sql import SparkSession  # noqa: E402

store = FeatureStore(repo_path=FEAST_REPO_PATH)
_spark_conf = dict(store.config.offline_store.spark_conf)
_spark_conf.pop("spark.jars.packages", None)
_spark_conf.pop("spark.jars.repositories", None)
_spark_conf["spark.jars"] = ",".join(
    [
        "/opt/spark-jars/iceberg-spark-runtime-3.5_2.12-1.10.0.jar",
        "/opt/spark-jars/hadoop-aws-3.3.4.jar",
        "/opt/spark-jars/aws-java-sdk-bundle-1.12.262.jar",
    ]
)
_builder = SparkSession.builder
for _k, _v in _spark_conf.items():
    _builder = _builder.config(_k, _v)
spark = _builder.getOrCreate()

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

    # serialization_format="pickle":同 train_demo_model.py 里的教训——
    # MLflow 3.x 默认改用 skops(更安全,避免 pickle 反序列化风险),但
    # 这个精简训练镜像没装 skops(mlflow-skinny 不带),KServe mlserver
    # runtime 镜像自带的 mlflow 客户端版本也较老、不认 skops 格式。换回
    # pickle 是为了兼容部署目标和这个镜像本身的依赖范围,不是否定 skops
    # 更安全这个前提。
    mlflow.sklearn.log_model(
        model,
        name="model",
        registered_model_name=registered_model_name,
        serialization_format="pickle",
    )
    print(
        f"完成:从 Feast 取了 {len(features_df)} 行历史特征,训练完成,"
        f"accuracy={accuracy:.3f},已注册进 MLflow Model Registry"
        f"({registered_model_name})。"
    )
    # demo 数据只有 10 行(scripts/08-create-demo-data.sh 里写死的 10 笔
    # 订单)、region 有 4 个取值,测试集固定切 1 条——这个规模下 accuracy
    # 只有 0 或 1 两种可能,是个没有统计意义的数字。**不加这句提示的话,
    # 在 MLflow UI 上看到 accuracy=0.000 很容易被误读成"平台坏了"**,
    # 实际上链路是通的,只是数据量决定了这个指标没法看。真要评估模型好坏,
    # 得先有真实规模的数据集,不是调这个脚本的参数。
    if len(features_df) < 100:
        print(
            f"  ⚠️  注意:训练集只有 {len(features_df)} 行(demo 数据规模),"
            f"上面这个 accuracy 没有统计意义——它只反映\"链路跑通了\","
            f"不反映\"模型好不好\"。这是预期行为,不是故障。"
        )
