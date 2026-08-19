"""
端到端 demo(AI/ML 主线):训练一个真实的 scikit-learn 模型,把参数/指标/
模型本身记到 MLflow(后端 Postgres,artifact 存 MinIO),再注册进 Model
Registry。

2026-08-19 改成完全靠环境变量配置(不在代码里硬编码 localhost,也不用
额外的 argparse)——MLFLOW_TRACKING_URI/MLFLOW_EXPERIMENT_NAME 本来就是
MLflow 客户端自己认的标准环境变量(不调用 mlflow.set_tracking_uri()/
set_experiment() 时会自动读),MLFLOW_S3_ENDPOINT_URL/AWS_ACCESS_KEY_ID/
AWS_SECRET_ACCESS_KEY 是 boto3/MLflow S3 客户端的标准变量。这样同一份
脚本文件不用改一行代码就能在两种场景下跑:
  - 人手动跑(scripts/09-train-demo-model.sh):变量指向 port-forward 到
    localhost 的地址。
  - Argo Workflows 编排跑(apps/definitions/argo-training-workflow-
    template.yaml):变量指向集群内部 Service DNS,不需要 port-forward。
调用方(shell 脚本 / WorkflowTemplate)负责设置这些变量,这个文件本身
不关心自己是被谁调用的。

依赖(见同目录 apps/argo-workflows-training-image/Dockerfile,已经打进
训练用的容器镜像,不是运行时现装):
    mlflow-skinny scikit-learn boto3
"""
import os

import mlflow
import mlflow.sklearn
from sklearn.datasets import make_classification
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score

for required in ("MLFLOW_TRACKING_URI", "MLFLOW_S3_ENDPOINT_URL"):
    if not os.environ.get(required):
        raise SystemExit(f"环境变量 {required} 没设置,调用方必须显式指定,不用默认值兜底")

# 不显式调用 mlflow.set_tracking_uri()——让 mlflow 客户端自己读
# MLFLOW_TRACKING_URI 这个标准环境变量,调用方(shell 脚本 / Argo
# WorkflowTemplate)负责设成各自场景该用的地址。
experiment_name = os.environ.get("MLFLOW_EXPERIMENT_NAME", "demo-classification")
registered_model_name = os.environ.get("MLFLOW_REGISTERED_MODEL_NAME", "demo-rf-classifier")
n_estimators = int(os.environ.get("TRAIN_N_ESTIMATORS", "100"))
max_depth = int(os.environ.get("TRAIN_MAX_DEPTH", "6"))
random_state = int(os.environ.get("TRAIN_RANDOM_STATE", "42"))

mlflow.set_experiment(experiment_name)

X, y = make_classification(n_samples=2000, n_features=20, n_informative=10, random_state=random_state)
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=random_state)

params = {"n_estimators": n_estimators, "max_depth": max_depth, "random_state": random_state}

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
        registered_model_name=registered_model_name,
        serialization_format="pickle",
    )

    print("RUN_ID:", run.info.run_id)
    print("ACCURACY:", acc)
    print("F1:", f1)
    print("EXPERIMENT_ID:", run.info.experiment_id)
