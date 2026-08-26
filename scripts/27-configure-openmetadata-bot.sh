#!/usr/bin/env bash
# 让"数据血缘"这条能力在部署阶段就能自动打通,不再依赖人工去 OpenMetadata
# UI 建 bot、生成 JWT、再手动 kubectl create secret(docs/roles.md 里
# "血缘"长期是 ❌ 就卡在这一步)。
#
# 关键发现(2026-08-22 验证过,不是推断):OpenMetadata 在初始化时(见
# openmetadata-create-db 这个 Job/迁移)已经自动建好了一批系统 bot,其中
# ingestion-bot 自带一个 authType=JWT、JWTTokenExpiry=Unlimited 的 token,
# 存在 Postgres 的 user_entity 表里(openmetadata_db 库),值是用
# OpenMetadata 自己的 Fernet key(Secret openmetadata-fernetkey-secret)
# 加密过的。不需要:
#   - 登录 OpenMetadata UI(它走 Keycloak OIDC,没法无人值守登录)
#   - 用 OpenMetadata 自己的 RSA 私钥现签一个新 JWT(更底层、没必要,
#     现成的已经有一个了)
#   - 调用任何需要管理员会话的 REST API
# 只需要:数据库里查出这个字段、用 Fernet key 解密,就是一个立刻可用、
# 永不过期的 bot JWT——已经用 curl 直接打 /api/v1/users/loggedInUser
# 验证过,返回的是 ingestion-bot 这个用户自己的信息,签名和权限都有效。
#
# 复用 ingestion-bot 而不是新建一个专用 bot:这个 token 的角色是
# IngestionBotRole(系统内置,权限比 Admin 窄,但覆盖读写 lineage/table
# 这类 ingestion 场景需要的操作),和"新建一个自定义 bot 再手动授权"比,
# 复用现成的、职责边界本来就是"给自动化流程用"的系统 bot 更简单、攻击面
# 也不会更大。
#
# 幂等:两个目标 Secret 已存在就跳过创建(不轮换),重复跑不报错。
#
# 前置条件:
#   - openmetadata 命名空间的 Deployment 已经 Running(至少跑过一次
#     db-migrations,ingestion-bot 才会被建出来)
#   - data 命名空间的 postgres-cnpg-1 在跑,且能用 postgres-root 这个
#     Secret 的密码连上 openmetadata_db 库
set -euo pipefail

LOG_FILE="logs/configure-openmetadata-bot.log"
mkdir -p logs
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

OM_NS="openmetadata"
PG_NS="data"
PG_POD="postgres-cnpg-1"
PG_DB="openmetadata_db"

TARGETS=(
  "table-registration-app:table-registration-app-openmetadata"
  "permission-request-app:permission-request-app-openmetadata"
  # 2026-08-26(ADR-073):数据质量告警的 CronJob 也要读 OpenMetadata API。
  # 复用同一个 ingestion-bot token,不另发一个——多一个 bot 就多一份要
  # 轮换、要收敛权限的凭据,而这三个消费方需要的读权限完全一样。
  "monitoring:openmetadata-quality-alerts-token"
)

# 幂等短路:两个目标都已存在就直接退出,不用连数据库。
all_exist=true
for t in "${TARGETS[@]}"; do
  ns="${t%%:*}"; name="${t##*:}"
  if ! kubectl -n "$ns" get secret "$name" >/dev/null 2>&1; then
    all_exist=false
  fi
done
if [ "$all_exist" = true ]; then
  log "两个目标 Secret 都已存在,跳过(幂等)。想强制刷新 token,先手动删除对应 Secret 再重跑。"
  exit 0
fi

log "检查前置条件 ..."
if ! kubectl -n "$OM_NS" get deploy openmetadata >/dev/null 2>&1; then
  log "openmetadata 命名空间还没有 Deployment,跳过(组件应该还是 park 状态)。"
  exit 0
fi
if ! kubectl -n "$PG_NS" get pod "$PG_POD" >/dev/null 2>&1; then
  log "data/${PG_POD} 不存在,跳过(postgres-cnpg 应该还是 park 状态)。"
  exit 0
fi

log "等 openmetadata Deployment 就绪(数据库迁移跑完 ingestion-bot 才会存在)..."
kubectl -n "$OM_NS" rollout status deploy/openmetadata --timeout=300s 2>&1 | tee -a "$LOG_FILE"

PG_PASSWORD="$(kubectl -n "$PG_NS" get secret postgres-root -o jsonpath='{.data.password}' | base64 -d)"

