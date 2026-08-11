"""
端到端 demo(AI/ML 主线):训练一个真实的 scikit-learn 模型,把参数/指标/
模型本身记到 MLflow(后端 Postgres,artifact 存 MinIO),再注册进 Model
Registry。

用 scripts/09-train-demo-model.sh 跑,不要直接跑这个文件——外层脚本负责
建 port-forward(到 MLflow/MinIO 的集群内部 Service,不经过 oauth2-proxy;
oauth2-proxy 只挡浏览器访问的 Ingress 入口,训练任务这类服务到服务场景
直接连内部 Service 就行,和 Trino 的服务账号是同一个思路,但更简单
——MLflow 自己的 Service 前面本来就没有另外加认证层)和 AWS_* 环境变量。

依赖(不在项目的 image-cache 里,是本机 Python 环境的依赖):
    pip install mlflow-skinny scikit-learn skops boto3
"""
import mlflow
import mlflow.sklearn
from sklearn.datasets import make_classification
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score

mlflow.set_tracking_uri("http://localhost:5000")
mlflow.set_experiment("demo-classification")

X, y = make_classification(n_samples=2000, n_features=20, n_informative=10, random_state=42)
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

params = {"n_estimators": 100, "max_depth": 6, "random_state": 42}

with mlflow.start_run(run_name="rf-baseline") as run:
    mlflow.log_params(params)

    model = RandomForestClassifier(**params)
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    acc = accuracy_score(y_test, preds)
    f1 = f1_score(y_test, preds)
    mlflow.log_metric("accuracy", acc)
    mlflow.log_metric("f1_score", f1)

    # serialization_format="pickle":MLflow 3.x 默认改用 skops(更安全,避免
    # pickle 反序列化风险),但 KServe mlserver runtime 镜像(seldonio/mlserver
    # :1.7.1)自带的 mlflow 客户端版本较老,不认 skops 格式,加载会报
    # "Unrecognized serialization format: skops"。这里换回 pickle 是为了兼容
    # 部署目标,不是否定 skops 更安全这个前提——如果以后 mlserver 镜像升级
    # 支持 skops 了,应该改回默认。
    mlflow.sklearn.log_model(
        model,
        name="model",
        registered_model_name="demo-rf-classifier",
        serialization_format="pickle",
    )

    print("RUN_ID:", run.info.run_id)
    print("ACCURACY:", acc)
    print("F1:", f1)
    print("EXPERIMENT_ID:", run.info.experiment_id)
