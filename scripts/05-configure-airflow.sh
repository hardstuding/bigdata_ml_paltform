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
