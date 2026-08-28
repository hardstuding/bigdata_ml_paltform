"""训练模板:取数 → 训练 → 记实验 → 注册模型。

这是算法工程师在这个平台上的黄金路径。它和 `scripts/train_demo_model.py`
的区别是:那个是平台自己的验证脚本(还要被 Argo WorkflowTemplate 复用),
这份是给人照着改的**起点**,只保留必要的骨架。

照着改的时候动三处:取数的 SQL、特征/标签怎么切、模型怎么建。
"""

import os

from platform_sdk import mlflow_setup, query

EXPERIMENT = os.environ.get("EXPERIMENT_NAME", "my-experiment")
MODEL_NAME = os.environ.get("REGISTERED_MODEL_NAME", "my-model")

# 1. 取数。不用填连接串——SDK 已经接好 Trino(ADR-058)。
df = query("SELECT region, product, amount FROM iceberg.demo.orders")
if len(df) < 5:
    raise SystemExit(f"!! 只取到 {len(df)} 行,不够训练。先确认 scripts/08 建过 demo 数据。")

# 2. 切特征和标签。这里用最朴素的做法,替换成你自己的。
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

X = df[["amount"]].astype(float)
y = df["product"]
# stratify 在小样本上会报错,demo 数据只有 10 行,所以不加;真实数据要加。
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3, random_state=42)

# 3. 训练 + 记实验 + 注册模型。
mlflow = mlflow_setup(EXPERIMENT)
with mlflow.start_run(run_name="train-model-template"):
    model = RandomForestClassifier(n_estimators=20, random_state=42)
    model.fit(X_train, y_train)
    acc = model.score(X_test, y_test)

    mlflow.log_param("n_estimators", 20)
    mlflow.log_metric("accuracy", acc)
    # registered_model_name 一给,模型就进注册表,后面 KServe 直接从注册表拉。
    # **不注册的话训练产物只是一次实验记录,上不了线。**
    mlflow.sklearn.log_model(model, name="model", registered_model_name=MODEL_NAME)

print(f"训练完成:accuracy={acc:.3f},模型已注册为 {MODEL_NAME}")

# 上线是**另外两步**,不会因为训练完就自动发生:
#   ./scripts/41-approve-model.sh <模型名> <版本号> "批注"   # 盖章
#   ./scripts/11-deploy-demo-inference-service.sh            # 部署被批准的那版
# scripts/11 只认带 approval=approved 标记的版本,没盖章直接拒绝(ADR-080)。
# **注册 ≠ 批准 ≠ 上线**,是三件事。
