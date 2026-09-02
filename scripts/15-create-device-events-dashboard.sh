#!/usr/bin/env bash
# Phase 2 数据工程主线("SeaTunnel → Iceberg → Airflow 调度 → Superset 看板
# 端到端跑通",见 ADR-037)最后一步:把 SeaTunnel 已经写进 demo.device_events
# 表的数据在 Superset 里注册成 Dataset + 建一张图 + 建一个 Dashboard。
#
# 表本身不在这里建——DAG(apps/airflow/dags/seatunnel_device_events.py)已经
# 跑过、表和数据都已经在 Iceberg 里了,这个脚本只做 Superset 这一侧的注册,
# 复用 scripts/06-configure-superset-datasources.sh 建好的 Trino 数据源。
#
# 图表故意用 Table 类型,不用按 event_type/device_id 分组的柱状图——DAG 里
# FakeSource 那两个字段用的是默认 string.fake.mode=range(随机字符串,不是
# 有意义的分类值,见 apps/airflow/dags/seatunnel_device_events.py),按它们
# groupby 出来的图没有实际意义,只是为了"能查、能出图"这个验证目的,Table
# 视图更直接、不依赖数据本身有没有语义。
#
# 幂等:Dataset/Chart/Dashboard 按名字查,已存在就跳过创建。
#
# 前置条件:scripts/06-configure-superset-datasources.sh 跑过,Trino/Superset
# 都在正常运行,demo.device_events 表已经有数据(跑过一次
# seatunnel_device_events 这个 DAG)。
#
# 用法:
#   ./scripts/15-create-device-events-dashboard.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/create-device-events-dashboard.log"
echo "=== create-device-events-dashboard $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

# **选择器必须带 component=web。** 2026-09-02 打开 Celery worker/beat
# 之后,`-l app.kubernetes.io/name=superset` 会同时匹配到 worker 和 beat,
# 而那两个 pod 里的容器名不是 `superset`,`kubectl exec` 直接报
# "container superset is not valid for pod ..."。加上 Running 过滤是因为
# 滚动更新期间会有 Terminating 的旧 pod 排在前面。
SUPERSET_POD=$(kubectl get pod -n superset \
  -l app.kubernetes.io/name=superset,app.kubernetes.io/component=web \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
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
            n = conn.exec_driver_sql(
                "SELECT count(*) FROM iceberg.demo.device_events"
            ).scalar()
            if not n:
                raise SystemExit(
                    "iceberg.demo.device_events 里没有数据,先跑一次 "
                    "seatunnel_device_events 这个 Airflow DAG"
                )
            print(f"确认表里有 {n} 行数据")

    dataset = db.session.query(SqlaTable).filter_by(table_name="device_events", schema="demo").first()
    if not dataset:
        dataset = SqlaTable(table_name="device_events", schema="demo", database=database)
        db.session.add(dataset)
        db.session.commit()
        print("已创建 dataset id=", dataset.id)
    else:
        print("已存在 dataset id=", dataset.id)
    dataset.fetch_metadata()
    db.session.commit()

    chart = db.session.query(Slice).filter_by(slice_name="Demo: Device Events (SeaTunnel)").first()
    if not chart:
        params = {
            "datasource": f"{dataset.id}__table",
            "viz_type": "table",
            "query_mode": "raw",
            "columns": ["event_id", "device_id", "event_type", "value", "event_time"],
            "metrics": [],
            "adhoc_filters": [],
            "order_by_cols": ['["event_id", true]'],
            "row_limit": 100,
        }
        chart = Slice(
            slice_name="Demo: Device Events (SeaTunnel)",
            datasource_type="table",
            datasource_id=dataset.id,
            viz_type="table",
            params=json.dumps(params),
        )
        db.session.add(chart)
        db.session.commit()
        print("已创建 chart id=", chart.id)
    else:
        print("已存在 chart id=", chart.id)

    dashboard = db.session.query(Dashboard).filter_by(dashboard_title="Demo: Data Engineering Pipeline").first()
    if not dashboard:
        dashboard = Dashboard(dashboard_title="Demo: Data Engineering Pipeline", slug="demo-data-engineering-pipeline")
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
            "columns": ["event_id", "device_id", "event_type", "value", "event_time"],
            "metrics": [],
            "row_limit": 100,
        }],
        result_type="full",
        force=True,
    )
    result = qc.get_payload()
    q = result["queries"][0]
    assert str(q.get("status")).endswith("SUCCESS"), f"图表查询没成功: {q.get('status')}"
    assert q.get("rowcount", 0) > 0, "图表查询返回 0 行"
    print("验证通过: 图表查询链路成功,", q.get("rowcount"), "行数据")
    print("Dashboard 地址(登录后访问): /superset/dashboard/", dashboard.id, "/", sep="")
PYEOF

kubectl cp "$TMP_SCRIPT" "superset/${SUPERSET_POD}:/tmp/create_device_events_dashboard.py" -c superset >> "$LOG_FILE" 2>&1
kubectl exec -n superset "$SUPERSET_POD" -- python3 /tmp/create_device_events_dashboard.py 2>&1 | tee -a "$LOG_FILE" | grep -vE "^$|WARNING|warnings\.warn|INFO:|^Loaded|^Defaulted"
kubectl exec -n superset "$SUPERSET_POD" -- rm -f /tmp/create_device_events_dashboard.py >> "$LOG_FILE" 2>&1 || true

echo
echo "完成。详细日志: ${LOG_FILE}"
echo "浏览器打开 http://superset.local-lite.test/superset/dashboard/demo-data-engineering-pipeline/ 看图(需要先登录)"
