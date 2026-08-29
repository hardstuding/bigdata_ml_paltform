#!/usr/bin/env bash
# 建 Airflow 的初始管理员账号。没用 chart 自带的 createUserJob,是因为那个只能
# 在 values 里写明文密码(会进公开仓库的 git 历史)。改成这个脚本,从
# airflow-webserver-admin 这个 Secret 读密码,不落地到 git。
#
# 幂等:账号已存在就跳过。
#
# 前置条件:airflow Application 已经 Synced/Healthy(webserver pod 在跑)。
set -euo pipefail

NS="airflow"
# Airflow 3.x 把这个组件从 "webserver" 改名成了 "api-server"。
DEPLOY="deploy/airflow-api-server"

echo "==> 等待 webserver 就绪"
kubectl -n "$NS" rollout status "$DEPLOY" --timeout=180s

USERNAME=$(kubectl -n "$NS" get secret airflow-webserver-admin -o jsonpath='{.data.username}' | base64 -d)
PASSWORD=$(kubectl -n "$NS" get secret airflow-webserver-admin -o jsonpath='{.data.password}' | base64 -d)

if kubectl -n "$NS" exec "$DEPLOY" -- airflow users list 2>/dev/null | grep -q "^${USERNAME} "; then
  echo "用户 ${USERNAME} 已存在,跳过"
else
  kubectl -n "$NS" exec "$DEPLOY" -- airflow users create \
    --role Admin --username "$USERNAME" --password "$PASSWORD" \
    --firstname admin --lastname user --email admin@example.com
  echo "已创建管理员用户 ${USERNAME}"
fi

# ---- 解除定时 DAG 的暂停 ----
# Airflow 默认 `dags_are_paused_at_creation = True`,DAG 第一次被解析出来
# 是暂停的。**给 DAG 加了 schedule 但不解除暂停,等于没加**——2026-08-29
# 给 dbt/feast/seatunnel 三条 DAG 加定时的时候,如果只改 schedule 就收工,
# 表现会是"配置看起来对、日志里什么都没有",又是一次"看起来做完了"。
#
# 只解除**明确设计成定时**的那几条。platform_sdk_demo 保持手动触发——
# 它是给人演示 SDK 用的,自己定时跑没有意义。
#
# 幂等:已经是 unpaused 的再执行一次没有副作用。
SCHEDULED_DAGS="dbt_demo feast_materialize seatunnel_device_events"
echo "==> 解除定时 DAG 的暂停(${SCHEDULED_DAGS})"
SCHEDULER="deploy/airflow-scheduler"
for dag in $SCHEDULED_DAGS; do
  if kubectl -n "$NS" exec "$SCHEDULER" -c scheduler -- airflow dags unpause "$dag" >/dev/null 2>&1; then
    echo "    ${dag} 已解除暂停"
  else
    # DAG 还没被 dag-processor 解析出来时会失败,这不是致命错误——
    # 下次跑这个脚本会补上。但要说出来,不要静默跳过。
    echo "    !! ${dag} 解除暂停失败(多半是 dag-processor 还没解析到它,稍后重跑本脚本)"
  fi
done
