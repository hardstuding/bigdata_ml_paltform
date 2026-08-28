#!/usr/bin/env bash
# 把线上模型切回**上一个已批准的版本**(ADR-080)。
#
# 回滚要成立有个前提:**得知道"上一个"是哪一个**。这正是 scripts/41 之前
# 做不到的事——那时候上线的是"MinIO 里时间戳最新的目录",没有版本序列,
# 也就没有"上一个"。有了审批 tag 之后,"所有 approval=approved 的版本按
# 版本号倒序"就是一条明确的回滚路径。
#
# 用法:
#   ./scripts/42-rollback-model.sh <模型名>            # 回退一格
#   ./scripts/42-rollback-model.sh <模型名> <版本号>   # 回到指定版本
#
# **这个脚本只改 alias,不自己重新部署。** 故意的:部署会重建
# InferenceService、有几十秒不可用,该由人在确认要回滚之后显式触发
# (脚本最后会打印那条命令)。把"决定回滚"和"执行重启"分开,是为了避免
# 手滑跑一下就把线上服务重启了。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/rollback-model.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

MODEL="${1:?用法: $0 <模型名> [版本号]}"
TARGET="${2:-}"

kubectl port-forward -n mlflow svc/mlflow-mlflow 15500:5000 >> "$LOG_FILE" 2>&1 &
PF=$!
trap 'kill $PF 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  curl -s --max-time 2 http://127.0.0.1:15500/health >/dev/null 2>&1 && break
  sleep 1
done

python3 - "$MODEL" "$TARGET" <<'PYEOF' 2>&1 | tee -a "$LOG_FILE"
import json, sys, urllib.error, urllib.request

MODEL, TARGET = sys.argv[1], sys.argv[2]
B = "http://127.0.0.1:15500"


def api(path, body=None):
    req = urllib.request.Request(
        f"{B}/api/2.0/mlflow{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET")
    try:
        return json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"!! {path} 失败(HTTP {e.code}): {e.read().decode()[:200]}")


import urllib.parse
q = urllib.parse.quote(f"name='{MODEL}'")
versions = api(f"/model-versions/search?filter={q}&max_results=200").get("model_versions", [])
approved = sorted(
    (v for v in versions
     if any(t.get("key") == "approval" and t.get("value") == "approved"
            for t in v.get("tags", []))),
    key=lambda v: int(v["version"]), reverse=True)

if not approved:
    raise SystemExit(f"!! {MODEL} 一个被批准过的版本都没有——"
                     "先用 scripts/41-approve-model.sh 批一个。回滚不能回到没批准过的版本。")

print(f"  已批准的版本(倒序):{[v['version'] for v in approved]}")
try:
    current = api(f"/registered-models/alias?name={MODEL}&alias=production")["model_version"]["version"]
except SystemExit:
    current = None
print(f"  当前 production 指向:{current or '(没有)'}")

if TARGET:
    if TARGET not in [v["version"] for v in approved]:
        raise SystemExit(f"!! v{TARGET} 不在已批准列表里,不能回滚过去。"
                         "**回滚只能回到批准过的版本**,否则等于绕过审批。")
    target = TARGET
else:
    older = [v["version"] for v in approved if current is None or int(v["version"]) < int(current)]
    if not older:
        raise SystemExit(f"!! 当前已经是最老的已批准版本(v{current}),没有可回退的了。")
    target = older[0]

api("/registered-models/alias", {"name": MODEL, "alias": "production", "version": target})
print(f"  alias production:{current or '(无)'} -> {target}")
print(f"ROLLED_BACK {MODEL} {target}")
PYEOF

log "alias 已切。**服务还没变**——确认之后执行下面这条才会真正生效:"
log "    ./scripts/11-deploy-demo-inference-service.sh"
