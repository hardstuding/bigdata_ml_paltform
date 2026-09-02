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
# 一套。
#
# 2026-08-28(ADR-076):Spark 3.5.9 → 4.1.3,整条线一起动,原因是它们本来
# 就是一个死结——iceberg-spark-runtime 之前锁 1.10.0,是因为 1.11.0 起改用
# Java 17 编译(class file version 61)而 Spark 3.5 镜像自带 Java 11,driver
# 启动直接 UnsupportedClassVersionError(ADR-036 逐版本二分定位过);而
# Iceberg 给 Spark 4.1 只发了 1.11.0 这一个版本。Spark 4 本身要求 Java 17,
# 所以升 Spark 恰好解开 Iceberg 的天花板,反过来也一样,只能一起升。
#
# 连带变的三处(都不是可选项):
#   - 镜像 tag 从 `-python3` 换成 `4.1.3-scala2.13-java17-python3-ubuntu`
#     ——Spark 4 官方只发 Scala 2.13,不再有 2.12。
#   - hadoop-aws 3.3.4 → 3.4.2:必须匹配 Spark 4.1.3 内置的 hadoop-client
#     (spark-parent POM 里 hadoop.version=3.4.2)。
#   - AWS SDK v1 的 aws-java-sdk-bundle:1.12.262 → v2 的
#     software.amazon.awssdk:bundle:2.29.52。Hadoop 3.4.0 起 S3A 迁到了
#     SDK v2(HADOOP-18073),hadoop-aws:3.4.2 的 POM 里依赖的就是它;
#     继续带 v1 的包会在初始化 S3AFileSystem 时 ClassNotFoundException。
#     版本取 Spark POM 的 aws.java.sdk.v2.version,和引擎同一套。
#
# Hive Metastore 仍然是 3.1.3,不跟着动 —— 那个版本锁定的原因和 Spark 无关
# (Iceberg 自带的 Hive Catalog 客户端只会发已被 Hive 4.x 删掉的 `get_table`),
# apps/hive-metastore/manifests/deployment.yaml 里有完整论证,包括
# "升到 Spark 4 是不是就不用锁 3.1.3" 这个问题当时查证过的否定结论。
# **为什么停在 4.1.3,而不是上游最新的 4.2.0(2026-09-02 核实过)**:
# 卡点不在 Spark,在 Iceberg —— `iceberg-spark-runtime` 的 artifact 名字里
# 带 Spark 大版本(见下面那行 `-4.1_2.13`),而 Maven 中央仓库上目前只有
# `4.0` 和 `4.1` 两个,**没有 `4.2`**。
#
# 升到 Spark 4.2 而 Iceberg 没有对应 runtime = Spark 读写 Iceberg 直接断,
# 那是这个平台的核心链路。**Spark 的版本号在这里是跟着 Iceberg 走的,
# 不是跟着 Spark 自己走的。**
#
# 下次评估时先查这个,再看 Spark:
#   curl -s https://repo1.maven.org/maven2/org/apache/iceberg/ \
#     | grep -oE 'iceberg-spark-runtime-4\.[0-9]_2\.13/'
FROM apache/spark:4.1.3-scala2.13-java17-python3-ubuntu

# 官方镜像默认以非 root 用户(spark)运行,$SPARK_HOME/jars 属主是 root,
# 构建期要写进去得先切 root,装完切回去 —— 不改变运行时的用户身份。
USER root

RUN set -eux; \
    cd "${SPARK_HOME}/jars"; \
    curl -fsSL -O https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-spark-runtime-4.1_2.13/1.11.0/iceberg-spark-runtime-4.1_2.13-1.11.0.jar; \
    curl -fsSL -O https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.4.2/hadoop-aws-3.4.2.jar; \
    curl -fsSL -O https://repo1.maven.org/maven2/software/amazon/awssdk/bundle/2.29.52/bundle-2.29.52.jar

USER spark
