"""数据质量断言模板:查一张表 → 断言 → 不合格就非零退出。

**和 OpenMetadata 自带的数据质量(ADR-065)是什么关系**:那套负责"平台
统一登记的断言,定时跑、结果进目录、失败进告警",覆盖的是**表本身**的
通用规则(行数、非空、唯一、新鲜度)。这份模板负责另一半——**只有写这个
作业的人才知道的业务规则**:

    "退款金额不能大于订单金额"
    "每个大区每天至少要有一笔订单"
    "状态字段只能是这五个值之一"

这类规则没法用通用断言表达,而它们恰恰是最容易出问题的地方。

**为什么用「作业失败」而不是「打印警告」**:作业挂了会有人管(Argo 里
是红的、告警会响);打印的警告没人看。数据质量的价值在于**阻断**,
不是在于记录——ADR-065 里写过同样的判断。
"""

from platform_sdk import query

failures = []


def check(name: str, sql: str, ok):
    """跑一条 SQL,把第一行第一列交给 ok() 判断。

    ok 返回 True 就通过,返回字符串就是失败原因。
    """
    df = query(sql)
    value = df.iloc[0, 0] if hasattr(df, "iloc") else df[1][0][0]
    verdict = ok(value)
    if verdict is True:
        print(f"  通过  {name}(实际值 {value})")
    else:
        print(f"  失败  {name}:{verdict}(实际值 {value})")
        failures.append(name)


print("开始检查 iceberg.demo.orders")

check("表不能是空的",
      "SELECT count(*) FROM iceberg.demo.orders",
      lambda v: True if v > 0 else "一行都没有")

check("金额不能为负",
      "SELECT count(*) FROM iceberg.demo.orders WHERE amount < 0",
      lambda v: True if v == 0 else f"有 {v} 行金额是负数")

check("大区必须在已知范围内",
      "SELECT count(*) FROM iceberg.demo.orders "
      "WHERE region NOT IN ('East','West','North','South')",
      lambda v: True if v == 0 else f"有 {v} 行的 region 不认识")

check("订单号不能重复",
      "SELECT count(*) - count(DISTINCT order_id) FROM iceberg.demo.orders",
      lambda v: True if v == 0 else f"有 {v} 个重复的 order_id")

if failures:
    # **非零退出是这个模板的重点。** 打印完就正常结束的话,上游的调度器
    # 会认为一切正常,而脏数据已经流到下游了。
    raise SystemExit(f"\n!! {len(failures)} 条检查没通过:{', '.join(failures)}")

print(f"\n全部 {4 - len(failures)} 条检查通过。")
