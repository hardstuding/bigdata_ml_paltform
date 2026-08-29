#!/usr/bin/env bash
# 触发一次 dbt 采集,并**独立核实血缘真的建起来了**。
#
# 为什么要单独一个验证脚本:`deploy` 返回 200 不等于采集跑过一次,采集
# Job `Completed` 也不等于血缘真的进了目录。这个仓库被"看起来成功了"坑过
# 太多次(ArgoCD Synced ≠ 生效、Pod Running ≠ 健康、Job Complete ≠ 业务
# 逻辑跑对),所以判定条件写成"能从 OpenMetadata 的血缘接口查到这条边"。
#
# 期望的血缘链(dbt 项目见 apps/dbt-demo/project/models/):
#   iceberg.demo.orders  --(source)-->  stg_orders  --(ref)-->  daily_order_totals
#
# 用法:./scripts/44-verify-openmetadata-dbt-lineage.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/verify-openmetadata-dbt-lineage.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

OM_NS="openmetadata"
TRA_NS="table-registration-app"
CRON="om-cronjob-trino-dbt"
JOB="dbt-verify-$(date +%H%M%S)"

kubectl -n "$OM_NS" get cronjob "$CRON" >/dev/null 2>&1 || {
  log "!! 找不到 CronJob ${CRON}——先跑 ./scripts/43-configure-openmetadata-dbt-ingestion.sh"
  log "   (OpenMetadata 的 CronJob 名字是 om-cronjob-<pipeline name>,pipeline 叫 trino_dbt,"
  log "    名字里的下划线会被转成连字符;如果实际名字不一样,用下面这条查:)"
  kubectl -n "$OM_NS" get cronjob 2>&1 | tee -a "$LOG_FILE"
  exit 1
}

# **顺序有硬依赖**(2026-08-29 实测撞到):dbt 采集是往**已经存在的表实体**
# 上挂血缘边的,表不在目录里就无处可挂——第一次跑时 dbt 采集报
# `Success 100%`,而血缘接口对 daily_order_totals 返回 404,因为元数据采集
# 还没把 dbt 新建的这两张表(stg_orders / daily_order_totals)扫进来。
# 所以这里先跑一次元数据采集,再跑 dbt 采集。
log "==> 先跑一次元数据采集(dbt 新建的表要先进目录,血缘才有地方挂)"
MD_JOB="md-sync-$(date +%H%M%S)"
kubectl -n "$OM_NS" create job --from=cronjob/om-cronjob-trino-metadata "$MD_JOB" >/dev/null
# 轮询 **Job 的 .status.succeeded**,不要轮询 pod 的 .status.phase ——
# 2026-08-29 实测:OpenMetadata 这些采集 pod 在 `kubectl get pods` 里已经
# 显示 `Completed`(容器正常退出),而 `.status.phase` 仍然是 `Running`,
# 用 phase 判定会一直等到超时。Job 那边最终会正确地翻成 succeeded=1。
for i in $(seq 1 60); do
  st="$(kubectl -n "$OM_NS" get job "$MD_JOB" -o jsonpath='{.status.succeeded}/{.status.failed}' 2>/dev/null || echo "/")"
  case "$st" in
    1/*) log "    元数据采集完成"; break ;;
    */[1-9]*) log "!! 元数据采集失败:kubectl -n ${OM_NS} logs job/${MD_JOB}"; exit 1 ;;
    *) [ "$i" = "60" ] && { log "!! 元数据采集 10 分钟没跑完"; exit 1; }
       sleep 10 ;;
  esac
done

log "==> 手工触发一次 dbt 采集(不等下一个整点)"
kubectl -n "$OM_NS" create job --from=cronjob/"$CRON" "$JOB" >/dev/null
log "    Job: ${JOB}"

log "==> 等它跑完(最多 10 分钟)"
for i in $(seq 1 60); do
  st="$(kubectl -n "$OM_NS" get job "$JOB" -o jsonpath='{.status.succeeded}/{.status.failed}' 2>/dev/null || echo "/")"
  case "$st" in
    1/*) log "    (${i}/60)Job 成功"; break ;;
    */1|*/[2-9]) log "!! Job 失败,日志:"; kubectl -n "$OM_NS" logs "job/$JOB" --tail=40 2>&1 | tee -a "$LOG_FILE"; exit 1 ;;
    *)   [ "$i" = "60" ] && { log "!! 10 分钟还没跑完,人工看:kubectl -n ${OM_NS} logs job/${JOB}"; exit 1; }
         sleep 10 ;;
  esac
done

log "==> 直接查血缘接口核实(不只看 Job 状态)"
TRA_POD="$(kubectl -n "$TRA_NS" get pod -l app=table-registration-app -o jsonpath='{.items[0].metadata.name}')"
PY="$(mktemp)"; trap 'rm -f "$PY"' EXIT
cat > "$PY" <<'PYEOF'
import os, sys, json, requests
TOKEN = os.environ["OPENMETADATA_TOKEN"]
BASE = os.environ.get("OPENMETADATA_URL", "http://openmetadata.openmetadata.svc.cluster.local:8585")
H = {"Authorization": f"Bearer {TOKEN}"}

FQN = "trino.iceberg.demo.daily_order_totals"
r = requests.get(f"{BASE}/api/v1/lineage/table/name/{FQN}",
                 params={"upstreamDepth": 3, "downstreamDepth": 0}, headers=H, timeout=30)
if r.status_code == 404:
    print("FAIL 目录里根本没有 daily_order_totals 这张表——dbt 采集没把模型写进目录")
    sys.exit(1)
r.raise_for_status()
d = r.json()
nodes = {n["id"]: n.get("fullyQualifiedName", "?") for n in d.get("nodes", [])}
nodes[d.get("entity", {}).get("id")] = d.get("entity", {}).get("fullyQualifiedName", FQN)
edges = d.get("upstreamEdges", [])
print(f"上游节点 {len(nodes)} 个,上游边 {len(edges)} 条")
for e in edges:
    print(f"  {nodes.get(e['fromEntity'],'?')}  ->  {nodes.get(e['toEntity'],'?')}")
names = set(nodes.values())
want = {"trino.iceberg.demo.stg_orders", "trino.iceberg.demo.orders"}
missing = want - names
if not edges:
    print("FAIL 一条上游边都没有——表进来了但血缘没建")
    sys.exit(1)
if missing:
    print(f"PARTIAL 缺这些上游节点: {sorted(missing)}")
    sys.exit(1)
print("OK dbt 血缘链完整:orders -> stg_orders -> daily_order_totals")
PYEOF
kubectl -n "$TRA_NS" exec -i "$TRA_POD" -- python3 - < "$PY" 2>&1 | tee -a "$LOG_FILE" | tail -12

log "=== 验证结束(上面出现 OK 才算通过)==="
