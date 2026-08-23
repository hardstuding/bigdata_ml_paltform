#!/usr/bin/env bash
# 触发一次真实的数据质量检查,然后**核实断言真的跑出结果了**。
#
# 和 scripts/30 同一个教训:不轮询 pipelineStatus 那个接口(1.13.3 上它
# 返回 404,scripts/30 一开始就是栽在这儿,把成功的采集报告成失败),
# 直接轮询业务结果——三条断言在目录里各自有没有 testCaseResult。
#
# 这个脚本**允许断言结果是 Failed**:断言失败说明数据真有问题,那正是它
# 该干的活;这里验的是"这套机制活着、能出结果",不是"数据一定是干净的"。
# 分不清这两件事的话,以后真的检出脏数据时会有人来改这个脚本让它变绿。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/verify-openmetadata-data-quality.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

TRA_NS="table-registration-app"
TRA_POD="$(kubectl -n "$TRA_NS" get pod -l app=table-registration-app -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$TRA_POD" ] || { log "table-registration-app 没有 Running 的 Pod。"; exit 1; }

PY_SCRIPT="$(mktemp)"
trap 'rm -f "$PY_SCRIPT"' EXIT
cat > "$PY_SCRIPT" <<'PYEOF'
import os
import sys
import time

import requests

TOKEN = os.environ["OPENMETADATA_TOKEN"]
BASE = os.environ.get("OPENMETADATA_URL", "http://openmetadata.openmetadata.svc.cluster.local:8585")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TABLE_FQN = "trino.iceberg.demo.orders"
EXPECTED = {"orders_row_count_not_empty", "orders_order_id_unique", "orders_amount_not_null"}


def om(method, path, body=None, ok404=False):
    resp = requests.request(method, f"{BASE}{path}", headers=HEADERS, json=body, timeout=60)
    if ok404 and resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json() if resp.content else None


pipeline = om("GET", "/api/v1/services/ingestionPipelines/name/"
                     f"{TABLE_FQN}.testSuite.orders_data_quality")
print(f"pipeline: {pipeline['fullyQualifiedName']}")
om("POST", f"/api/v1/services/ingestionPipelines/trigger/{pipeline['id']}")
print("已触发一次检查,等结果落到各条断言上(最多 10 分钟)...")

deadline = time.time() + 600
results = {}
while time.time() < deadline:
    # **不要用 entityLink 过滤。** 实测:按 `<#E::table::FQN>` 过滤只会返回
    # 表级别的断言,列级断言的 entityLink 带着 `::columns::<列名>` 后缀,
    # 匹配不上——第一版就是这么写的,结果两条列级断言明明已经 Success,
    # 脚本却一直等到超时报 FAIL。又是一次"验证脚本自己错了,把成功报成
    # 失败"(scripts/30 也栽过同一类跟头)。列出来按名字挑,朴素但是对的。
    listing = om("GET", "/api/v1/dataQuality/testCases?fields=testCaseResult&limit=200")
    results = {
        c["name"]: (c.get("testCaseResult") or {}).get("testCaseStatus")
        for c in listing.get("data", [])
        if c["name"] in EXPECTED
    }
    if EXPECTED <= set(results) and all(results.get(n) for n in EXPECTED):
        break
    print(f"  还没有全部结果,当前:{results},20 秒后重试")
    time.sleep(20)

print("\n最终结果:")
for name in sorted(EXPECTED):
    print(f"  {name}: {results.get(name) or '(没有结果)'}")

missing = [n for n in EXPECTED if not results.get(n)]
if missing:
    sys.exit(f"FAIL:这几条断言没有跑出任何结果 {missing}")
print("\nDATA_QUALITY_OK")
PYEOF

log "触发检查并核实结果 ..."
if kubectl -n "$TRA_NS" exec -i "$TRA_POD" -- python3 - < "$PY_SCRIPT" 2>&1 | tee -a "$LOG_FILE" | grep -q "DATA_QUALITY_OK"; then
  log "通过:三条断言都跑出了结果(结果是 Success 还是 Failed 由数据本身决定,不影响这里的判定)。"
else
  log "!! 失败,上面输出里有原因。常见方向:(1) openmetadata 命名空间的 Job 有没有建出来"
  log "   kubectl -n openmetadata get job | grep -i quality"
  log "(2) Job 建出来但 Pending/ImagePullBackOff:ingestion-base 镜像没缓存"
  log "(3) Job 跑了但失败:kubectl -n openmetadata logs job/<名字>"
  exit 1
fi
