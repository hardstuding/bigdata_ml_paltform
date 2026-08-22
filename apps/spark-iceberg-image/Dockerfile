# 给 Spark Operator 提交的 Iceberg 作业用的镜像 —— 官方 apache/spark 镜像
# 不带 Iceberg / hadoop-aws / aws-sdk 这三个 jar,原本靠 SparkApplication 里的
# `spark.jars.packages` 在**运行时**让 Spark 去 Maven Central 现拉。
#
# **2026-08-21:这条运行时拉取的路径在 cloud-full 上是不可靠的,必须干掉。**
# 实测(2026-08-20 在 Argo 训练那条链路上,同样这三个 jar、同一台节点):
# 直连 repo1.maven.org 下载 aws-java-sdk-bundle(几百 MB)时进度停在 0 字节
# 超过 8 分钟不动,不是慢是卡死;加 `spark.jars.repositories` 指向阿里云
# 镜像也没用——Ivy 的默认解析器链路仍然先打 Maven Central,不会因为多了
# 一个候选源就绕开卡住的那个。
#
# 解法和这个仓库其它镜像一致(见 docs/BACKLOG.md 2.1 那一整条:全仓库的
# 自定义镜像都已经改成"构建期装依赖,运行时不联网"):在 GitHub Actions
# 的境外 runner 上构建期把 jar 下好打进镜像。放进 `$SPARK_HOME/jars/` 而
# 不是自定义目录 —— 这个位置 driver 和 executor 都会自动加载,连
# `spark.jars` 都不用配,比指定路径更省事也更不容易配漏一边。
#
# 三个 jar 的版本和 scripts/feast_feature_repo/feature_store.yaml、
# apps/argo-workflows-training-image/Dockerfile 里用的完全一致,不是另挑
# 一套 —— iceberg-spark-runtime 选 1.10.0 是有原因的:1.11.0 起改用
# Java 17 编译(class file version 61),而这个 Spark 镜像自带的是 Java 11,
# 用 1.11.0 driver 启动直接 UnsupportedClassVersionError(ADR-036 里逐个
# 版本二分定位过,不是随便选的)。
FROM apache/spark:3.5.9-python3

# 官方镜像默认以非 root 用户(spark)运行,$SPARK_HOME/jars 属主是 root,
# 构建期要写进去得先切 root,装完切回去 —— 不改变运行时的用户身份。
USER root

RUN set -eux; \
    cd "${SPARK_HOME}/jars"; \
    curl -fsSL -O https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-spark-runtime-3.5_2.12/1.10.0/iceberg-spark-runtime-3.5_2.12-1.10.0.jar; \
    curl -fsSL -O https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.3.4/hadoop-aws-3.3.4.jar; \
    curl -fsSL -O https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.262/aws-java-sdk-bundle-1.12.262.jar

USER spark
