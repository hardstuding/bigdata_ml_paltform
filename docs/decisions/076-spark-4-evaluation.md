# ADR-076:Spark 3.5.9 → 4.1.3 + Iceberg 1.10.0 → 1.11.0(一起升)

日期:2026-08-26(评估) / 2026-08-28(执行)
状态:**已执行**

## 起因

`docs/BACKLOG.md` 里挂着"Spark 4.x 评估:仓库固定 Spark 3.5.9,官方已到
4.x"。zhenghe 2026-08-26 说这条我自己看着办。

## 先把依赖链查清楚(全部是实测,不是查文档)

| 环节 | 结果 |
|---|---|
| `apache/spark:4.1.2-python3` | ✅ 存在(还有 scala2.13-java17 / java21 各种组合) |
| `iceberg-spark-runtime-4.1_2.13` | ✅ 只有 **1.11.0**,**没有 1.10.0** |
| `iceberg-spark-runtime-4.0_2.13` | ✅ 1.10.0 和 1.11.0 都有 |
| `iceberg-flink-runtime-1.20:1.11.0` | ✅ 存在 —— 也就是说 Flink 那边能跟着升到同一个 Iceberg |
| spark-operator | ✅ v2.5.0(2026-03)起的发布说明就提到 Spark 4,我们用的 2.5.2 |

**一个方法上的插曲值得记**:查 `apache/spark` 有哪些 tag 时,Docker Hub 的
`/v2/.../tags/list` 返回的 290 条里**没有 `3.5.9-python3`**——而我们的
Dockerfile 正钉着它、CI 一直构建成功。直接用 `crane manifest` 查单个 tag,
确认它存在。**Docker Hub 的 tag 列表不全,而且没有分页标志**,拿它做"某个
tag 存不存在"的判断会误报。差一点就报了个"我们钉的镜像不存在"的假警报。

## 关键发现:Spark 4 能解开 Iceberg 1.10.0 这个结

`apps/spark-iceberg-image/Dockerfile` 里写得很清楚,Iceberg 钉在 1.10.0
不是随便选的([ADR-036](036-iceberg-version-pinning.md) 二分定位过):

> 1.11.0 起改用 Java 17 编译(class file version 61),而这个 Spark 镜像
> 自带的是 Java 11,用 1.11.0 driver 启动直接 UnsupportedClassVersionError

而 **Spark 4.x 本身就要求 Java 17+**,官方镜像有 `java17` / `java21` 变体。
也就是说:

```
现在:Spark 3.5.9(Java 11 / Scala 2.12)→ Iceberg 只能停在 1.10.0
升级:Spark 4.1.2(Java 17 / Scala 2.13)→ Iceberg 可以到 1.11.0
      而 Flink 1.20 那边 1.11.0 的 runtime 也有 → 全平台能统一在 1.11.0
```

**所以这件事的性质不是"追新",是"解开一个已知约束"。** 这个仓库有一条硬
规则:所有引擎读写同一份 Iceberg 表格式,版本必须统一。Java 11 这个天花板
把整个平台的 Iceberg 锁在了 1.10.0。

## 决策:暂不升,但把触发条件和路径定下来

不升的理由不是"风险高"这种空话,是**现在没有任何东西被 1.10.0 挡住**:

- Spark 3.5.9 仍在维护(2026 年还在发补丁版本);
- 批处理链路(`scripts/13`)和训练链路都跑得好好的;
- 没有任何需求依赖 Iceberg 1.11.0 的新特性。

**触发条件**(满足任意一条就该动):

1. 需要 Iceberg 1.11+ 的某个具体能力(比如新的表维护/分区演进特性);
2. Spark 3.5 线停止维护;
3. 有别的组件也要求 Java 17+,让"升 Java"这件事无论如何都要做。

**真要做的时候,它是一次联动升级,不是改一个版本号。** 涉及:

- `apps/spark-iceberg-image`:基础镜像换 `4.1.2-scala2.13-java17-*`,
  jar 从 `iceberg-spark-runtime-3.5_2.12:1.10.0` 换成 `-4.1_2.13:1.11.0`
- `apps/argo-workflows-training-image`、`scripts/feast_feature_repo/feature_store.yaml`:同步 Iceberg 版本
- `apps/flink-iceberg-image`:`iceberg-flink-runtime-1.20` 也升 1.11.0(不然
  两个引擎读写同一批表用不同版本,正是这个仓库明确要避免的)
- **Scala 2.12 → 2.13**:任何自带 Scala 依赖的地方都要跟着换
- **PySpark 4 的 API 变更**要过一遍 `scripts/spark_iceberg_demo.py` 和训练脚本
- **Hive Metastore 3.1.3 的兼容性**要实测:Spark 4 默认的 metastore client
  版本变了,大概率要显式配 `spark.sql.hive.metastore.version` / `jars`
  ——**这一条是我判断里风险最高的**,因为 Trino/Flink 和它共用同一个 HMS,
  验证不到位会伤到别的引擎

## 建议做的时候怎么做

**不要在现有镜像上原地改。** 新建一个并行的 `spark4-iceberg-image`,让
`scripts/13-run-spark-iceberg-demo.sh` 能用环境变量切镜像,两条链路对着跑
一遍同一张表——**Iceberg 的价值就在于同一张表多引擎读写,那正好是这次升级
唯一必须验证的东西**。验完再切换,而不是切换完再验。


