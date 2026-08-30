#!/usr/bin/env bash
# 让 OpenMetadata 从 **Trino 的查询历史**自动推出表之间的血缘 ——
# roadmap P4「B 线:端到端血缘和变更影响分析」缺的那一半。
#
# **现在血缘只覆盖两条路**:SeaTunnel 那个 DAG(在 DAG 里手动 push)和
# dbt(ADR-082)。而 `jobs/` 里的平台作业、notebook 里的临时查询、Superset
# 的看板查询,读写了哪些表在目录里**完全不可见** —— "改了 orders 这张表会
# 影响谁"这个问题今天答不上来。
#
# **为什么不自己写 SQL 解析**:zhenghe 有基于 sqllineage 自建血缘的实际
# 经验,明确知道那条路的痛点是"需要人工维护、方言升级容易不兼容"。
# OpenMetadata 自带 `DatabaseLineage` 采集器(读 Trino 的
# `system.runtime.queries`、自己解析、自己建边),复用它比自建解析器
# 正确得多 —— 这也是这个仓库一贯的取舍(能复用就不自建)。
#
# **也不用 job.yaml 里声明 inputs/outputs**:声明式血缘的问题是它记录的是
# "作者以为读写了什么",不是"实际读写了什么",两者会分叉而且没人会发现。
# 从查询历史推的是**实际发生过的事**。
#
# ⚠️ **一个必须说清楚的局限**:Trino 的 `system.runtime.queries` 是**内存里
# 的**,只保留最近一段(受 `query.max-history` 限制,默认 100 条),而且
# **coordinator 一重启就全没了**。所以:
#   - 采集周期要比"查询历史被冲掉"快 —— 这里配成每 2 小时一次
#   - coordinator 重启期间的查询,血缘会永久缺失
#   - 真正持久的那份记录在 `iceberg.audit.query_events`(ADR-066),但
#     OpenMetadata 的采集器不认那张表。**要做到"一条不漏"得让采集器读审计
#     表,那是另一件事**,先用官方机制拿到 80% 的价值
#
# 前置条件、认证复用、为什么借 table-registration-app 的 Pod 来调 API ——
# 全部和 scripts/29 一样,不在这里重复,见那个脚本的头部注释。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/configure-openmetadata-trino-lineage.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

OM_NS="openmetadata"
TRA_NS="table-registration-app"

log "检查前置条件 ..."
if ! kubectl -n "$OM_NS" get deploy openmetadata >/dev/null 2>&1; then
  log "openmetadata 还没部署,跳过。"
  exit 0
fi
TRA_POD="$(kubectl -n "$TRA_NS" get pod -l app=table-registration-app -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [ -z "$TRA_POD" ]; then
  log "table-registration-app 没有 Running 的 Pod,跳过(借它的 python3+requests 调 API)。"
  exit 0
fi
if ! kubectl -n "$TRA_NS" get secret table-registration-app-openmetadata >/dev/null 2>&1; then
  log "还没有 OPENMETADATA_TOKEN,先跑 scripts/27-configure-openmetadata-bot.sh。"
  exit 0
fi

PY_SCRIPT="$(mktemp)"
trap 'rm -f "$PY_SCRIPT"' EXIT
cat > "$PY_SCRIPT" <<'PYEOF'
import os

import requests

TOKEN = os.environ["OPENMETADATA_TOKEN"]
BASE = os.environ.get("OPENMETADATA_URL",
                      "http://openmetadata.openmetadata.svc.cluster.local:8585")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def om(method, path, body=None):
    resp = requests.request(method, f"{BASE}{path}", headers=HEADERS, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json() if resp.content else None


# 复用 scripts/29 已经补全过连接配置的那个 "trino" DatabaseService,
# **不新建**。新建一个的话目录里会出现两份 Trino,血缘边挂在哪一份上
# 取决于运气。
svc = om("GET", "/api/v1/services/databaseServices/name/trino")
print(f"database service: {svc['fullyQualifiedName']} (id={svc['id']})")

# `DatabaseLineage` 采集器:读 Trino 的查询历史、自己解析 SQL、自己建边。
#
# queryLogDuration=1:只看最近 1 天的查询。**给大了没用** —— Trino 的
# system.runtime.queries 本来就只有内存里那点历史(见脚本头部的局限说明),
# 给 7 天不会凭空多出记录,只会让每次扫描多做无用功。
#
# resultLimit=1000:一次最多处理 1000 条查询。这台机器的查询量远低于这个数,
# 给这个值是为了防止哪天有人跑了一批批量作业把采集器拖死。
#
# parsingTimeoutLimit=300:单条 SQL 解析超时。默认值偏小,而 dbt 生成的
# SQL 可能很长。
pipeline = om("PUT", "/api/v1/services/ingestionPipelines", {
    "name": "trino_lineage",
    "displayName": "Trino Query Lineage",
    "pipelineType": "lineage",
    "service": {"id": svc["id"], "type": "databaseService"},
    "sourceConfig": {"config": {
        "type": "DatabaseLineage",
        "queryLogDuration": 1,
        "resultLimit": 1000,
        "parsingTimeoutLimit": 300,
    }},
    # 每 2 小时一次。**比 metadata 那条(6 小时)密**,理由不是"血缘更重要",
    # 是 Trino 的查询历史会被冲掉 —— 采集慢于冲掉的速度就等于永久丢失。
    "airflowConfig": {"scheduleInterval": "0 */2 * * *"},
    "loggerLevel": "INFO",
})
print(f"ingestion pipeline ready: {pipeline['fullyQualifiedName']} (id={pipeline['id']})")

om("POST", f"/api/v1/services/ingestionPipelines/deploy/{pipeline['id']}")
print("deploy 请求已提交")
print(f"PIPELINE_ID={pipeline['id']}")
PYEOF

log "PUT IngestionPipeline(trino_lineage,每 2 小时)+ deploy ..."
RESULT="$(kubectl -n "$TRA_NS" exec -i "$TRA_POD" -- python3 - < "$PY_SCRIPT" 2>&1 | tee -a "$LOG_FILE")"

PIPELINE_ID="$(echo "$RESULT" | grep -oE 'PIPELINE_ID=[a-f0-9-]+' | cut -d= -f2 || true)"
if [ -z "$PIPELINE_ID" ]; then
  log "错误:没拿到 pipeline id,上面的输出里有具体报错。"
  log "最常见的原因:trino 这个 DatabaseService 还没被 scripts/29 补全连接配置 —— 先跑那个。"
  exit 1
fi

log "完成。IngestionPipeline id=${PIPELINE_ID}。"
log "把 OpenMetadata 建的 CronJob 的 startingDeadlineSeconds 放大(理由见那个脚本)..."
"$(dirname "$0")/fix-openmetadata-cronjob-deadline.sh" 2>&1 | tee -a "$LOG_FILE" || true

log ""
log "接下来跑 ./scripts/48-verify-trino-lineage.sh —— **deploy 成功不等于真的建出了血缘边**。"
