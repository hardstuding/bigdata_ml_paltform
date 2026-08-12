#!/usr/bin/env bash
# 给 DAG(apps/airflow/dags/seatunnel_device_events.py)提供连 MinIO 要用的
# 凭据。没有把凭据直接复制成 Secret 挂进 Airflow 的 pod(那是给"pod 启动时
# 就要用"的场景用的,比如 apps/spark-iceberg-demo),这里凭据只在 DAG 任务
# 执行时、拼 SeaTunnel job JSON 请求体那一刻才用得到,更适合用 Airflow
# 自己的 Variable 机制(存在 Airflow 元数据库里,Fernet key 加密,和
# scripts/05-configure-airflow.sh 建管理员账号是同一个"用 airflow CLI 走
# kubectl exec"的思路)。
#
# 幂等:变量已存在就覆盖(凭据可能轮换),不报错。
#
# 前置条件:airflow Application 已经 Synced/Healthy。
set -euo pipefail

NS="airflow"
DEPLOY="deploy/airflow-api-server"

echo "==> 等待 webserver 就绪"
kubectl -n "$NS" rollout status "$DEPLOY" --timeout=180s

MINIO_KEY=$(kubectl -n minio get secret minio-root -o jsonpath='{.data.rootUser}' | base64 -d)
MINIO_SECRET=$(kubectl -n minio get secret minio-root -o jsonpath='{.data.rootPassword}' | base64 -d)

kubectl -n "$NS" exec "$DEPLOY" -- airflow variables set minio_access_key "$MINIO_KEY" >/dev/null
kubectl -n "$NS" exec "$DEPLOY" -- airflow variables set minio_secret_key "$MINIO_SECRET" >/dev/null
echo "已设置 Airflow Variable: minio_access_key / minio_secret_key"
