#!/usr/bin/env bash
# 端到端验证 Feast 特征存储流水线(ADR-042):
#   iceberg.demo.orders(Trino建的demo表)→ Spark 离线读取 → feast apply →
#   feast materialize-incremental → Redis 在线存储 → Feature Server 在线
#   查询接口,验证 Alice/Bob 的 region/product/amount 能查到正确值。
#
# 前置条件:
#   - Feast(feature-server + redis)已经 un-park 在跑
#   - Airflow 已经 un-park 在跑(feast_materialize 这个 DAG 靠它触发)
#   - iceberg.demo.orders 这张表存在(scripts/08-create-demo-data.sh 建的,
#     如果被清空过,这个脚本会报错提示,不会自动重建——建表涉及 Superset
#     侧的 Dataset/Chart,应该走 08 号脚本走完整流程,不在这里偷懒重建)
#
# 用法:
#   ./scripts/19-feast-feature-pipeline.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/feast-feature-pipeline.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

log "=== Feast 特征流水线端到端验证开始 ==="

log "==> 确认 airflow-scheduler 在跑"
kubectl -n airflow get deploy airflow-scheduler >/dev/null 2>&1 || {
  echo "airflow-scheduler 不存在,先 un-park apps/definitions/airflow.yaml" >&2
  exit 1
}

log "==> 清掉今天的历史记录,触发一次干净的 DAG Run"
TODAY="$(date -u +%F)"
kubectl -n airflow exec deploy/airflow-scheduler -c scheduler -- \
  airflow tasks clear feast_materialize -s "$TODAY" -e "$TODAY" -y 2>&1 | tee -a "$LOG_FILE" || true
TRIGGER_OUT=$(kubectl -n airflow exec deploy/airflow-scheduler -c scheduler -- \
  airflow dags trigger feast_materialize 2>&1 | tee -a "$LOG_FILE")
RUN_ID=$(echo "$TRIGGER_OUT" | grep -oE 'manual__[0-9T:.+-]+' | head -1)
log "DAG Run: ${RUN_ID}"

log "==> 等待 DAG Run 到终态(最多 10 分钟)"
for i in $(seq 1 40); do
  STATE=$(kubectl -n airflow exec deploy/airflow-scheduler -c scheduler -- \
    airflow dags list-runs feast_materialize 2>/dev/null | grep "$RUN_ID" | awk -F'|' '{print $3}' | tr -d ' ' || true)
  log "轮询($i/40):state=${STATE}"
  if [ "$STATE" = "success" ]; then break; fi
  if [ "$STATE" = "failed" ]; then
    log "DAG Run 失败,查任务级状态:"
    kubectl -n airflow exec deploy/airflow-scheduler -c scheduler -- \
      airflow tasks states-for-dag-run feast_materialize "$RUN_ID" 2>&1 | tee -a "$LOG_FILE"
    exit 1
  fi
  sleep 15
done

if [ "$STATE" != "success" ]; then
  log "10 分钟内没跑完,手动查 kubectl -n airflow exec deploy/airflow-scheduler -c scheduler -- airflow dags list-runs feast_materialize"
  exit 1
fi

log "==> DAG Run 成功,直接查 Redis 核实数据真的落盘(不只信 Airflow 状态)"
REDIS_POD=$(kubectl get pod -n feast -l app=feast-redis -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -z "$REDIS_POD" ]; then
  REDIS_POD=$(kubectl get pod -n feast -o name | grep redis | head -1 | cut -d/ -f2)
fi
DBSIZE=$(kubectl exec -n feast "$REDIS_POD" -- redis-cli DBSIZE 2>&1)
log "Redis DBSIZE: ${DBSIZE}"

log "==> 查 Feature Server 在线接口,核实能查出正确的特征值"
FS_POD=$(kubectl get pod -n feast -l app=feast-feature-server -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -z "$FS_POD" ]; then
  FS_POD=$(kubectl get pod -n feast -o name | grep feature-server | head -1 | cut -d/ -f2)
fi
RESPONSE=$(kubectl exec -n feast "$FS_POD" -- curl -s -X POST http://localhost:6566/get-online-features \
  -H "Content-Type: application/json" \
  -d '{"features": ["customer_order_features:region", "customer_order_features:product", "customer_order_features:amount"], "entities": {"customer_name": ["Alice", "Bob"]}}')
log "在线查询返回: ${RESPONSE}"

echo "$RESPONSE" | grep -q '"East"' && echo "$RESPONSE" | grep -q '"Widget"' && echo "$RESPONSE" | grep -q '120.5' || {
  log "返回内容和预期的 Alice(East/Widget/120.5)对不上,人工核查"
  exit 1
}

log "=== 验证通过:Alice 的 region=East, product=Widget, amount=120.5,数据链路端到端跑通 ==="
