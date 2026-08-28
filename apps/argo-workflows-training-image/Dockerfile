# 给 Argo Workflows 编排训练用的镜像——scripts/train_demo_model.py 需要
# mlflow-skinny/scikit-learn/boto3,官方 python 基础镜像不带,不在容器
# 启动时现装(这个项目已经踩过多次"运行时 pip install"的坑,见
# docs/BACKLOG.md P2.1),在镜像构建时一次装好。
#
# 2026-08-19 真实踩坑(和 apps/feast/feature-server-image/Dockerfile 那次
# 一样):当时是手动登 cloud-full 云主机 build,直连官方 PyPI 装包在
# 这片网络里会被限速到几十 KB/s,换成阿里云 PyPI 镜像后能到 1MB/s 以上
# 量级,构建时间从"数十分钟到数小时"降到几分钟。
#
# 2026-08-20:接进 GitHub Actions 自动构建(见 .github/workflows/
# build-images.yml),不再需要手动登云主机——构建方是境外 runner,不用
# 阿里云镜像源,直连默认 PyPI 反而更快(和 apps/iam-sync/Dockerfile、
# apps/superset-image/Dockerfile 同一个原因)。
FROM python:3.11-slim

# mlflow-skinny 版本和已部署的 MLflow server(ghcr.io/mlflow/mlflow:
# v3.14.0-full,见 apps/definitions/mlflow.yaml)对齐,不是随便挑的——
# 四个版本号都在 PyPI 上核实过存在(`pip index versions`,不是猜的)。
#
# 2026-08-19 真实故障(第一次跑这个镜像才发现,本机手动跑那个脚本时
# 从没暴露过):mlflow.sklearn.log_model() 内部会 import pandas 做 schema
# 推断,但 mlflow-skinny(特意排除掉 pandas 这类重依赖的精简版客户端)
# 不会自动带上它,报 ModuleNotFoundError。本机能跑通是因为本机是
# anaconda 环境,pandas 作为基础环境的一部分早就装着,把这个隐藏依赖
# 掩盖掉了——这类"本机凑巧能跑、干净容器里跑不起来"的坑,只有真正
# 在干净环境里跑一次才能发现,这也是这次要做"Argo Workflows 编排训练"
# 的价值之一,不只是把手动步骤自动化。
# 2026-08-20 新增(BACKLOG P1.7,"notebook → Feast 特征 → Argo Workflows
# 训练 → MLflow 记录"这条完整链路的训练那一步):补 feast[spark] + JRE,
# 让训练脚本能从 Feast 的离线存储(Spark 读 Iceberg,和
# apps/feast/feature-server-image/ 走的是同一个 offline_store 配置)取
# point-in-time 正确的历史特征,不是像 train_demo_model.py 那样用
# sklearn.datasets.make_classification 生成的合成数据。
#
# feast==0.65.0 和已部署的 Feast(quay.io/feastdev/feature-server:0.65.0,
# 见 apps/feast/manifests/feature-server.yaml)版本对齐——client/server
# 版本不匹配可能导致 feature_store.yaml/registry 格式不兼容,不是随便
# 挑的。pyspark==4.1.3 和 apps/feast/feature-server-image/Dockerfile
# 用的是同一个版本(那边已经验证过这个版本能跑 Feast 的 Spark offline
# store),不单独选一个新版本增加不必要的变量。
#
# 依赖这里分别装 `feast==0.65.0`(不带 `[spark]` extras)+ `pyspark`,
# 不用 `feast[spark]` 的 extras 语法。历史原因是 extras 要求
# `pyspark>=4.0.0`(不满足时 pip 直接 ResolutionImpossible),而当时整条
# Spark/Iceberg 链路锁在 3.5 系列。2026-08-28 已经整体升到 Spark 4.1.3
# (ADR-076),这个冲突本身消失了,但仍然保持分开装:版本由这个仓库统一
# 钉死(和 apps/spark-iceberg-image、apps/feast/feature-server-image、
# scripts/feast_feature_repo/feature_store.yaml 同一个版本),不想让
# extras 的版本范围在某次 feast 升级时把 pyspark 悄悄带走。
#
# 注意 Java:pyspark 4 要求 Java 17+。这个镜像装的是 `default-jre-headless`
# (见下面),在 python:3.11-slim 的 Debian bookworm 上就是 OpenJDK 17,
# 满足;换基础镜像时要重新确认这一点,不能想当然。
# 这是这次没有把握、需要实测验证的一处,如果真的踩到运行时 API 不兼容,
# 再回来处理(可能的方向:等 feast 出一个对 3.5 更宽松的版本,或者接受
# 升到 Spark 4.x 这个更大的变化)。
#
# `default-jre-headless`(虚拟包,指向这个 Debian 发行版当前打包的
# OpenJDK 版本)而不是写死 `openjdk-17-jre-headless`——这个基础镜像
# (`python:3.11-slim`,没有像 apps/iam-sync/Dockerfile 那样锁定具体
# digest)用的 Debian 发行版会随官方镜像更新滚动变化,某个具体大版本号
# 的 JDK 包不一定在每个发行版分支里都打包了(Debian testing/unstable
# 分支尤其容易出现"某个具体版本号暂时没有对应二进制包"),pyspark 只需要
# 一个能跑的 JVM,不需要精确到某个大版本号,用虚拟包更稳。
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends default-jre-headless curl \
    && rm -rf /var/lib/apt/lists/*
# 和上面的 mlflow-skinny/scikit-learn/pandas/boto3 合并成一次 pip
# install(而不是分两次 RUN):pip 的依赖解析器在同一次调用里能同时看到
# 所有包的版本约束,一次性算出一套互相兼容的版本组合,比分两次调用更不
# 容易踩到"后装的包和先装的包版本冲突,但因为是两条独立命令,报错信息
# 不够直观"这类坑。
RUN pip install --no-cache-dir \
    mlflow-skinny==3.14.0 \
    scikit-learn==1.5.2 \
    pandas==2.2.3 \
    boto3==1.35.36 \
    feast==0.65.0 \
    pyspark==4.1.3 \
    redis==6.4.0
# ↑ redis 客户端(2026-08-20 真实踩坑,cloud-full 上跑第一次才暴露):
# feature_store.yaml 里 online_store.type 配的是 redis(和 Feast Serving/
# feast_materialize 那条链路共用同一份配置),Feast 在 FeatureStore()
# 初始化阶段就会校验 online_store 这段配置、eagerly import 对应模块
# (feast.infra.online_stores.redis),哪怕这个训练脚本压根不碰在线
# 存储、只用离线的 Spark/Iceberg 那条路径——报
# FeastModuleImportError,不是"没装 feast[spark] extras"那类版本冲突,
# 是缺了一个完全独立的运行时依赖。补上就行,不需要改脚本逻辑。

# 2026-08-20 真实踩坑:train_from_feast_features.py 需要的
# Iceberg/hadoop-aws/aws-sdk 这三个 jar,原本想靠 Spark 自己的
# `spark.jars.packages`(feature_store.yaml 里配的)运行时从 Maven
# Central 解析下载——cloud-full 云主机上直连 repo1.maven.org 会真的
# 卡死不动(aws-java-sdk-bundle 这个几百 MB 的大包,下载进度停在 0
# 字节超过 8 分钟没有任何变化,加了 spark.jars.repositories 指向阿里云
# 镜像也没用,Ivy 的默认解析器链路优先级仍然先打 Maven Central,不会
# 因为多了一个候选源就绕开卡住的那个)——这是这个仓库反复踩过的"直连
# 境外源卡死"同一类问题,运行时下载这条路径本身就不可靠,应该在构建期
# 解决,不是加更多运行时重试/镜像候选源。GitHub Actions 的 runner 在
# 境外,直连 Maven Central 没有这个问题,构建期下载一次,打进镜像里,
# 训练 pod 运行时完全不需要联网下载——版本号和
# apps/spark-iceberg-demo/manifests/sparkapplication.yaml、
# scripts/feast_feature_repo/feature_store.yaml 里的
# spark.jars.packages 保持一致,不是另挑一套。
RUN mkdir -p /opt/spark-jars \
    && curl -fsSL -o /opt/spark-jars/iceberg-spark-runtime-4.1_2.13-1.11.0.jar \
        https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-spark-runtime-4.1_2.13/1.11.0/iceberg-spark-runtime-4.1_2.13-1.11.0.jar \
    && curl -fsSL -o /opt/spark-jars/hadoop-aws-3.4.2.jar \
        https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.4.2/hadoop-aws-3.4.2.jar \
    && curl -fsSL -o /opt/spark-jars/bundle-2.29.52.jar \
        https://repo1.maven.org/maven2/software/amazon/awssdk/bundle/2.29.52/bundle-2.29.52.jar

# train_demo_model.py 不打进镜像——通过 ConfigMap 挂载(见
# apps/argo-workflows-training-image/manifests/script-configmap.yaml),
# 和 apps/spark-iceberg-demo 是同一个模式:改脚本逻辑不用重新构建镜像,
# 只有改 Python 依赖(这个 Dockerfile)才需要重新构建。
