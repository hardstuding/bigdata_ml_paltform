#!/usr/bin/env bash
# 打开 Superset impersonation(ADR-074)之前必须先跑的一步:**盘一遍
# Superset 里实际在用哪些表、以及哪些人在用**。
#
# **为什么这一步不能跳**:打开 impersonation 之后 Trino 看到的是登录用户
# 本人,而不再是那个无条件放行的 superset_service。也就是说——**之前所有
# 能在 Superset 里查的表,现在都需要那个人有对应的 grant**
# (platform/iam/table-access-grants.csv)。不先盘一遍就切,结果是一片看板
# 同时报 PERMISSION_DENIED,而且报错发生在用户那边、不在我们这边。
#
# 这和 ADR-051 第一次给 Trino 接 OPA 时是同一个动作,那次也是先盘表再切。
#
# 输出三样:
#   1. Superset 里注册的数据集用到的表(dbs/tables 两张表)
#   2. platform-team 之外、真正登录过 Superset 的用户
#   3. 这些用户当前的 grant 覆盖情况 —— **差集就是切换前要补的**
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/audit-superset-tables.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

# 标签是 2026-08-26 实测确认的(第一版写的 component=node 和 app=superset
# 都选不中,脚本直接报"找不到 Pod")。chart 给 web 这一档打的是
# component=web,worker 那档才是别的值——这里只要 web。
SS_POD="$(kubectl -n superset get pod -l app.kubernetes.io/name=superset,app.kubernetes.io/component=web \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$SS_POD" ] || { log "找不到 Superset 的 Pod。"; exit 1; }
log "用 ${SS_POD} 直接查 Superset 自己的元数据库"

kubectl -n superset exec -i "$SS_POD" -- python3 - <<'PYEOF' 2>&1 | tee -a "$LOG_FILE"
from superset.app import create_app
app = create_app()
with app.app_context():
    from superset import db
    from sqlalchemy import text

    def q(sql):
        return list(db.session.execute(text(sql)))

    print("=== 1. Superset 数据集实际引用的表 ===")
    rows = q("""select d.database_name, t.schema, t.table_name,
                       (select count(*) from slices s where s.datasource_id = t.id) as charts
                from tables t join dbs d on d.id = t.database_id
                order by d.database_name, t.schema, t.table_name""")
    if not rows:
        print("  (一个数据集都没有)")
    for r in rows:
        print(f"  {r[0]}.{r[1]}.{r[2]}   被 {r[3]} 个 chart 引用")

    print("\n=== 2. 登录过 Superset 的用户(排除 admin 本人) ===")
    users = q("""select u.username, count(distinct l.id) as logins,
                        string_agg(distinct r.name, ',') as roles
                 from ab_user u
                 left join logs l on l.user_id = u.id
                 left join ab_user_role ur on ur.user_id = u.id
                 left join ab_role r on r.id = ur.role_id
                 group by u.username order by logins desc""")
    for r in users:
        print(f"  {r[0]:24s} 操作记录 {r[1]:5d} 条   角色={r[2]}")

    print("\n=== 3. 打开 impersonation 之后,这些人需要的 grant ===")
    print("  规则:platform-team 的人不用配(is_platform_admin 全放行);")
    print("  其他人对上面第 1 节里每一张表都要有 grant,否则看板会 PERMISSION_DENIED。")
PYEOF

log "接下来:把上面第 1 节的表 × 第 2 节的非 platform-team 用户,"
log "逐条对照 platform/iam/table-access-grants.csv,缺的补上再切 impersonation。"
log "**这一步没做完就跑 scripts/06,会让一批看板同时挂掉。**"
