# ADR-076:Spark 4.x 评估 —— 暂不升,但它能解开 Iceberg 卡在 1.10.0 的那个结

日期:2026-08-26
状态:**评估结论,暂不执行**

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
