#!/usr/bin/env bash
# 端到端 demo:建一张真实的 Iceberg 表(通过 Trino,数据真实落盘到 MinIO)、
# 塞几行样例数据,然后在 Superset 里注册成 Dataset + 建一张图 + 建一个
# Dashboard——验证的是 docs/architecture.md 路线图 Phase 1/2 的退出标准
# ("建一张 Iceberg 表、写入,Trino 查、Superset 出图")。
#
# 复用 scripts/06-configure-superset-datasources.sh 已经建好的 Trino 数据源
# (走 ADR-021 的服务账号),不重新处理认证。
#
# 幂等:表/schema 用 IF NOT EXISTS,已有数据先清空再插入(保证每次跑出来的
# 数字一样,方便核对);Dataset/Chart/Dashboard 按名字查,已存在就跳过创建。
#
# 前置条件:scripts/06-configure-superset-datasources.sh 跑过,Trino/Superset
# 都在正常运行。
#
# 用法:
#   ./scripts/08-create-demo-data.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/create-demo-data.log"
echo "=== create-demo-data $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

SUPERSET_POD=$(kubectl get pod -n superset -l app.kubernetes.io/name=superset -o jsonpath='{.items[0].metadata.name}')
if [ -z "$SUPERSET_POD" ]; then
  echo "找不到 superset pod,先确认它在跑" >&2
  exit 1
fi

TMP_SCRIPT="$(mktemp)"
trap 'rm -f "$TMP_SCRIPT"' EXIT
cat > "$TMP_SCRIPT" <<'PYEOF'
import json
from superset.app import create_app
app = create_app()
with app.app_context():
    from superset import db
    from superset.models.core import Database
    from superset.connectors.sqla.models import SqlaTable
    from superset.models.slice import Slice
    from superset.models.dashboard import Dashboard

    database = db.session.query(Database).filter_by(database_name="Trino").first()
    if not database:
        raise SystemExit("Trino 数据源不存在,先跑 scripts/06-configure-superset-datasources.sh")

    with database.get_sqla_engine() as engine:
        with engine.connect() as conn:
            def run(sql):
                print(">>>", sql.strip().splitlines()[0][:80])
                conn.exec_driver_sql(sql)

            # 不写 WITH (location = ...):显式指定 external location 会报
            # "Failed to create external path ... : null",用 Hive Metastore
            # 默认的 warehouse 路径就行,见 troubleshooting.md。
            run("CREATE SCHEMA IF NOT EXISTS iceberg.demo")
            run("""
                CREATE TABLE IF NOT EXISTS iceberg.demo.orders (
                    order_id BIGINT,
                    customer_name VARCHAR,
                    region VARCHAR,
                    product VARCHAR,
                    amount DECIMAL(10,2),
                    order_date DATE
                )
            """)
            run("DELETE FROM iceberg.demo.orders")
            run("""
                INSERT INTO iceberg.demo.orders VALUES
                (1, 'Alice',   'East',  'Widget', 120.50, DATE '2026-07-01'),
                (2, 'Bob',     'West',  'Gadget',  85.00, DATE '2026-07-03'),
                (3, 'Carol',   'East',  'Widget',  60.25, DATE '2026-07-05'),
                (4, 'Dave',    'North', 'Gizmo',  200.00, DATE '2026-07-10'),
                (5, 'Eve',     'West',  'Widget',  45.75, DATE '2026-07-12'),
                (6, 'Frank',   'South', 'Gadget', 150.00, DATE '2026-07-15'),
                (7, 'Grace',   'East',  'Gizmo',   99.99, DATE '2026-07-18'),
                (8, 'Heidi',   'North', 'Widget',  75.50, DATE '2026-07-20'),
                (9, 'Ivan',    'South', 'Gadget', 130.00, DATE '2026-07-22'),
                (10,'Judy',    'West',  'Gizmo',  110.25, DATE '2026-07-25')
            """)

    dataset = db.session.query(SqlaTable).filter_by(table_name="orders", schema="demo").first()
    if not dataset:
        dataset = SqlaTable(table_name="orders", schema="demo", database=database)
        db.session.add(dataset)
        db.session.commit()
        print("已创建 dataset id=", dataset.id)
    else:
        print("已存在 dataset id=", dataset.id)
    dataset.fetch_metadata()
    db.session.commit()

    chart = db.session.query(Slice).filter_by(slice_name="Demo: Orders by Region").first()
    if not chart:
        params = {
            "datasource": f"{dataset.id}__table",
            "viz_type": "dist_bar",
            "groupby": ["region"],
            "metrics": [{"expressionType": "SQL", "sqlExpression": "SUM(amount)", "label": "Total Amount"}],
            "adhoc_filters": [],
            "row_limit": 100,
        }
        chart = Slice(
            slice_name="Demo: Orders by Region",
            datasource_type="table",
            datasource_id=dataset.id,
            viz_type="dist_bar",
            params=json.dumps(params),
        )
        db.session.add(chart)
        db.session.commit()
        print("已创建 chart id=", chart.id)
    else:
        print("已存在 chart id=", chart.id)

    dashboard = db.session.query(Dashboard).filter_by(dashboard_title="Demo: Lakehouse Core Path").first()
    if not dashboard:
        dashboard = Dashboard(dashboard_title="Demo: Lakehouse Core Path", slug="demo-lakehouse-core-path")
        dashboard.slices = [chart]
        db.session.add(dashboard)
        db.session.commit()
        print("已创建 dashboard id=", dashboard.id, "slug=", dashboard.slug)
    else:
        if chart not in dashboard.slices:
            dashboard.slices.append(chart)
            db.session.commit()
        print("已存在 dashboard id=", dashboard.id, "slug=", dashboard.slug)

    # 验证:走 Superset 真实的图表查询链路(前端渲染图表用的就是这条),
    # 不是只存了个连接串/记录。
    from superset.common.query_context_factory import QueryContextFactory
    qc = QueryContextFactory().create(
        datasource={"type": "table", "id": dataset.id},
        queries=[{
            "columns": ["region"],
            "metrics": [{"expressionType": "SQL", "sqlExpression": "SUM(amount)", "label": "Total Amount"}],
            "row_limit": 100,
        }],
        result_type="full",
        force=True,
    )
    result = qc.get_payload()
    q = result["queries"][0]
    assert str(q.get("status")).endswith("SUCCESS"), f"图表查询没成功: {q.get('status')}"
    assert q.get("rowcount") == 4, f"预期 4 个 region,实际 {q.get('rowcount')}"
    print("验证通过: 图表查询链路成功,", q.get("rowcount"), "行数据")
    print("Dashboard 地址(登录后访问): /superset/dashboard/", dashboard.id, "/", sep="")
PYEOF

kubectl cp "$TMP_SCRIPT" "superset/${SUPERSET_POD}:/tmp/create_demo_data.py" -c superset >> "$LOG_FILE" 2>&1
kubectl exec -n superset "$SUPERSET_POD" -- python3 /tmp/create_demo_data.py 2>&1 | tee -a "$LOG_FILE" | grep -vE "^$|WARNING|warnings\.warn|INFO:|^Loaded|^Defaulted"
kubectl exec -n superset "$SUPERSET_POD" -- rm -f /tmp/create_demo_data.py >> "$LOG_FILE" 2>&1 || true

echo
echo "完成。详细日志: ${LOG_FILE}"
echo "浏览器打开 http://superset.local-lite.test/superset/dashboard/demo-lakehouse-core-path/ 看图(需要先登录)"
