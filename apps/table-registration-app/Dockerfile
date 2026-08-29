# 见 docs/BACKLOG.md 2.1("引入镜像构建流程,停止用运行时 pip install")。
# 之前是 python:3.12-slim 官方镜像 + 容器启动时现装 pip 依赖,这次改成
# 构建期固化依赖和源码,和 apps/permission-request-app/Dockerfile 同一个
# 模式(这个 app 不调 git,不需要装 git 二进制)。
FROM python:3.12-slim@sha256:876416ecde9aca2bcc90e1fb0c7a9500bbf749f5788b70f82d4c5a5c2357f8b4

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

COPY src/app.py .
# 登录身份/组的解析。权威源是 shared/flask_identity.py,这里是它的副本
# (scripts/check-duplicated-sources.py 在 CI 里保证不漂移)。**漏拷这一行
# 镜像会直接起不来**(ModuleNotFoundError),不是"少个功能"那么轻。
COPY src/identity.py .

EXPOSE 8080
CMD ["python3", "app.py"]
