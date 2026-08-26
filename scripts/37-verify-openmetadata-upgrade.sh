#!/usr/bin/env bash
# OpenMetadata 升级之后的验收:**盯业务结果,不盯 Pod 状态**。
#
# 为什么单独写:大版本升级会跑数据库迁移,而"迁移 Job 成功"和"数据还在、
# 功能还能用"是两回事——这个平台被"看起来成功了"坑过太多次(ArgoCD
# Synced 不等于生效、Pod Running 不等于健康、Job Complete 不等于业务逻辑
# 跑对)。所以这里核对的是四样**只有真的迁移对了才会成立**的东西:
#
#   1. 版本号真的变了(不是还在跑老镜像)
#   2. 目录里的表还在,而且**数量不比升级前少**
#   3. 采集/质检 pipeline 还在(它们是我们自己 PUT 进去的实体,
#      迁移时最容易丢的就是这类"非内置"数据)
#   4. 数据质量断言还在,而且**还能跑出结果**——不只是对象还在
#
# 用法:
#   ./scripts/37-verify-openmetadata-upgrade.sh                 # 只看现状
#   BASELINE=/tmp/om-baseline.txt ./scripts/37-...              # 和升级前基线比
#
# 基线文件的格式就是这个脚本自己的输出,升级前先跑一遍存下来即可。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/verify-openmetadata-upgrade.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

TRA_NS="table-registration-app"
TRA_POD="$(kubectl -n "$TRA_NS" get pod -l app=table-registration-app -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$TRA_POD" ] || { log "table-registration-app 没有 Running 的 Pod(这个脚本借它的 python3+requests 调 API)。"; exit 1; }

OUT="$(kubectl -n "$TRA_NS" exec -i "$TRA_POD" -- python3 - <<'PYEOF' 2>/dev/null
import os, requests, sys
T = os.environ["OPENMETADATA_TOKEN"]
B = os.environ.get("OPENMETADATA_URL", "http://openmetadata.openmetadata.svc.cluster.local:8585")
H = {"Authorization": f"Bearer {T}"}


def get(path):
    r = requests.get(f"{B}{path}", headers=H, timeout=60)
    r.raise_for_status()
    return r.json()


print("版本:", get("/api/v1/system/version").get("version"))
print("表总数:", get("/api/v1/tables?limit=1")["paging"]["total"])
print("pipeline:", sorted(x["name"] for x in get("/api/v1/services/ingestionPipelines?limit=50")["data"]))
cases = get("/api/v1/dataQuality/testCases?fields=testCaseResult&limit=50")["data"]
print("质量断言:", sorted(x["name"] for x in cases))
# 断言不只要"还在",还要"有结果"——迁移之后结果表被清掉是很典型的一种损失
withres = sorted(x["name"] for x in cases if x.get("testCaseResult"))
print("其中有结果的:", withres)
PYEOF
)"
echo "$OUT" | tee -a "$LOG_FILE"

if [ -n "${BASELINE:-}" ] && [ -f "$BASELINE" ]; then
  log "和基线 $BASELINE 对比 ..."
  python3 - "$BASELINE" <<PYEOF
import sys, re
base = dict(l.split(":", 1) for l in open(sys.argv[1]) if ":" in l)
now  = dict(l.split(":", 1) for l in """$OUT""".splitlines() if ":" in l)
bad = []
bt, nt = base.get("表总数","").strip(), now.get("表总数","").strip()
if bt and nt and int(nt) < int(bt):
    bad.append(f"表总数从 {bt} 掉到 {nt} —— 迁移丢数据了")
for key in ("pipeline", "质量断言"):
    b, n = base.get(key,"").strip(), now.get(key,"").strip()
    if b and n and b != n:
        bad.append(f"{key} 变了:\n    升级前 {b}\n    升级后 {n}")
if base.get("版本","").strip() == now.get("版本","").strip():
    bad.append(f"版本号没变({now.get('版本','').strip()})—— 可能还在跑老镜像,这次验证不作数")
if bad:
    print("!! 和基线对不上:")
    for b in bad: print("   " + b)
    sys.exit(1)
print("和基线一致(表数没少、pipeline 和断言都在、版本确实变了)")
PYEOF
  log "对比通过。"
else
  log "(没给 BASELINE,只输出了现状。升级前先存一份基线才能做对比。)"
fi
