# 036. Spark 读写 Iceberg 端到端验证

- 状态: 已采纳,已验证(2026-08-12)

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

真实跑通比预想中费劲得多,一路上暴露了 5 个独立的真实 bug,不是一次就
成功的——记录下来是因为每一个都有可能在别的场景下复现,不是这个 demo 特有的:

### bug 1:spark-operator controller 默认只认 default namespace

见 `apps/definitions/spark-operator.yaml` 里的说明,提交到 `spark-operator`
namespace 的作业被静默忽略。加 `spark.jobNamespaces` 解决。

### bug 2:Ivy 缓存目录不可写

`spark.jars.packages` 触发的依赖解析是 spark-operator controller 自己在
它的 pod 里跑的,controller 跑在没有可写 `$HOME` 的用户下。加
`spark.jars.ivy: /tmp/.ivy2` 解决。

### bug 3:iceberg-spark-runtime 1.11.0 要 Java 17,镜像自带 Java 11

`apache/spark:3.5.9-python3` 镜像自带 Temurin JDK 11,Maven Central 上最新
的 iceberg-spark-runtime 1.11.0 是 Java 17 编译的,直接
`UnsupportedClassVersionError`。逐版本下载实测二分定位,降级到 1.10.0
(还是 Java 11 编译)解决。

### bug 4:Hive Metastore 4.x 删掉了 Iceberg 客户端依赖的老 Thrift 方法(最大的一个)

Spark 端报 `org.apache.thrift.TApplicationException: Invalid method name:
'get_table'`。查证是 Hive 自己的 HIVE-26537(fixVersions 4.0.1/4.1.0,当时
部署的 4.2.0 自然也在内)把老式 `get_table` 方法删了,换成
`get_table_req`;Iceberg 的 Hive Catalog 客户端绑死用 Spark 内置的 Hive
2.3.x 客户端,只会发老方法——这是 Iceberg 社区自己确认的已知限制
(github.com/apache/iceberg/issues/12878:"We don't support hive 4
metastore yet"),不是这个项目配错了什么。把 Hive Metastore 降级到 3.1.3
(Spark 官方文档列出支持的最高 Hive 2.x 系列版本)解决,连带:
- Postgres 里 metastore 库的 schema 要整库重置(HMS 有版本门禁,4.x
  初始化过的 schema 不能被 3.x 直接复用),当时库里只有 demo 数据,可以
  接受。
- S3A 客户端 jar(hadoop-aws)版本要跟着镜像自带的 hadoop-common 版本走
  (3.1.3 镜像是 hadoop-common 3.1.0),不能照抄别处的版本,否则
  `NoClassDefFoundError: IOStatisticsSource`。
- 官方镜像的 entrypoint.sh 不会自己探测 schema 是否已初始化,pod 一重启
  就对着已建好的库重跑一遍 `initSchema` 崩溃循环——加了一层用
  `schematool -info` 探测的幂等检查。
- Trino 不受影响(它自己实现了一套独立的、更宽容的 Thrift 客户端)。

### bug 5:Spark 这边的 hadoop-aws 版本不能照抄 Trino 的

过了 bug 4 那关之后又报
`ClassNotFoundException: org.apache.hadoop.fs.BulkDelete`——`hadoop-aws`
必须匹配它所在环境自己的 `hadoop-common` 版本,Trino 用的 3.4.1 和
apache/spark:3.5.9-python3 镜像自带的 hadoop-client(3.3.4,实测确认)对不
上,这个类是 Hadoop 3.4.0 才加的。换成 `hadoop-aws:3.3.4` +
对应的 AWS SDK v1(`aws-java-sdk-bundle:1.12.262`,不是 Trino 那边用的
SDK v2)解决。

### 排查过程中顺带发现并修的另外两个问题

- **不是 bug,是这次意外验证清楚的一个误解**:之前以为 spark-operator 那次
  "GitOps 同步不生效"是 ArgoCD 的 bug,这次再踩了一遍才发现根本原因很
  简单——改动只 commit 到本地,忘了 `git push`,远程仓库里其实没有这个
  commit,ArgoCD 显示"已同步到最新 commit"是真的同步对了,只是那个
  commit 本来就没有这处改动。
- ArgoCD `application-controller` 之前补的 `resources.limits.memory: 1Gi`
  上限定低了,真实批量操作(un-park Trino 触发 apps-root 重新渲染)时被
  OOMKilled 反复重启,导致所有 Application 的 sync 操作卡住——调到 2Gi。

### 最终验证结果(真实跑通)

`scripts/08-create-demo-data.sh`(通过 Trino REST API 直接执行,当时
Superset 处于 park 状态)重新建了 `iceberg.demo.orders`(10 行示例数据)。
`spark-iceberg-demo` SparkApplication 提交后 COMPLETED,driver 日志确认:

```
读到 10 行
+--------+-------------+------+-------+------+----------+
|order_id|customer_name|region|product|amount|order_date|
+--------+-------------+------+-------+------+----------+
|       1|        Alice|  East| Widget|120.50|2026-07-01|
...(10 行,和 Trino 写入的数据完全一致)

=== 按 region 聚合,写一张新表,验证 Spark 也能写 ===
已写入 iceberg.demo.orders_by_region_spark
+------+------------+
|region|total_amount|
+------+------------+
| South|      280.00|
|  East|      280.74|
|  West|      241.00|
| North|      275.50|
+------+------------+
SPARK_ICEBERG_DEMO_OK
```

driver 日志里也确认了真实的 Iceberg commit(`HiveTableOperations:
Committed to table iceberg.demo.orders_by_region_spark`,写到
`s3a://lakehouse/opt/hive/data/warehouse/demo.db/orders_by_region_spark/`),
不是内存里算完就完事——Spark 读了 Trino 建的表、写了一张新表回同一个
Hive Metastore + 同一个 MinIO warehouse,"湖仓"这条核心链路第一次真实跑通。

跑完之后 Trino 按惯例重新收回 `pending-definitions`(释放内存,executor
一开始因为 CPU 请求不够一直 Pending,收回 Trino 才腾出空间,后来干脆把
demo 作业本身的 driver/executor CPU request 也降到 500m,减少对这台单机
CPU 的依赖)。

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
