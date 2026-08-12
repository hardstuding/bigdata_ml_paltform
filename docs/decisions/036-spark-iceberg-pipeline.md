# 036. Spark 读写 Iceberg 端到端验证

- 状态: 已采纳,验证中(2026-08-12)

## 背景

架构复盘时明确指出:这个平台"数据工程"这条主线(SeaTunnel/Kafka/
Airflow/Spark)从来没有真实跑通过,`docs/architecture.md` Phase 1 的退出
标准里写得很清楚——"建一张 Iceberg 表、写入,Trino 读出、Superset 出图"
已经验证过,但"Spark 读出还没做,留到 Spark Operator 真正跑作业时一起
验证"。这是当前架构里唯一一条"配置写了、但从没跑过真实作业"的核心链路,
优先级最高。

colima 内存从 9G 扩到 11G(用户主动提出的)之后有了余量,先启用了
Spark Operator(本身很轻,controller+webhook 加起来几百 Mi),这次是
提交一个真实作业验证读写链路。

## 决策

### 读 Trino 建的表,不是自己另建一张

`spark_iceberg_demo.py` 读的是 `scripts/08-create-demo-data.sh` 用 Trino
建的 `iceberg.demo.orders`,不是自己新建一张表自己读自己写——这是刻意的:
证明 Spark 和 Trino 走的是**同一个 Hive Metastore + 同一个 MinIO
warehouse**,两边看到的是同一份数据,这才是"湖仓"这个词的核心含义(存储
和计算分离,多个引擎共享同一份数据),不是简单地"Spark 也能连 Iceberg"。

### Iceberg/S3A 支持用 `spark.jars.packages` 现拉,不打进镜像

官方 `apache/spark:3.5.9-python3` 镜像,不自己 build。Iceberg 和 S3A 相关
的 jar 包通过 `spark.jars.packages` 在作业提交时从 Maven Central 现拉
(`org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.11.0`——查 Maven
Central 当前最新稳定版,不是拍的;`org.apache.hadoop:hadoop-aws:3.4.1` +
`software.amazon.awssdk:bundle:2.51.3` 版本和 `apps/hive-metastore/` 保持
一致)。这个仓库反复出现的取舍:不自己维护镜像构建流水线。

### `spark` 这个 ServiceAccount 是补的,chart 本身不建

`spark-operator` chart 只给它自己的 controller/webhook 建了
ServiceAccount,没有给"提交上来的作业本身"建一个——Spark on k8s 原生
调度模式下,driver pod 自己要有权限直接建 executor pod/service/
configmap(它自己就是个迷你调度器,不是 operator 替它调度),这是官方
spark-on-k8s 文档写明的标准模式,不是这个项目发明的权限模型。补了一个
最小权限的 `spark` ServiceAccount + Role(只给 pods/services/configmaps/
persistentvolumeclaims 这几类资源,只在 `spark-operator` namespace 内)。

### PySpark 脚本用 ConfigMap 挂载,不打进镜像

和 `apps/iam-sync/`、`apps/permission-request-app/` 是同一个模式——没有
容器镜像仓库能 build+push 自定义镜像,单个 Python 文件用 ConfigMap 挂载
进 driver pod,`mainApplicationFile: local:///opt/spark-demo/xxx.py` 指到
挂载路径。

### 不走 GitOps,一次性验证资源

和 `kserve-demo`/Superset demo dashboard 是同一类东西——这是验证链路用的
一次性资源,不是常驻的平台组件,`scripts/13-run-spark-iceberg-demo.sh`
直接 `kubectl apply`。

## 验证记录

(跑完 `scripts/13-run-spark-iceberg-demo.sh` 之后补充实际结果)

## 后果

- 这次只验证单个 driver + 1 个 executor 的最小规模,没有测试真实数据量级
  下的多 executor 并行、动态资源分配这些生产场景才会遇到的问题。
- 没有测试 SeaTunnel/Kafka/Airflow 这几个数据工程主线里的其他组件,只是
  最靠核心的"Spark 能不能读写 Iceberg"这一条链路——数据工程整条主线要
  完全跑通,还需要接上批流采集(SeaTunnel/Kafka)和调度编排(Airflow)。
- `spark.jars.packages` 这种运行时现拉依赖的方式,每次提交作业都要重新
  下载(没有做本地 Maven 仓库缓存),在这台机器的网络条件下可能会比较慢
  ——如果以后作业提交频繁到这个变成瓶颈,需要评估要不要建一个本地 Maven
  代理仓库或者把这些 jar 打进一个自定义镜像(会重新触发"要不要自己维护
  镜像构建"这个已经讨论过多次的取舍)。
