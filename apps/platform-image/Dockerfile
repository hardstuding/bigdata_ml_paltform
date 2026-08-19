# 统一开发镜像(ADR-058)——JupyterHub 的 singleuser pod 和调度器
# (Argo Workflows / Airflow KubernetesPodOperator)的任务 pod 用同一个
# 镜像。这是"交互开发和调度执行环境一致"这条设计原则的物理保证:不是
# "两边配得差不多",是同一个镜像 tag。
#
# 基础镜像选 JupyterHub 官方的 singleuser 样例镜像(chart 4.4.1 的默认值,
# `helm show values jupyterhub/jupyterhub --version 4.4.1` 核实过,不是
# 猜的)——它已经装好 JupyterLab、`jovyan` 用户、常见科学计算库,直接
# 拿来当 singleuser 镜像用;调度任务不需要 JupyterLab 本身,但多带这层
# 不影响正确性,换来的是"镜像只有一个"这个更重要的属性。
#
# 依赖装法沿用这个仓库已经踩平的两个坑:
#   1. 直连 PyPI 装包在这片网络里会被限速到几十 KB/s(和
#      apps/feast/feature-server-image/Dockerfile、
#      apps/argo-workflows-training-image/Dockerfile 同一个问题),换阿里云
#      镜像源。
#   2. mlflow-skinny 版本对齐已部署的 MLflow server(ghcr.io/mlflow/mlflow:
#      v3.14.0-full,见 apps/definitions/mlflow.yaml),不是随便挑的。
#
# 构建时 build context 必须是仓库根目录(不是这个文件所在目录),因为下面
# `COPY platform-sdk` 引用的是仓库根下的 platform-sdk/ 目录:
#   docker build -f apps/platform-image/Dockerfile -t local/platform-runtime:0.1.0 .
FROM quay.io/jupyterhub/k8s-singleuser-sample:4.4.1

USER root

# platform_sdk 本身在构建时从本仓库拷进去装(不是发布到 PyPI 再拉下来——
# 这个项目还没有私有 PyPI registry,而且现阶段 SDK 变化会很频繁,拷源码
# 装是最快的反馈循环;等 SDK 稳定、这个仓库开源之后,可以考虑发 PyPI)。
COPY platform-sdk /tmp/platform-sdk
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
        /tmp/platform-sdk[submit] \
        mlflow-skinny==3.14.0 \
        scikit-learn==1.5.2 \
        pandas==2.2.3 \
        trino==0.338.0 \
        jupyterlab-git \
    && rm -rf /tmp/platform-sdk

# NB_USER 是 jupyter/docker-stacks 系列镜像(k8s-singleuser-sample 由此
# 派生)的标准 ENV,基础镜像里已经设好,这里不用硬编码成 jovyan。
USER ${NB_USER}
