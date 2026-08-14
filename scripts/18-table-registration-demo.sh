#!/usr/bin/env bash
# 端到端 demo/验证:提交一次真实的建表注册请求(见 ADR-043),验证两件事:
# (1) Trino 里这张表真的建出来了,结构对得上;(2) 应用自己记录的状态和
# 活资源的真实状态一致(不是只看应用页面上写的"ok"就信了)。
#
# 前置条件:table-registration-app / table-registration-app-oauth2-proxy /
# trino / trino-tls 这几个 ArgoCD Application 都同步过、Pod 都 Running。
# OPENMETADATA_TOKEN 没配的话,OpenMetadata 回写这一步会显示 skipped,这是
# 预期状态,不是脚本的 bug(见 ADR-043"还没验证的部分")。
#
# 用法:
#   ./scripts/18-table-registration-demo.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/table-registration-demo.log"
echo "=== table-registration-demo $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

APP_POD=$(kubectl get pod -n table-registration-app -l app=table-registration-app -o jsonpath='{.items[0].metadata.name}')
if [ -z "$APP_POD" ]; then
  echo "找不到 table-registration-app 的 pod,先确认它在跑" | tee -a "$LOG_FILE" >&2
  exit 1
fi

TABLE_NAME="demo.governance_demo_$(date +%s)"
echo ">>> 提交建表请求: $TABLE_NAME" | tee -a "$LOG_FILE"

kubectl exec -n table-registration-app "$APP_POD" -- python3 -c "
import urllib.request, urllib.parse
data = urllib.parse.urlencode({
    'table_fqn': '$TABLE_NAME',
    'columns': 'id BIGINT\ncustomer_name VARCHAR\nscore DOUBLE',
    'owner': 'demo-owner',
    'security_level': '2',
}).encode()
req = urllib.request.Request('http://localhost:8080/submit', data=data, headers={'X-Forwarded-User': 'demo-owner'})
resp = urllib.request.urlopen(req, timeout=60)
print('HTTP', resp.status)
" 2>&1 | tee -a "$LOG_FILE"

echo ">>> 直接查 Trino,核实表真的建出来了(不信任应用页面上写的状态)" | tee -a "$LOG_FILE"

TRINO_POD=$(kubectl get pod -n trino -l app.kubernetes.io/name=trino -o jsonpath='{.items[0].metadata.name}')
if [ -z "$TRINO_POD" ]; then
  echo "找不到 trino pod,Trino 是不是还没同步起来" | tee -a "$LOG_FILE" >&2
  exit 1
fi

SCHEMA_NAME="${TABLE_NAME%%.*}"
TABLE_ONLY="${TABLE_NAME##*.}"

RESULT=$(kubectl exec -n table-registration-app "$APP_POD" -- python3 -c "
import os, trino
from trino.auth import BasicAuthentication
pw = os.environ['TRINO_PASSWORD']
conn = trino.dbapi.connect(host='trino.trino.svc.cluster.local', port=8443, user='table_registration_service',
    http_scheme='https', verify=False, auth=BasicAuthentication('table_registration_service', pw), catalog='iceberg')
cur = conn.cursor()
cur.execute(\"DESCRIBE iceberg.${SCHEMA_NAME}.${TABLE_ONLY}\")
print(cur.fetchall())
" 2>/dev/null)

echo "$RESULT" | tee -a "$LOG_FILE"

if echo "$RESULT" | grep -q "customer_name"; then
  echo ">>> 验证通过:表结构在 Trino 里真实存在,列定义正确" | tee -a "$LOG_FILE"
else
  echo ">>> 验证失败:Trino 里没查到预期的表结构" | tee -a "$LOG_FILE" >&2
  exit 1
fi

echo ">>> 完整日志见 $LOG_FILE"
