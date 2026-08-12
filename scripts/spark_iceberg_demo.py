"""
验证 Spark 能读写 Iceberg 表——见 docs/decisions/036-spark-iceberg-pipeline.md。

之前这条链路(Spark 读写 Iceberg)从来没有真实验证过,`docs/architecture.md`
Phase 1 的退出标准里明确写着"留到 Spark Operator 真正跑作业时一起验证"。
读的是 `scripts/08-create-demo-data.sh` 用 Trino 建的 `iceberg.demo.orders`
表——不是随便建一张新表自己读自己写,是刻意读 **Trino 建的表**,证明
Spark 和 Trino 用的是同一个 Hive Metastore + 同一个 MinIO warehouse,
两边看到的是同一份数据,不是各自一套,这才是"湖仓"这个词真正的含义。

用 scripts/13-run-spark-iceberg-demo.sh 提交,不要直接跑这个文件。
"""
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("spark-iceberg-demo").getOrCreate()

print("=== 读 Trino 建的表 iceberg.demo.orders ===")
df = spark.table("iceberg.demo.orders")
row_count = df.count()
print(f"读到 {row_count} 行")
df.show()

if row_count == 0:
    raise RuntimeError("iceberg.demo.orders 是空的——先跑 scripts/08-create-demo-data.sh 建好 demo 数据")

print("=== 按 region 聚合,写一张新表,验证 Spark 也能写 ===")
agg = df.groupBy("region").sum("amount").withColumnRenamed("sum(amount)", "total_amount")
agg.writeTo("iceberg.demo.orders_by_region_spark").createOrReplace()
print("已写入 iceberg.demo.orders_by_region_spark")

print("=== 读回刚写的表,确认真的落盘了(不是只在内存里算完就丢) ===")
verify = spark.table("iceberg.demo.orders_by_region_spark")
verify.show()

spark.stop()
print("SPARK_ICEBERG_DEMO_OK")
