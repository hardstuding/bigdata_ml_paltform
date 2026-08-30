# 推理留痕的接收端(ADR-085)。收 KServe payload logger 的 CloudEvent,
# 丢进 Kafka。
#
# 依赖在**构建时**装好,不在运行时 pip install —— 那是这个项目明确记过的
# 反模式(每次 pod 重启重装一遍,遇到这片网络的 PyPI 限速直接卡死探针预算,
# 2026-08-16 真实故障 SIGKILL 5 分 44 秒整)。
#
# **不用阿里云镜像源**:构建方是 GitHub Actions(境外 runner),直连默认
# PyPI 更快更稳 —— 和 apps/iam-sync/Dockerfile 同一个判断。
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/app.py .

EXPOSE 8080
# 用 flask 自带的 server:这个组件的流量是"每次推理两条 POST",不是 Web
# 应用级别的并发。上生产、量真的上来了再换 gunicorn —— 现在换只是提前
# 增加一个没人验证过的部件。
CMD ["python3", "app.py"]
