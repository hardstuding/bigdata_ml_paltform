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
COPY src/templates/ ./templates/

EXPOSE 8080
CMD ["python3", "app.py"]
