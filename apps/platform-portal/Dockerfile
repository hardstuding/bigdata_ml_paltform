# 见 docs/BACKLOG.md 2.1("引入镜像构建流程,停止用运行时 pip install")。
# 之前是 python:3.12-slim 官方镜像 + 容器启动时现装 pip 依赖,这次改成
# 构建期固化依赖和源码,和 apps/permission-request-app/Dockerfile 同一个
# 模式。
FROM python:3.12-slim@sha256:876416ecde9aca2bcc90e1fb0c7a9500bbf749f5788b70f82d4c5a5c2357f8b4

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

# 2026-08-23:HTML 从 Python 字符串里搬进 templates/(ADR-067),这里要跟着
# 一起拷进去——只拷 app.py 的话镜像跑起来会 TemplateNotFound。
COPY src/app.py .
# ADR-084 的 SQL Lab 深链。app.py `import sqllab`,漏拷这一行镜像会直接
# 起不来(ModuleNotFoundError),不是"少个功能"那么轻。
COPY src/sqllab.py .
# 登录身份/组的解析。权威源是 shared/flask_identity.py,这里是它的副本
# (scripts/check-duplicated-sources.py 在 CI 里保证不漂移)。**漏拷这一行
# 镜像会直接起不来**(ModuleNotFoundError),不是"少个功能"那么轻。
COPY src/identity.py .
COPY src/templates/ ./templates/
# 工具图标(Simple Icons,CC0)。**必须一起 COPY 进来** —— 只加文件不改
# Dockerfile 的话,本地跑得好好的、镜像里那一栏全是空白,而且不报错。
COPY src/logos/ ./logos/

EXPOSE 8080
CMD ["python3", "app.py"]
