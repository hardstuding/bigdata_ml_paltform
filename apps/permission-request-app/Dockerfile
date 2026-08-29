# 见 docs/decisions/032-permission-request-app.md 和 docs/BACKLOG.md 2.1
# ("引入镜像构建流程,停止用运行时 pip install")。之前是 python:3.12-slim
# 官方镜像 + 容器启动时现装 git/pip 依赖(deployment.yaml 里那段 shell),
# 2026-08-16 一晚就因为这个模式产生过四次真实故障(见 BACKLOG 2.1 的详细
# 记录),这次改成构建期把依赖和源码都固化进镜像。
#
# 用官方 python:3.12-slim 基础镜像(和之前运行时用的是同一个 digest,只是
# 现在挪到构建期装依赖,不是运行时才装)。
FROM python:3.12-slim@sha256:876416ecde9aca2bcc90e1fb0c7a9500bbf749f5788b70f82d4c5a5c2357f8b4

# app.py 里 apply_to_git()/apply_grant_to_git()/reclaim_expired()/
# transfer() 这几个函数用 subprocess 调 git clone/commit/push,运行时
# 镜像本身需要带 git 二进制(和依赖 pip 包不是一回事,这个装不进
# requirements.txt)。用阿里云镜像源装(这几行和这个仓库其它 Dockerfile
# 换源的原因一样:直连 deb.debian.org 在这片网络里经常连不上或者巨慢,
# 见 docs/operations/troubleshooting.md 相关记录)。
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/*.sources \
    && apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

# 只拷 app.py 本身进镜像——employees.csv 是可以直接改的 demo 数据(见
# apps/permission-request-app/manifests/app-configmap.yaml 顶部注释),
# 继续走 ConfigMap 挂载,不固化进镜像,改数据不用重新构建。
COPY src/app.py .
# 登录身份/组的解析。权威源是 shared/flask_identity.py,这里是它的副本
# (scripts/check-duplicated-sources.py 在 CI 里保证不漂移)。**漏拷这一行
# 镜像会直接起不来**(ModuleNotFoundError),不是"少个功能"那么轻。
COPY src/identity.py .

EXPOSE 8080
CMD ["python3", "app.py"]
