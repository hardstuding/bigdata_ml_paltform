# Hive Metastore + 三个必需的 jar,构建期打进去。
#
# **为什么要自建这个镜像**(2026-08-27 实测逼出来的):
#
# 原来的做法是两个 initContainer 每次 Pod 启动都 `curl` 去 repo1.maven.org
# 下 postgres 驱动、hadoop-aws、aws-java-sdk-bundle,落到 emptyDir,再让主
# 容器 subPath 挂进 /opt/hive/lib/。代价实测过:
#
#   - 云主机到 repo1.maven.org **283 KB/s**,而 aws-java-sdk-bundle 约
#     100MB ——**每次 Pod 启动要等约 6 分钟**才轮到主容器。2026-08-27 那一轮
#     开机实测等了 7 分钟以上,期间 Trino 查任何表都报
#     `Failed connecting to Hive metastore`。
#   - 更要紧的是**它把整条查询链路挂在了境外网络上**。这一周反复撞到境外
#     不可用(2026-08-26 云主机同时失去了到 Docker Hub 的所有通路)。真赶上
#     那种时候 hive-metastore 起不来,而它一挂,Trino 查任何表都报错——
#     它是 Trino/Spark/Flink 共用的元数据服务,是单点。
#
# 这正是 `docs/BACKLOG.md` 2.1(引入镜像构建流程,停止运行时下载)已经解决过
# 一遍的那类问题,只是那次覆盖的是 `pip install`,漏掉了这个走 `curl` 的变种。
#
# 版本全部照抄原来 initContainer 里写死的那几个,**一个字节都没改**——
# 这次改的是"什么时候下载",不是"下载什么"。要升级版本是另一件事。
#
# **实测效果(2026-08-28,cloud-full)**:Pod 创建 → Ready **55 秒**,
# 改之前是 6~7 分钟(实测过一次等了 7 分钟以上)。而且 initContainer 从
# 3 个减到 1 个(只剩 wait-for-postgres)。换完之后跑 goldenpath-query
# 探针确认链路正常——**打进镜像的 jar 是好的,不只是「Pod 起来了」**。

# ---------- 第一阶段:下 jar ----------
#
# **用多阶段而不是在 hive 镜像里直接 curl**:第一版就是后者,CI 构建失败。
# 线索其实一开始就在——原来那两个 initContainer 用的是 `curlimages/curl`
# 镜像,说明**当时的人也知道 hive 镜像里没有 curl**,只是那个信息藏在
# "用哪个 initContainer 镜像"里,没有写成一句话。
#
# 多阶段还有个好处:下载工具不会进最终镜像,产物只有那三个 jar。
FROM curlimages/curl:8.11.0 AS jars

WORKDIR /jars
RUN set -eux; \
    curl -fsSL -o postgres.jar \
      https://repo1.maven.org/maven2/org/postgresql/postgresql/42.7.13/postgresql-42.7.13.jar; \
    curl -fsSL -o hadoop-aws.jar \
      https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.1.0/hadoop-aws-3.1.0.jar; \
    curl -fsSL -o awssdk-bundle.jar \
      https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.11.271/aws-java-sdk-bundle-1.11.271.jar; \
    # 断言三个都真的下下来了而且不是空文件。没有这一步的话,某次网络抖动
    # 会构建出一个"看起来成功、缺 jar"的镜像,而症状要到运行时才以
    # ClassNotFoundException 的形式冒出来——那是最难查的一类。
    # 用 `wc -c` 而不是 `stat -c%s`:这一层是 busybox,stat 的参数不一样。
    for f in postgres.jar hadoop-aws.jar awssdk-bundle.jar; do \
      test -s "$f" || { echo "!! $f 没下下来或者是空的"; exit 1; }; \
      echo "$f $(wc -c < "$f") 字节"; \
    done

# ---------- 第二阶段:装进 Hive ----------
FROM apache/hive:3.1.3

# 官方镜像默认 USER=hive(`crane config apache/hive:3.1.3` 确认过),
# 而 /opt/hive/lib 属主是 root,所以 COPY 要以 root 身份做。
USER root
COPY --from=jars --chown=hive:hive /jars/*.jar /opt/hive/lib/
USER hive
