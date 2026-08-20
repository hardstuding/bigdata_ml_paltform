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
#
# 2026-08-19 真实踩坑:当时手动登 cloud-full 云主机 build,直连官方
# PyPI 装这个 317.9MB 的 pyspark 包,实测带宽被限到约 39KB/s(用
# /proc/net/dev 前后两次采样算的真实吞吐,不是猜的),按这个速度要 2 个
# 多小时——和这个项目其它地方已经踩过的"直连 PyPI/apt 官方源带宽瓶颈"
# 是同一类问题(比如 scripts/12-sync-iam.py 的 fetch-kubectl 那次)。
# 换阿里云 PyPI 镜像后实测吞吐涨到约 1.6MB/s,40 倍左右,几分钟就装完。
#
# 2026-08-20:接进 GitHub Actions 自动构建(见 .github/workflows/
# build-images.yml),构建方从"cloud-full 云主机"变成境外 runner,不再
# 需要阿里云镜像源,直连默认 PyPI 反而更快更稳(和这批一起接进 CI 的
# 其它几个 Dockerfile 同一个原因)。基础镜像
# `quay.io/feastdev/feature-server:0.65.0` 确认原生支持 linux/amd64 +
# linux/arm64(quay.io manifest list 里两个 platform 都有,不需要 QEMU
# 模拟就能各自拿到原生镜像),多架构构建不会有问题。
FROM quay.io/feastdev/feature-server:0.65.0

USER root
RUN microdnf install -y java-17-openjdk-headless && microdnf clean all
RUN /opt/app-root/bin/pip install --no-cache-dir pyspark==3.5.9

USER 1001
