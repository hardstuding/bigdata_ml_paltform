# 往 Kafka 灌 device event 数据的 CronJob 用的镜像——运行脚本本身在
# apps/kafka-producer/manifests/script-configmap.yaml 里(ConfigMap 挂载,
# 和 apps/spark-iceberg-demo/、apps/argo-workflows-training-image/ 同一个
# 模式:镜像只提供运行时环境,业务脚本走 ConfigMap,方便迭代不用重新
# build 镜像)。这个 Dockerfile 只负责把 Kafka 客户端库在构建期装好——
# 不在运行时 pip install(见 docs/BACKLOG.md 2.1)。
#
# 选 confluent-kafka(librdkafka 绑定)而不是纯 Python 实现的 kafka-python:
#   - confluent-kafka 在 PyPI 发布了预编译 manylinux wheel,构建期
#     `pip install` 不需要现场编译 C 扩展,构建更快更可靠。
#   - librdkafka 对 Kafka 4.x(KRaft,无 ZooKeeper)的 broker 端协议协商
#     支持是当前最活跃维护的 Kafka 客户端实现之一,kafka-python 这个项目
#     近几年提交频率明显下降,不选它。
# 2.15.0 是当前 PyPI 最新稳定版(查过 PyPI 而不是猜的)。
FROM python:3.12-slim

# `[avro]` 这个 extra 带的是 fastavro + Schema Registry 客户端所需的依赖。
# 2026-08-23 producer 从 JSON 改成 Avro + Schema Registry(ADR-068),
# 不加 extra 的话 `from confluent_kafka.schema_registry.avro import
# AvroSerializer` 直接 ImportError——base 包里有 SchemaRegistryClient,
# 但没有 Avro 序列化器需要的 fastavro。
RUN pip install --no-cache-dir "confluent-kafka[avro]==2.15.0"
