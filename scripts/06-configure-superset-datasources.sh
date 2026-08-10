#!/usr/bin/env bash
# 给 Superset 注册 Trino 数据源(用 ADR-021 那个服务账号)。这一步是命令式
# 操作(用 superset app context 里的 Database model 建记录),不在 GitOps
# 管理范围内——Superset 没有官方支持的"用 YAML 声明数据源"方案,数据源存在
# 它自己的 Postgres 元数据库里,和 Keycloak realm 那种情况类似(见
# docs/decisions/009)。
#
# 幂等:数据源已存在(按 database_name 找)就更新连接串,不会重复创建。
#
# 关键的坑:Trino 的 SQLAlchemy URI 本身不支持把 http_scheme/verify 当查询
# 参数写在 URI 里(试过 `?http_scheme=https&verify=false`,实际连接时客户端
# 还是用了明文 HTTP 去连 Trino 的 HTTPS-only 端口,报
# "BadStatusLine(...TLS alert 的字节序列...)"——服务端返回的是 TLS
# 握手/告警数据,客户端却当成 HTTP 响应解析,顾名思义就是协议对不上)。
# 必须通过 Superset Database 模型的 `extra` 字段(JSON)下的
# `engine_params.connect_args` 传,这是 Superset "高级" - "引擎参数"那个
# UI 输入框背后对应的字段,CLI/脚本这条路只能走同一个机制,不是抄近路。
#
# 前置条件:scripts/00-generate-secrets.sh 跑过(生成了 trino-service-account
# 并复制到 superset 命名空间),Trino 和 Superset 都在正常运行。
#
# 用法:
#   ./scripts/06-configure-superset-datasources.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/configure-superset-datasources.log"
echo "=== configure-superset-datasources $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

SUPERSET_POD=$(kubectl get pod -n superset -l app.kubernetes.io/name=superset -o jsonpath='{.items[0].metadata.name}')
if [ -z "$SUPERSET_POD" ]; then
  echo "找不到 superset pod,先确认它在跑" >&2
  exit 1
fi

TRINO_SVC_PW=$(kubectl -n superset get secret trino-service-account -o jsonpath='{.data.password}' | base64 -d)

TMP_SCRIPT="$(mktemp)"
trap 'rm -f "$TMP_SCRIPT"' EXIT
cat > "$TMP_SCRIPT" <<PYEOF
import json
from superset.app import create_app
app = create_app()
with app.app_context():
    from superset import db
    from superset.models.core import Database

    uri = "trino://superset_service:${TRINO_SVC_PW}@trino.trino.svc.cluster.local:8443/iceberg"
    extra = {
        "engine_params": {
            "connect_args": {
                "http_scheme": "https",
                "verify": False
            }
        }
    }

    d = db.session.query(Database).filter_by(database_name="Trino").first()
    if d:
        d.sqlalchemy_uri = uri
        d.extra = json.dumps(extra)
        db.session.commit()
        print(f"已更新: Trino 数据源(id={d.id})")
    else:
        d = Database(database_name="Trino", sqlalchemy_uri=uri, extra=json.dumps(extra))
        db.session.add(d)
        db.session.commit()
        print(f"已创建: Trino 数据源(id={d.id})")

    # 顺手验证一下能不能真的查——不只是存了个连接串,是真的连得通、查得动。
    with d.get_sqla_engine() as engine:
        with engine.connect() as conn:
            result = list(conn.exec_driver_sql("SELECT 1 AS ok"))
            assert result == [(1,)], f"预期 [(1,)],实际 {result}"
            print("验证通过: SELECT 1 查询成功")
PYEOF

kubectl cp "$TMP_SCRIPT" "superset/${SUPERSET_POD}:/tmp/configure_datasources.py" -c superset >> "$LOG_FILE" 2>&1
kubectl exec -n superset "$SUPERSET_POD" -- python3 /tmp/configure_datasources.py 2>&1 | tee -a "$LOG_FILE" | grep -E "已创建|已更新|验证通过|Error|Traceback" || true
kubectl exec -n superset "$SUPERSET_POD" -- rm -f /tmp/configure_datasources.py >> "$LOG_FILE" 2>&1 || true

echo
echo "完成。详细日志: ${LOG_FILE}"