---

## 2026-08-28:执行

上面写的触发条件("等到有需求真的被 Iceberg 1.10.0 挡住")没有等到,是
zhenghe 直接问的:既然 Spark 4 和 Iceberg 1.11.0 互为对方的解锁条件,
"那是不是一起升级就好了"。对——这两条本来就不是两个决定,是一个。拆开
升任何一边都不成立,继续拖着只是让 3.5 这条线上再多堆一年的东西。

### 实际改的版本(都在 Maven / Docker Hub 上核对过当天的可用版本)

| 项 | 从 | 到 | 为什么是这个值 |
|---|---|---|---|
| Spark 镜像 | `apache/spark:3.5.9-python3` | `apache/spark:4.1.3-scala2.13-java17-python3-ubuntu` | 4.1 线最新;Iceberg 只给 4.0/4.1 发 runtime,4.2.0 虽然 GA 了但没有对应 runtime |
| pyspark | 3.5.9 | 4.1.3 | 和引擎同版本 |
| iceberg-spark-runtime | `3.5_2.12:1.10.0` | `4.1_2.13:1.11.0` | Spark 4 官方只发 Scala 2.13 |
| iceberg-flink-runtime | `1.20:1.10.0` | `1.20:1.11.0` | 全平台同一个 Iceberg 版本;Flink 镜像本来就是 java17,没额外代价 |
| hadoop-aws | 3.3.4 | 3.4.2 | Spark 4.1.3 的 `spark-parent` POM 里 `hadoop.version=3.4.2` |
| AWS SDK | `com.amazonaws:aws-java-sdk-bundle:1.12.262` | `software.amazon.awssdk:bundle:2.29.52` | **这条最容易漏**:Hadoop 3.4.0 起 S3A 迁到 SDK v2(HADOOP-18073),`hadoop-aws:3.4.2` 的 POM 依赖的是 v2 bundle;继续带 v1 会在初始化 `S3AFileSystem` 时 ClassNotFoundException。版本取 Spark POM 的 `aws.java.sdk.v2.version` |

### 明确没有跟着动的

- **Hive Metastore 仍然是 3.1.3**。这个版本锁定和 Spark 无关:Iceberg 自带的
  Hive Catalog 客户端只会发 Hive 4.x 已经删掉的 `get_table`
  (HIVE-26537)。2026-08-15 专门查证过"升到 Spark 4 是不是就不用锁 3.1.3"
  ——结论是不能,SPARK-45265 修的是 Spark 自己的 HiveExternalCatalog,
  不是 Iceberg 那份独立的 Thrift 客户端(证据:apache/iceberg#13572)。
  完整论证在 `apps/hive-metastore/manifests/deployment.yaml` 顶部。
- **Flink 侧的 hadoop-aws / aws-sdk**。它匹配的是 flink 镜像自己的 hadoop,
  和 Spark 内置的那套无关,一起动只会多一个变量。

### 一个被认真考虑过、但没做的取舍

SDK v2 的 bundle 是 611 MB,v1 是 267 MB,镜像会胖 350 MB。换成 Iceberg
自己的 S3FileIO + `iceberg-aws-bundle`(60 MB)能省下来,但 `spark.eventLog.dir`
和 Flink 那两条链路都还是 `s3a://`,S3A 去不掉,换了等于两套 S3 客户端
并存。所以没换。


### 实机验证(2026-08-28 23:43,cloud-full)

`spark-iceberg-demo` 这个 SparkApplication 跑完 `COMPLETED`,driver 日志里:

```
INFO SparkContext: Running Spark version 4.1.3
读到 10 行
...
INFO SparkWrite: Committing overwrite by filter true with 1 new data files
      to table demo.orders_by_region_spark
| South|      280.00|
|  East|      280.74|
|  West|      241.00|
| North|      275.50|
SPARK_ICEBERG_DEMO_OK
```

这一条日志同时证了四件事,不是"pod 起来了"级别的验证:

1. Spark 真的是 4.1.3(不是镜像换了但跑的还是旧的)
2. 读得到 **Trino 建的** Iceberg 表 —— 说明 Iceberg 1.11.0 的 Hive Catalog
   客户端和**没有跟着升的 HMS 3.1.3** 仍然对得上,这是这次升级最大的风险点
3. 写得进去(`s3a://lakehouse/...` 提交了新数据文件)—— 说明
   hadoop-aws 3.4.2 + AWS SDK **v2** 这套换血是对的
4. 读得回来,数字对

### 一个没预料到的连带工作:镜像引用形式

云主机拉不动 GHCR 的大镜像(升级后单个 AWS SDK v2 层就 570MB,实测
`docker pull` 25 秒 0 字节),只能用 `scripts/38-ship-image-to-cloud.sh`
搬 tar。搬完最后一步才发现这条路径**不支持 digest 引用**:`crane pull`
一个 `repo@sha256:...` 之后 tar 里的 tag 是 crane 造的
`repo:i-was-a-digest`,而 `docker tag` 拒绝把 digest 作为目标。

所以 spark-iceberg / feast-feature-server / argo-workflows-training 三个
的引用从 digest 改成了 commit SHA tag(flink-iceberg 一直就是这么写的)。
commit SHA 当 tag 同样是不可变引用。`scripts/38` 也加了显式拦截,不要再
让人搬完 1.3GB 才在最后一步失败。
