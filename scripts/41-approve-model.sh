#!/usr/bin/env bash
# 给模型注册表里的某个版本盖"可以上线"的章(ADR-080)。
#
# **为什么需要这一步**(2026-08-28 看 scripts/11 时发现的):
# 现在的上线流程是 `scripts/11` **在 MinIO 里挑时间戳最新的那个模型目录**
# ——注释里自己写着这是权宜之计。后果比"不优雅"严重:
#
#   - **没有版本概念** ⇒ 谈不上回滚:出事了不知道该切回哪一个;
#   - **没有审批** ⇒ 任何人跑一次训练,产物就自动成了"下次要上线的那个";
#   - 甚至可能上线一个**失败的或纯实验性**的训练产物,只因为它最新。
#
# 这个脚本 + `scripts/42`(回滚)+ 改造过的 `scripts/11` 一起,把上线单位
# 从"最新目录"换成"**注册表里某个被明确批准过的版本**"。
#
# 审批记录写在 MLflow 上(不是另起一个库):给 model version 打 tag
# 记下谁批的、什么时候、批注,并把 `production` 这个 alias 指过去。
# **复用已有系统而不是新建一个审批服务**——和 ADR-064 里"队列按已有的组切,
# 不另发明组织结构"是同一个判断。
#
# 用法:
#   ./scripts/41-approve-model.sh <模型名> <版本号> [批注]
#   ./scripts/41-approve-model.sh demo-rf-classifier 1 "离线 AUC 0.92,同意上线"
#
# 谁能批:这个脚本没做身份校验——它需要 kubectl 权限才能 port-forward,
# 而 kubectl 权限本身就是平台管理组才有的(见 platform/iam/)。**这是一个
# 有意的取舍,不是遗漏**:再包一层登录只会多一套要维护的凭据,而真正的边界
# 在 kubectl 那一层。审批人从 `whoami` 取,记进 tag 里可追溯。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/approve-model.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

MODEL="${1:?用法: $0 <模型名> <版本号> [批注]}"
VERSION="${2:?用法: $0 <模型名> <版本号> [批注]}"
NOTE="${3:-}"
APPROVER="$(whoami)"

log "port-forward 到 MLflow ..."
kubectl port-forward -n mlflow svc/mlflow-mlflow 15500:5000 >> "$LOG_FILE" 2>&1 &
PF=$!
trap 'kill $PF 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  curl -s --max-time 2 http://127.0.0.1:15500/health >/dev/null 2>&1 && break
  sleep 1
done

python3 - "$MODEL" "$VERSION" "$APPROVER" "$NOTE" <<'PYEOF' 2>&1 | tee -a "$LOG_FILE"
import json, sys, time, urllib.error, urllib.request

MODEL, VERSION, APPROVER, NOTE = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
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


# 先确认这个版本真的存在。**不确认就打 tag 的话,版本号写错会静默成功**
# ——MLflow 对不存在的版本打 tag 会报错,但错误信息不直白,不如自己先查。
mv = api(f"/model-versions/get?name={MODEL}&version={VERSION}")["model_version"]
print(f"  版本 {MODEL} v{VERSION}:status={mv.get('status')} "
      f"source={mv.get('source','')[:60]}")
if mv.get("status") != "READY":
    raise SystemExit(f"!! 这个版本的状态是 {mv.get('status')},不是 READY——"
                     "产物可能没上传完,先别批。")

stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
for key, value in (("approval", "approved"), ("approved_by", APPROVER),
                   ("approved_at", stamp), ("approval_note", NOTE)):
    api("/model-versions/set-tag",
        {"name": MODEL, "version": VERSION, "key": key, "value": value})
print(f"  审批记录已写进 MLflow:approved_by={APPROVER} approved_at={stamp}")

# alias 指向它。`scripts/11` 上线时只认这个 alias —— 也就是说"批准"和
# "会被部署"是同一个动作的两面,不会出现"批了但没生效"或者"没批却上线了"。
api("/registered-models/alias", {"name": MODEL, "alias": "production", "version": VERSION})
print(f"  alias production -> {MODEL} v{VERSION}")
print(f"APPROVED {MODEL} {VERSION}")
PYEOF

log "完成。现在跑 ./scripts/11-deploy-demo-inference-service.sh 会部署这个版本。"
log "要回滚:./scripts/42-rollback-model.sh ${MODEL}"