log "从 Postgres(${PG_NS}/${PG_POD} 的 ${PG_DB} 库)查询 ingestion-bot 的加密 token ..."
ENCRYPTED_TOKEN="$(kubectl -n "$PG_NS" exec "$PG_POD" -- env PGPASSWORD="$PG_PASSWORD" \
  psql -U postgres -d "$PG_DB" -t -A -c \
  "select json->'authenticationMechanism'->'config'->>'JWTToken' from user_entity where name='ingestion-bot';" \
  2>>"$LOG_FILE" | tr -d '\r\n')"

if [ -z "$ENCRYPTED_TOKEN" ] || [ "$ENCRYPTED_TOKEN" = "" ]; then
  log "错误:查不到 ingestion-bot 的 JWTToken(user_entity 里没有这条记录,或者 authenticationMechanism 为空)。"
  log "可能原因:openmetadata-create-db 这个初始化 Job 还没跑完,或者 OpenMetadata 版本变了、系统 bot 的建立方式不一样了。"
  exit 1
fi

log "拿到加密 token(fernet: 前缀),用 openmetadata-fernetkey-secret 解密 ..."
FERNET_KEY="$(kubectl -n "$OM_NS" get secret openmetadata-fernetkey-secret -o jsonpath='{.data.FERNET_KEY}' | base64 -d)"

JWT_TOKEN="$(python3 - "$ENCRYPTED_TOKEN" "$FERNET_KEY" <<'PYEOF'
import sys
from cryptography.fernet import Fernet

encrypted = sys.argv[1]
if encrypted.startswith("fernet:"):
    encrypted = encrypted[len("fernet:"):]
key = sys.argv[2]
f = Fernet(key)
print(f.decrypt(encrypted.encode()).decode())
PYEOF
)"

if [ -z "$JWT_TOKEN" ]; then
  log "错误:Fernet 解密结果是空的,停止(避免建一个空 token 的 Secret)。"
  exit 1
fi

log "解密成功,校验一下这个 JWT 真的能打通 OpenMetadata API(打 /api/v1/users/loggedInUser)..."
# 不额外拉一个 curlimages/curl 的 Pod 校验(实测在这台云主机上 kubectl run
# --rm 经常卡在镜像拉取/清理上,1 分钟超时都不够)——openmetadata 自己的
# 容器镜像里带了 wget(没有 curl/python3),直接 kubectl exec 进去用 wget
# 打自己的 localhost,不依赖额外调度一个新 Pod。
VERIFY_JSON="$(kubectl -n "$OM_NS" exec deploy/openmetadata -- \
  wget -q -O - --header="Authorization: Bearer ${JWT_TOKEN}" \
  "http://localhost:8585/api/v1/users/loggedInUser" 2>>"$LOG_FILE")" || {
    log "错误:用解密出来的 token 调 API 校验失败,详情见 $LOG_FILE。"
    exit 1
  }
BOT_NAME="$(echo "$VERIFY_JSON" | python3 -c "import json,sys;print(json.load(sys.stdin).get('name'))" 2>/dev/null || echo "?")"
if [ "$BOT_NAME" != "ingestion-bot" ]; then
  log "错误:API 校验返回的登录用户是 [${BOT_NAME}],不是预期的 ingestion-bot,原始返回:${VERIFY_JSON}"
  exit 1
fi
log "校验通过:API 返回的登录用户是 ${BOT_NAME}。"

for t in "${TARGETS[@]}"; do
  ns="${t%%:*}"; name="${t##*:}"
  if kubectl -n "$ns" get secret "$name" >/dev/null 2>&1; then
    log "已存在,跳过: ${ns}/${name}"
    continue
  fi
  if ! kubectl get namespace "$ns" >/dev/null 2>&1; then
    log "跳过: ${ns}/${name}(namespace 还不存在,等对应 Application 先同步一次)"
    continue
  fi
  log "创建 Secret ${ns}/${name} ..."
  kubectl -n "$ns" create secret generic "$name" --from-literal=token="$JWT_TOKEN" 2>&1 | tee -a "$LOG_FILE"
done

log "完成。table-registration-app 和 permission-request-app 的 Deployment 引用的是"
log "optional secretKeyRef,新建的 Secret 不会立刻生效到已经在跑的 Pod 里——"
log "需要 kubectl rollout restart 对应 Deployment,或等它们下次自然重启。"
log "接下来跑 ./scripts/14-configure-airflow-seatunnel-variable.sh,应该会打印"
log "\"已设置 Airflow Variable: openmetadata_token\" 而不是跳过。"
