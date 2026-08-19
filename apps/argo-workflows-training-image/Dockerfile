# 给 Argo Workflows 编排训练用的镜像——scripts/train_demo_model.py 需要
# mlflow-skinny/scikit-learn/boto3,官方 python 基础镜像不带,不在容器
# 启动时现装(这个项目已经踩过多次"运行时 pip install"的坑,见
# docs/BACKLOG.md P2.1),在镜像构建时一次装好。
#
# 2026-08-19 真实踩坑(和 apps/feast/feature-server-image/Dockerfile 那次
# 一样):直连官方 PyPI 装包在这片网络里会被限速到几十 KB/s,换成阿里云
# PyPI 镜像后能到 1MB/s 以上量级,构建时间从"数十分钟到数小时"降到几
# 分钟。这不是这个 Dockerfile 独有的坑,以后这个项目里任何新增的自定义
# 镜像装 Python 依赖都应该默认带上这个 mirror 参数,不用每次重新踩一遍。
FROM python:3.11-slim

# mlflow-skinny 版本和已部署的 MLflow server(ghcr.io/mlflow/mlflow:
# v3.14.0-full,见 apps/definitions/mlflow.yaml)对齐,不是随便挑的——
# 四个版本号都在 PyPI 上核实过存在(`pip index versions`,不是猜的)。
#
# 2026-08-19 真实故障(第一次跑这个镜像才发现,本机手动跑那个脚本时
# 从没暴露过):mlflow.sklearn.log_model() 内部会 import pandas 做 schema
# 推断,但 mlflow-skinny(特意排除掉 pandas 这类重依赖的精简版客户端)
# 不会自动带上它,报 ModuleNotFoundError。本机能跑通是因为本机是
# anaconda 环境,pandas 作为基础环境的一部分早就装着,把这个隐藏依赖
# 掩盖掉了——这类"本机凑巧能跑、干净容器里跑不起来"的坑,只有真正
# 在干净环境里跑一次才能发现,这也是这次要做"Argo Workflows 编排训练"
# 的价值之一,不只是把手动步骤自动化。
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    mlflow-skinny==3.14.0 \
    scikit-learn==1.5.2 \
    pandas==2.2.3 \
    boto3==1.35.36

# train_demo_model.py 不打进镜像——通过 ConfigMap 挂载(见
# apps/argo-workflows-training-image/manifests/script-configmap.yaml),
# 和 apps/spark-iceberg-demo 是同一个模式:改脚本逻辑不用重新构建镜像,
# 只有改 Python 依赖(这个 Dockerfile)才需要重新构建。
