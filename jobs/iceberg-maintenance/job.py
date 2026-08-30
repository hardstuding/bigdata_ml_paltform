"""Iceberg 表维护:过期快照 + 清孤儿文件 + 合并小文件。

**为什么需要**:Iceberg 每次写入都产生一个新快照,旧快照和它引用的数据
文件不会自动清理;失败的写入会留下没人引用的孤儿文件;流式写入每个
checkpoint 落一批小文件。三件事都是**只增不减**的,不管的话:

- 存储无限增长(而这个平台的 MinIO 是单副本、没有备份)
- 查询越来越慢(小文件多 → 打开的文件句柄多、元数据大)

**最先出问题的是持续写入的表**:`audit.query_events`、
`ml.inference_log`、`demo.device_events_stream`。

用 Trino 的 `ALTER TABLE ... EXECUTE` 做,不引入新组件 —— Iceberg 的这几个
维护动作 Trino 原生支持。
"""
from platform_sdk import query

from jobkit import param, rows_of

RETAIN_DAYS = int(param("retain_days", "7"))
DRY_RUN = bool(param("dry_run"))

# **只维护平台自己产生的表。** 不去动 tpch/tpcds 这类只读的基准数据集
# (它们不写入,没有快照堆积),也不自作主张动用户自己建的表 —— 合并小
# 文件会重写数据,那是一个有副作用的操作,不该由平台默默对所有表执行。
SCHEMAS = ["audit", "ml", "demo"]


def tables_in(schema):
    """**只要真正的表,不要视图。**

    2026-08-30 实机第一次跑就撞到:`iceberg.demo.stg_orders` 是 dbt 建的
    视图,而 `ALTER TABLE ... EXECUTE` 对视图直接报 `NOT_SUPPORTED` ——
    三个动作全失败。视图没有数据文件也没有快照,本来就不需要维护。
    """
    try:
        return [r[0] for r in rows_of(query(
            f"SELECT table_name FROM iceberg.information_schema.tables "
            f"WHERE table_schema = '{schema}' AND table_type = 'BASE TABLE'"))]
    except Exception as exc:   # noqa: BLE001
        # schema 不存在是正常的(比如 ml 要等推理留痕启用之后才有)
        print(f"  跳过 schema {schema}:{str(exc).splitlines()[0][:80]}")
        return []


def maintain(fqn):
    """三个动作。**任何一个失败都继续做下一个** —— 一张表的维护失败不该
    让其它表也不做,而"这次没清干净"的后果只是下次多清一点。"""
    done, failed = [], []
    actions = [
        # 顺序有讲究:先合并小文件(会产生新快照),再过期快照(把合并前
        # 的旧快照清掉),最后清孤儿文件。反过来的话刚合并出来的旧文件
        # 还被快照引用着,清不掉。
        ("optimize", f"ALTER TABLE {fqn} EXECUTE optimize"),
        ("expire_snapshots",
         f"ALTER TABLE {fqn} EXECUTE expire_snapshots(retention_threshold => '{RETAIN_DAYS}d')"),
        ("remove_orphan_files",
         f"ALTER TABLE {fqn} EXECUTE remove_orphan_files(retention_threshold => '{RETAIN_DAYS}d')"),
    ]
    for name, sql in actions:
        if DRY_RUN:
            print(f"  [dry-run] {name}")
            continue
        try:
            query(sql)
            done.append(name)
        except Exception as exc:   # noqa: BLE001
            failed.append(f"{name}({str(exc).splitlines()[0][:60]})")
    return done, failed


print(f"Iceberg 表维护:保留 {RETAIN_DAYS} 天快照"
      f"{'(dry-run,只打印不执行)' if DRY_RUN else ''}")

total, all_failed = 0, []
for schema in SCHEMAS:
    names = tables_in(schema)
    if not names:
        continue
    print(f"\nschema {schema}:{len(names)} 张表")
    for t in names:
        fqn = f"iceberg.{schema}.{t}"
        done, failed = maintain(fqn)
        total += 1
        status = f"{len(done)}/3 成功" if not DRY_RUN else "dry-run"
        print(f"  {fqn:50s} {status}" + (f"  失败:{'; '.join(failed)}" if failed else ""))
        all_failed += [f"{fqn}: {f}" for f in failed]

print(f"\n处理了 {total} 张表")
if all_failed:
    # **不让整个作业失败** —— 部分表维护失败是常态(表正在被写、权限不够、
    # Trino 版本不支持某个动作),而把作业标成红色会让人学会忽略它。
    # 但要把失败列出来,不能静默。
    print(f"\n有 {len(all_failed)} 个动作失败(作业本身不算失败,下次会再试):")
    for f in all_failed:
        print(f"  - {f}")
if total == 0:
    raise SystemExit("一张表都没处理到 —— 检查 iceberg catalog 是否可访问")
