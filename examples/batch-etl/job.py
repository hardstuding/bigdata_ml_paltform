"""批处理模板:从 Iceberg 读 → 算 → 写回 Iceberg。

这是这个平台上最常见的一类作业。照着改的时候只有三处要动:
源表、目标表、中间那段 SQL。

**为什么用 CREATE TABLE ... AS 而不是先建表再 INSERT**:一步完成,
表结构跟着查询走,改了聚合逻辑不用记得同步改建表语句——那是最容易
漏的一步。代价是每次全量重建,数据量大了要改成增量(见文末)。
"""

from platform_sdk import query

SOURCE = "iceberg.demo.orders"
TARGET = "iceberg.demo.orders_by_region_job"

# 幂等:重跑不会因为表已存在而失败。作业被重试是常态,不是异常。
query(f"DROP TABLE IF EXISTS {TARGET}")
query(f"""
    CREATE TABLE {TARGET} AS
    SELECT region,
           count(*)      AS order_count,
           sum(amount)   AS total_amount,
           avg(amount)   AS avg_amount
    FROM {SOURCE}
    GROUP BY region
""")

# **写完一定要回查一次。** 这个平台反复吃过"作业显示成功但数据没落盘"
# 的亏(Iceberg 靠 commit 提交、Job Complete 不等于业务逻辑跑对),
# 所以模板里就把这一步写进去,而不是留给使用者想起来加。
df = query(f"SELECT * FROM {TARGET} ORDER BY total_amount DESC")
print(f"{TARGET} 写入完成,{len(df)} 行:")
print(df)

if len(df) == 0:
    raise SystemExit(f"!! {TARGET} 是空的——源表有数据吗?聚合条件是不是把行都过滤掉了?")

# 数据量大了之后要改成增量:给源表加一个时间/分区列,只处理新分区,
# 用 MERGE INTO 或者按分区覆盖,而不是每次 DROP + 全量重算。
