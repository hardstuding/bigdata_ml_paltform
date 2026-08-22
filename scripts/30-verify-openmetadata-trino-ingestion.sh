#!/usr/bin/env bash
# 端到端验证 scripts/29-configure-openmetadata-trino-ingestion.sh 配好的采集
# 任务是不是真的能用,不是只看 deploy 请求的返回码——这个仓库对"任务
# success 不等于业务逻辑跑对"很敏感(见 CLAUDE.md),这里同样适用:
# OpenMetadata 的 /trigger 接口返回 200 只代表"排队成功",真正要确认的是
# (1) 扫描任务本身跑完是 success/partialSuccess 而不是 failed,(2) 扫描出来
# 的内容里,一张我们知道真实存在、结构确定的 Trino 表(iceberg.demo.orders,
# scripts/08-create-demo-data.sh 建的)真的出现在 OpenMetadata 目录里,而且
# 字段名对得上——不是随便一张历史上被手动登记过的表。
#
# 触发之后到真的能 trigger 成功之间有一个已知的时间窗口(OpenMetadata 社区
# 记录过的行为:deploy 刚完成时,底层调度器还没来得及注册这个 DAG/CronJob,
# 立刻 trigger 会 400,大概 10-15 秒后才稳定),这个脚本对 trigger 这一步
# 做了重试,不是"报错就直接判失败"。
#
# 前置条件:scripts/29-configure-openmetadata-trino-ingestion.sh 已经跑过。
#
# 用法:
#   ./scripts/30-verify-openmetadata-trino-ingestion.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/verify-openmetadata-trino-ingestion.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

OM_NS="openmetadata"
TRA_NS="table-registration-app"

log "检查前置条件 ..."
if ! kubectl -n "$OM_NS" get deploy openmetadata >/dev/null 2>&1; then
  echo "openmetadata 命名空间没有 Deployment,先确认组件已启用并同步过。" >&2
  exit 1
fi

TRA_POD="$(kubectl -n "$TRA_NS" get pod -l app=table-registration-app -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [ -z "$TRA_POD" ]; then
  echo "table-registration-app 命名空间找不到 Running 的 Pod(这个脚本借用它的 python3+requests 调 OpenMetadata API)。" >&2
  exit 1
fi

PY_SCRIPT="$(mktemp)"
trap 'rm -f "$PY_SCRIPT"' EXIT
cat > "$PY_SCRIPT" <<'PYEOF'
import json
import os
import sys
import time

import requests

TOKEN = os.environ["OPENMETADATA_TOKEN"]
BASE = os.environ.get("OPENMETADATA_URL", "http://openmetadata.openmetadata.svc.cluster.local:8585")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def om(method, path, body=None, ok404=False):
    resp = requests.request(method, f"{BASE}{path}", headers=HEADERS, json=body, timeout=30)
    if ok404 and resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json() if resp.content else None


# 1. 找到 scripts/29 建好的 pipeline。
pipeline = om("GET", "/api/v1/services/ingestionPipelines/name/trino.trino_metadata", ok404=True)
if not pipeline:
    print("FAIL: 找不到 trino.trino_metadata 这个 ingestion pipeline,先跑 scripts/29。")
    sys.exit(1)
pid = pipeline["id"]
print(f"pipeline id={pid}, deployed={pipeline.get('deployed')}")

# 2. 触发一次真实扫描。刚 deploy 完立刻 trigger 有已知的竞态(社区记录过的
#    "DAG 还没注册好"),重试到 90 秒。
triggered = False
last_err = None
for attempt in range(18):
    try:
        om("POST", f"/api/v1/services/ingestionPipelines/trigger/{pid}")
        triggered = True
        break
    except requests.HTTPError as e:
        last_err = e
        time.sleep(5)
if not triggered:
    print(f"FAIL: trigger 重试 90 秒后仍然失败: {last_err}")
    sys.exit(1)
print("triggered ok")

# 3. 轮询这次运行的状态,最多等 10 分钟——第一次跑很可能要现拉
#    docker.getcollate.io/openmetadata/ingestion-base 这个镜像(未验证过大小,
#    如果这台集群从来没缓存过,可能是这个脚本超时的最常见原因,不代表配置
#    本身是错的,见 scripts/29 头部注释里关于镜像缓存的风险标注)。
state = None
deadline = time.time() + 600
while time.time() < deadline:
    statuses = om("GET", f"/api/v1/services/ingestionPipelines/{pid}/pipelineStatus", ok404=True)
    entries = statuses.get("data") if isinstance(statuses, dict) else statuses
    if entries:
        latest = sorted(entries, key=lambda x: x.get("timestamp", 0))[-1]
        state = latest.get("pipelineState")
        print(f"  pipelineState={state}")
        if state in ("success", "partialSuccess", "failed"):
            break
    time.sleep(15)

if state not in ("success", "partialSuccess"):
    print(f"FAIL: 扫描没有在 10 分钟内跑到 success/partialSuccess(最终状态: {state})")
    sys.exit(1)
print(f"扫描完成,状态: {state}")

# 4. 独立核实:不信任务状态,直接查一张我们知道真实结构的表有没有真的出现
#    在目录里,字段名对不对——scripts/08-create-demo-data.sh 建的
#    iceberg.demo.orders,列是 order_id/customer_name/region/product/
#    amount/order_date。
table = om("GET", "/api/v1/tables/name/trino.iceberg.demo.orders?fields=columns", ok404=True)
if not table:
    print("FAIL: OpenMetadata 目录里找不到 trino.iceberg.demo.orders,采集没有真的把这张表发现出来。")
    sys.exit(1)

expected_cols = {"order_id", "customer_name", "region", "product", "amount", "order_date"}
actual_cols = {c["name"].lower() for c in table.get("columns", [])}
missing = expected_cols - actual_cols
if missing:
    print(f"FAIL: trino.iceberg.demo.orders 在目录里但字段对不上,缺: {missing},实际: {actual_cols}")
    sys.exit(1)

print(f"独立核实通过: trino.iceberg.demo.orders 存在,字段 {sorted(actual_cols)} 和 Trino 里真实的表结构一致。")
print("OPENMETADATA_TRINO_INGESTION_OK")
PYEOF

log "触发一次真实采集并轮询结果(最多等 10 分钟,取决于 ingestion-base 镜像有没有缓存)..."
set +e
OUTPUT="$(kubectl -n "$TRA_NS" exec -i "$TRA_POD" -- python3 - < "$PY_SCRIPT" 2>&1)"
RC=$?
set -e
echo "$OUTPUT" | tee -a "$LOG_FILE"

if [ $RC -ne 0 ] || ! echo "$OUTPUT" | grep -q "OPENMETADATA_TRINO_INGESTION_OK"; then
  log "!! 验证失败,退出码 $RC,上面的输出里应该有具体的 FAIL 原因。"
  log "常见根因排查方向:(1) openmetadata 命名空间的 CronJob/Job 有没有真的建出来"
  log "    kubectl -n openmetadata get cronjob,job"
  log "(2) 如果 Job 存在但一直 Pending/ImagePullBackOff,大概率是"
  log "    docker.getcollate.io/openmetadata/ingestion-base 这个镜像没缓存,首次现拉超时"
  log "(3) 如果 Job Running 但最后 failed,看它的日志"
  log "    kubectl -n openmetadata logs job/<job名>"
  exit 1
fi

log "OK: OpenMetadata 已经能自动发现 Trino 里的真实表结构,不再依赖手动录入。"
