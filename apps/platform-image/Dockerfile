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
# 构建时的 pip 源。**默认阿里云**是给"在境内云主机上手工构建"那条路径准备
# 的;GitHub Actions 的 runner 在境外,连阿里云反而慢/不通,所以 CI 里用
# build-arg 覆盖成默认 PyPI(见 .github/workflows/build-images.yml)。
#
# 注意这只影响**构建时**。容器里 /etc/pip.conf 写的运行时索引仍然是阿里云
# + MinIO 内部包索引 —— 那是给集群里的人用的,集群在境内。
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

FROM quay.io/jupyterhub/k8s-singleuser-sample:4.4.1

# FROM 之后要重新声明才能在 RUN 里用(ARG 的作用域被 FROM 截断,
# 这是 Dockerfile 的规矩,不是笔误)。
ARG PIP_INDEX_URL

USER root

# platform_sdk 本身在构建时从本仓库拷进去装(不是发布到 PyPI 再拉下来——
# 这个项目还没有私有 PyPI registry,而且现阶段 SDK 变化会很频繁,拷源码
# 装是最快的反馈循环;等 SDK 稳定、这个仓库开源之后,可以考虑发 PyPI)。
# 内部包索引(ADR-083)。**配进镜像里,使用者不用记 --extra-index-url**
# ——那种"每次都要多敲一个参数"的设计,实际结果是没人用。
#
# `trusted-host` 是必需的:索引走的是集群内 http(MinIO 没有对外 TLS),
# 不加这一行 pip 会拒绝从 http 源安装。这是集群内部地址,不经过公网。
#
# extra-index-url 而不是 index-url:内部包找不到时**要能回退到公共源**,
# 否则任何一个普通依赖都装不了。
RUN printf '%s\n' \
    '[global]' \
    'index-url = https://mirrors.aliyun.com/pypi/simple/' \
    'extra-index-url = http://minio.minio.svc.cluster.local:9000/packages/simple/' \
    'trusted-host = minio.minio.svc.cluster.local' \
    > /etc/pip.conf

# 2026-08-29:第三方依赖搬进 requirements.txt。
#
# **不只是为了整洁** —— `scripts/render-jobs.py` 现在会拿 `jobs/*/job.yaml`
# 里声明的 `requires` 和这份清单对照:声明了镜像里没有的包,**CI 直接红**,
# 而不是等作业半夜跑到 import 那一行才 ModuleNotFoundError。要让这个校验
# 可信,清单就必须是一份机器读得懂的东西,不能是 Dockerfile 里一串续行。
#
# 加新依赖 = 改这份文件 + 重建镜像。**不要在作业里运行时 pip install** ——
# 那是这个项目明确记过的反模式(每次 pod 重启重装一遍,遇到网络限速直接
# 卡死探针预算,2026-08-16 真实故障:SIGKILL,5 分 44 秒整)。
COPY apps/platform-image/requirements.txt /tmp/requirements.txt
COPY platform-sdk /tmp/platform-sdk
RUN pip install --no-cache-dir -i "${PIP_INDEX_URL}" \
        /tmp/platform-sdk[submit] \
        -r /tmp/requirements.txt \
    && rm -rf /tmp/platform-sdk /tmp/requirements.txt

# NB_USER 是 jupyter/docker-stacks 系列镜像(k8s-singleuser-sample 由此
# 派生)的标准 ENV,基础镜像里已经设好,这里不用硬编码成 jovyan。
USER ${NB_USER}
