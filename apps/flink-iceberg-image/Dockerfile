# 给 Flink Kubernetes Operator 提交的流式作业用的镜像——官方 flink 镜像不带
# PyFlink 运行时、也不带 Iceberg/Kafka/S3A 连接器,这几样都要么在构建期装
# 好,要么运行时联网现拉。这个仓库已经在 apps/spark-iceberg-image/ 踩过
# "运行时从 Maven Central 拉 jar 在 cloud-full 上会卡死"这个坑,这里直接照抄
# 那次的结论:构建期装依赖,运行时不联网。见 docs/BACKLOG.md 2.1。
#
# 版本选择和兼容性核对(不是拍脑袋,逐项查过 Maven Central / PyPI / 官方
# 下载页,细节见 docs/decisions/062-flink-streaming-pipeline.md):
#
#   - Flink 1.20.5(官方 flink:1.20.5-java17,base 是 eclipse-temurin:
#     17-jre-jammy,查过 apache/flink-docker 仓库的 Dockerfile 源码确认,
#     不是猜的)——1.20 是当前 Flink Kubernetes Operator 1.15.0 生态里
#     iceberg-flink-runtime 和 flink-connector-kafka 都还在正常发新版本
#     的最新 1.x 分支(Flink 2.x 已发布到 2.3.0,但那条线要求的连接器/
#     生态还在快速变动中,这个仓库其它组件——Kafka 4.3.0、Iceberg
#     1.10.0——都还是稳定线,不跟进 Flink 2.x 这条更激进的线)。
#   - apache-flink==1.20.5(PyPI,和 Flink 核心版本完全对齐,PyFlink 的
#     Python API 版本必须和集群跑的 Flink 版本一致,这是 PyFlink 的硬性
#     要求,不是建议)。
#   - iceberg-flink-runtime-1.20:1.10.0——Iceberg 官方给 Flink 1.20 单独发
#     了一份 runtime jar(Maven Central 确认存在),版本号故意和
#     apps/spark-iceberg-image、apps/argo-workflows-training-image、
#     scripts/feast_feature_repo/feature_store.yaml 用的 iceberg-spark-
#     runtime 保持同一个 1.10.0,不是另挑一套——这个仓库现在所有引擎读写
#     的是同一份 Iceberg 表格式,版本不一致会增加"这张表某个引擎读不了"
#     的风险面,没有理由不统一。
#   - flink-connector-kafka(SQL 版,fat jar 自带 kafka-clients,不用额外
#     管理 kafka-clients 版本):3.4.0-1.20,Maven Central 上 Flink 1.20
#     线最新的一版。Kafka broker 是 4.3.0(见 apps/kafka/manifests/
#     kafka-cluster.yaml)——Kafka 的 client-broker 线协议是显式协商版本
#     的(broker 会告诉 client 自己支持的 API 版本范围),新版本 client
#     连旧版本或反过来都有较宽的兼容窗口,不要求逐位对齐;这个连接器内置
#     的 kafka-clients 版本本身也比 4.3.0 新不了太多,走的是标准 Produce/
#     Fetch API,没有用到任何 4.x 独有的新协议特性,风险很低。
#   - Hadoop S3A 栈(hadoop-client-api/hadoop-client-runtime/hadoop-aws/
#     aws-java-sdk-bundle,全部 3.3.4/1.12.262)——**和
#     apps/spark-iceberg-image/Dockerfile 完全一致的版本**,不是巧合。
#     官方 Flink 镜像不像 apache/spark 镜像那样自带 Hadoop client(Flink
#     从 1.11 起就是 hadoop-free 的),这里必须自己把整套 Hadoop client
#     补齐,版本选择照抄 Spark 那条(已经在这个仓库真实验证过能连通同一个
#     Hive Metastore + MinIO),不是另配一套版本组合去猜兼容性。
#
# 三个 jar 放进 $FLINK_HOME/lib/——Flink 会自动加载这个目录下所有 jar,不用
# 像 Spark 的 spark.jars 那样额外声明。
FROM flink:1.20.5-java17

# 官方镜像默认没有显式 USER(entrypoint 用 gosu 从 root 降权到 flink 用户,
# 见 apache/flink-docker 的 docker-entrypoint.sh),构建期本来就是 root,
# 不需要额外切换。

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3 python3-pip python3-dev; \
    rm -rf /var/lib/apt/lists/*; \
    pip3 install --no-cache-dir apache-flink==1.20.5; \
    ln -sf /usr/bin/python3 /usr/bin/python

RUN set -eux; \
    cd "${FLINK_HOME}/lib"; \
    curl -fsSL -O https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-flink-runtime-1.20/1.10.0/iceberg-flink-runtime-1.20-1.10.0.jar; \
    curl -fsSL -O https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/3.4.0-1.20/flink-sql-connector-kafka-3.4.0-1.20.jar; \
    curl -fsSL -O https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-client-api/3.3.4/hadoop-client-api-3.3.4.jar; \
    curl -fsSL -O https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-client-runtime/3.3.4/hadoop-client-runtime-3.3.4.jar; \
    curl -fsSL -O https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.3.4/hadoop-aws-3.3.4.jar; \
    curl -fsSL -O https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.262/aws-java-sdk-bundle-1.12.262.jar; \
    chown -R flink:flink "${FLINK_HOME}/lib"
