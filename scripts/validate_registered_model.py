"""
多步骤 DAG 的第三步:模型上线前的门禁校验(`docs/roles.md` 算法工程师
那一节点名的"多步骤 DAG"缺口里的最后一步)。

**为什么这一步不是"看 accuracy 达没达标"**:这个平台的 demo 数据只有
10 行(`scripts/08-create-demo-data.sh` 写死的 10 笔订单)、region 有 4 个
取值、测试集只有 1 条——accuracy 只可能是 0 或 1,拿它当门禁阈值是自欺欺人
(要么永远过不了,要么把阈值调到形同虚设)。**在这个数据规模下唯一诚实、
且真的有用的门禁是"这个模型能不能被加载出来、能不能做出形状正确的预测"**
——也就是"它到底能不能部署",这恰好是 KServe 上线的真实前置条件,不是
装饰性的检查。以后真有生产规模数据集了,再在这一步补真正的指标阈值门禁,
那时候阈值才有意义。

做的事:
1. 从 MLflow Model Registry 取指定模型的最新版本(不是某个写死的版本号)
2. 真的把它 load 出来(会从 MinIO 下载 artifact,这一步能抓出序列化格式
   不兼容这类问题——这个项目已经真实踩过 skops/pickle 那个坑)
3. 用一个和训练时同 schema 的样本做一次真实推理
4. 校验输出形状/取值合法
任何一步失败就非零退出,Argo Workflows 会把这一步判定为失败,后续步骤
(比如自动上线到 KServe)不会执行。

环境变量约定和 `train_from_feast_features.py` 一致,调用方负责设置。
"""
import os
import sys

import mlflow
import numpy as np

for required in ("MLFLOW_TRACKING_URI", "MLFLOW_S3_ENDPOINT_URL"):
    if not os.environ.get(required):
        raise SystemExit(f"环境变量 {required} 没设置,调用方必须显式指定,不用默认值兜底")

model_name = os.environ.get("MLFLOW_REGISTERED_MODEL_NAME", "demo-region-classifier")

client = mlflow.MlflowClient()

# 取最新版本:不写死版本号,让这一步永远校验"刚训出来的那个"。
versions = client.search_model_versions(f"name='{model_name}'")
if not versions:
    raise SystemExit(f"门禁失败:Model Registry 里找不到任何名为 {model_name} 的模型版本")
latest = max(versions, key=lambda v: int(v.version))
print(f"取到最新版本:{model_name} v{latest.version}(status={latest.status})")

if latest.status != "READY":
    raise SystemExit(
        f"门禁失败:v{latest.version} 的 status 是 {latest.status},不是 READY"
        "——artifact 可能还在上传,或者注册过程出错了"
    )

# 真的 load 出来。这一步会从 MinIO 下载 artifact 并反序列化,能抓出
# "训练时用的序列化格式,部署环境读不了"这类问题——这个项目真实踩过
# (MLflow 3.x 默认 skops,KServe 的 mlserver 镜像不认)。
try:
    model = mlflow.sklearn.load_model(f"models:/{model_name}/{latest.version}")
except Exception as exc:
    raise SystemExit(f"门禁失败:模型加载不出来(部署到 KServe 也会是同样的结果)—— {exc}")
print(f"模型加载成功:{type(model).__name__}")

# 用和训练时同 schema 的样本做一次真实推理。训练脚本的特征是
# [amount(float), product_encoded(int)],这里构造两条边界样本。
sample = np.array([[120.50, 0], [45.75, 2]])
try:
    preds = model.predict(sample)
except Exception as exc:
    raise SystemExit(f"门禁失败:模型加载出来了但推理报错 —— {exc}")

if len(preds) != len(sample):
    raise SystemExit(f"门禁失败:输入 {len(sample)} 条,输出 {len(preds)} 条,形状对不上")
if not all(np.isfinite(np.asarray(preds, dtype=float))):
    raise SystemExit(f"门禁失败:预测结果里有 NaN/Inf —— {preds}")

print(f"推理成功,预测结果:{preds.tolist()}")
print(
    f"门禁通过:{model_name} v{latest.version} 可加载、可推理、输出合法,"
    "满足上线到 KServe 的前置条件。"
)
print(
    "  注:这一步**不校验模型精度**——demo 数据规模下 accuracy 没有统计"
    "意义(见 train_from_feast_features.py 顶部说明)。有真实规模数据集"
    "之后,应该在这里补真正的指标阈值门禁。"
)
sys.exit(0)
