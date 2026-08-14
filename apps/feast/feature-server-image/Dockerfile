# 官方 quay.io/feastdev/feature-server 镜像不带 JRE、也不带 pyspark。实测发现
# (见 docs/decisions/042-feast-feature-store.md)`feast serve` 启动时会
# 按 feature_store.yaml 的 offline_store.type 动态 import 对应实现模块,不管
# 这个进程是不是真的会用到离线存储——配了 offline_store.type: spark,
# 在线特征服务(feature-server)进程启动就会 `import pyspark`,进而需要
# `java` 在 PATH 里,两个都没有,直接崩(ModuleNotFoundError → 装完
# pyspark 后会变成 exec: "java": executable file not found)。这是 Feast
# 本身的架构限制,不是这次配置错——真实生产环境部署 Feast serving 时,
# 只要 offline_store 配了非 file 类型,都得这么处理,不是这个项目独有的坑。
#
# 只在官方镜像基础上加两样东西:pyspark(和 apps/spark-iceberg-demo 已验证
# 过的 Spark 3.5 系列对齐)+ JRE 17(RHEL 9 UBI 基础镜像自带的免费仓库,不
# 需要订阅)。不改任何 Feast 自身逻辑。
FROM quay.io/feastdev/feature-server:0.65.0

USER root
RUN microdnf install -y java-17-openjdk-headless && microdnf clean all
RUN /opt/app-root/bin/pip install --no-cache-dir pyspark==3.5.9

USER 1001
