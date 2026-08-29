"""每天把 iceberg.demo.orders 按区域汇总,写回 orders_by_region_daily。

用 platform_sdk 拿连接,不手填连接串——连接信息由平台注入(见
platform_sdk.config),换环境不用改这个脚本。
"""
from platform_sdk import query

# 同目录下的 jobkit.py —— 多文件作业(2026-08-29)。平台把作业目录下所有
# .py 一起挂进容器并把 PYTHONPATH 指过去,所以这行 import 直接可用。
from jobkit import rows_of, run_date

TARGET = "iceberg.demo.orders_by_region_daily"

# 处理哪一天:默认今天,可以用 run_date 参数覆盖 —— **这就是补数**:
#   argo submit --from cronwf/daily-order-summary -p run_date=2026-08-01
RUN_DATE = run_date()


# 目标表不存在就先建。CREATE TABLE IF NOT EXISTS 让这个作业可以从零开始跑,
# 不需要谁先手动建表——"从空环境可恢复"这条对作业同样成立。
query(f"""
    CREATE TABLE IF NOT EXISTS {TARGET} (
        run_date     date,
        region       varchar,
        order_count  bigint,
        total_amount double
    )
""")

# 先删当天的,再插入。这样重跑不会产生重复行(幂等)——定时作业迟早会被
# 重跑一次(补数、失败重试、开机补跑),不幂等的作业到那天才会暴露问题。
query(f"DELETE FROM {TARGET} WHERE run_date = DATE '{RUN_DATE}'")

query(f"""
    INSERT INTO {TARGET}
    SELECT DATE '{RUN_DATE}', region, count(*), sum(amount)
    FROM iceberg.demo.orders
    GROUP BY region
""")

check = rows_of(query(
    f"SELECT region, order_count, total_amount FROM {TARGET} "
    f"WHERE run_date = DATE '{RUN_DATE}' ORDER BY region"))

print(f"{RUN_DATE} 汇总完成,{len(check)} 个区域:")
for region, cnt, amount in check:
    print(f"  {region:<8} {cnt:>4} 单  {float(amount):>10.2f}")

# 写完要核实真的写进去了。作业"没报错"和"产出了东西"是两回事——这个平台
# 被"看起来成功了"坑过太多次。
if not check:
    raise SystemExit("汇总结果是空的——上游 iceberg.demo.orders 可能没有数据")
