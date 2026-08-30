#!/usr/bin/env bash
# 验证"从 Trino 查询历史自动推血缘"这条真的建出了边(scripts/47 配的)。
#
# **deploy 返回 200 不等于采集跑过;Job Completed 也不等于血缘进了目录。**
# 这个仓库被"看起来成功了"坑过太多次,所以判定条件是"能从血缘接口查到一条
# 我们刚刚制造出来的边"。
#
# 做法:
#   1. 先用 Trino 真的跑一条 `CREATE TABLE ... AS SELECT`,制造一条**确定
#      存在**的血缘关系(源表 → 新表)。不依赖"最近碰巧有人查过什么"。
#   2. 跑元数据采集(新表要先进目录,血缘才有地方挂 —— 这个顺序依赖
#      2026-08-29 在 dbt 那条上实测撞到过)。
#   3. 跑血缘采集。
#   4. 查血缘接口,确认那条边在。
#   5. 清理临时表。
#
# 用法:./scripts/48-verify-trino-lineage.sh
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/verify-trino-lineage.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

OM_NS="openmetadata"
TRA_NS="table-registration-app"
TBL="lineage_probe_$(date +%s)"

TRA_POD="$(kubectl -n "$TRA_NS" get pod -l app=table-registration-app -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$TRA_POD" ] || { log "!! table-registration-app 没有 Running 的 Pod"; exit 1; }

run_trino() {
  kubectl -n "$TRA_NS" exec -i "$TRA_POD" -- python3 -c "
import os, sys, trino, warnings
warnings.filterwarnings('ignore')
from trino.auth import BasicAuthentication
c = trino.dbapi.connect(host=os.environ['TRINO_HOST'], port=int(os.environ['TRINO_PORT']),
    user=os.environ['TRINO_USER'], http_scheme='https', verify=False,
    auth=BasicAuthentication(os.environ['TRINO_USER'], os.environ['TRINO_PASSWORD']),
    catalog='iceberg')
cur = c.cursor(); cur.execute(sys.argv[1]); print(cur.fetchall()[:3])
" "$1" 2>&1 | tail -2
}

cleanup() {
  log "==> 清理临时表 demo.${TBL}"
  run_trino "DROP TABLE IF EXISTS iceberg.demo.${TBL}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "==> 1/4 用 CTAS 制造一条确定存在的血缘(demo.orders -> demo.${TBL})"
run_trino "CREATE TABLE iceberg.demo.${TBL} AS SELECT region, count(*) AS n FROM iceberg.demo.orders GROUP BY region" \
  | tee -a "$LOG_FILE"

wait_job() {   # $1=job名 $2=描述
  for i in $(seq 1 60); do
    st="$(kubectl -n "$OM_NS" get job "$1" -o jsonpath='{.status.succeeded}/{.status.failed}' 2>/dev/null || echo "/")"
    case "$st" in
      1/*) log "    $2 完成"; return 0 ;;
      */[1-9]*) log "!! $2 失败:kubectl -n ${OM_NS} logs job/$1"; return 1 ;;
      *) [ "$i" = "60" ] && { log "!! $2 十分钟没跑完"; return 1; }; sleep 10 ;;
    esac
  done
}

log "==> 2/4 元数据采集(新表要先进目录,血缘才有地方挂)"
MD="lin-md-$(date +%H%M%S)"
kubectl -n "$OM_NS" create job --from=cronjob/om-cronjob-trino-metadata "$MD" >/dev/null 2>&1 \
  || { log "!! 找不到 om-cronjob-trino-metadata,先跑 scripts/29"; exit 1; }
wait_job "$MD" "元数据采集" || exit 1

log "==> 3/4 血缘采集"
LIN="lin-run-$(date +%H%M%S)"
kubectl -n "$OM_NS" create job --from=cronjob/om-cronjob-trino-lineage "$LIN" >/dev/null 2>&1 \
  || { log "!! 找不到 om-cronjob-trino-lineage,先跑 scripts/47-configure-openmetadata-trino-lineage.sh"; exit 1; }
wait_job "$LIN" "血缘采集" || { kubectl -n "$OM_NS" logs "job/$LIN" --tail=40 2>&1 | tee -a "$LOG_FILE"; exit 1; }

log "==> 4/4 查血缘接口核实(不只看 Job 状态)"
kubectl -n "$TRA_NS" exec -i "$TRA_POD" -- python3 - "$TBL" <<'PYEOF' 2>&1 | tee -a "$LOG_FILE"
import os, sys, requests
TBL = sys.argv[1]
TOKEN = os.environ["OPENMETADATA_TOKEN"]
BASE = os.environ.get("OPENMETADATA_URL", "http://openmetadata.openmetadata.svc.cluster.local:8585")
H = {"Authorization": f"Bearer {TOKEN}"}
FQN = f"trino.iceberg.demo.{TBL}"

r = requests.get(f"{BASE}/api/v1/lineage/table/name/{FQN}",
                 params={"upstreamDepth": 2, "downstreamDepth": 0}, headers=H, timeout=30)
if r.status_code == 404:
    print(f"FAIL 目录里没有 {TBL} —— 元数据采集没扫到它,血缘无从谈起")
    sys.exit(1)
r.raise_for_status()
d = r.json()
names = {n["id"]: n.get("fullyQualifiedName", "?") for n in d.get("nodes", [])}
edges = d.get("upstreamEdges", [])
print(f"上游节点 {len(names)} 个,上游边 {len(edges)} 条")
for e in edges:
    print("   ", names.get(e.get("fromEntity"), e.get("fromEntity")), "->", TBL)
if any("demo.orders" in names.get(e.get("fromEntity"), "") for e in edges):
    print("OK 血缘边存在:demo.orders -> " + TBL + "(从查询历史自动推出来的,没有人工声明)")
else:
    print("FAIL 没有从 demo.orders 指过来的边。")
    print("     最可能的原因:Trino 的 system.runtime.queries 里已经没有那条 CTAS 了")
    print("     (它是内存里的,受 query.max-history 限制,coordinator 重启就清空)。")
    print("     这正是 scripts/47 头部写的那个局限 —— 不是配置错了。")
    sys.exit(1)
PYEOF
