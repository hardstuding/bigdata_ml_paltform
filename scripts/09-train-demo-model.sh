#!/usr/bin/env bash
# 端到端 demo(AI/ML 主线,和 scripts/08-create-demo-data.sh 的湖仓核心路径
# 是并列的两条):训练脚本 -> MLflow 记录实验 -> 模型注册。
#
# MLflow 前面挂了 oauth2-proxy 做 Keycloak SSO(见 ADR-019),但那只是挡
# 浏览器访问的 Ingress 入口。训练任务这类服务到服务的场景,直接
# port-forward 到 MLflow/MinIO 的集群内部 Service,不需要经过交互式登录
# ——这两个 Service 本来就没在前面另外加认证层,和 Trino 的服务账号
# (ADR-021)是同一个"人走 SSO、服务到服务走别的路"思路,但更简单。
#
# 依赖(本机 Python 环境,不在这个项目的 image-cache 里管理):
#   pip install mlflow-skinny scikit-learn skops boto3
#
# 前置条件:MLflow 和 MinIO 都在正常运行(MLflow 默认收在
# pending-definitions/,先用 `./scripts/local-lite-toggle-heavy.sh on` 拉
# 回来,或者手动 git mv 到 apps/definitions/)。
#
# 用法:
#   ./scripts/09-train-demo-model.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/train-demo-model.log"
echo "=== train-demo-model $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

if ! kubectl get svc mlflow-mlflow -n mlflow >/dev/null 2>&1; then
  echo "mlflow-mlflow 这个 Service 不存在,MLflow 是不是还没起来?" >&2
  exit 1
fi

MLFLOW_PF_PID=""
MINIO_PF_PID=""
cleanup() {
  [ -n "$MLFLOW_PF_PID" ] && kill "$MLFLOW_PF_PID" 2>/dev/null || true
  [ -n "$MINIO_PF_PID" ] && kill "$MINIO_PF_PID" 2>/dev/null || true
}
trap cleanup EXIT

kubectl port-forward -n mlflow svc/mlflow-mlflow 5000:5000 >> "$LOG_FILE" 2>&1 &
MLFLOW_PF_PID=$!
kubectl port-forward -n minio svc/minio 9000:9000 >> "$LOG_FILE" 2>&1 &
MINIO_PF_PID=$!

echo "等 port-forward 就绪..."
for i in $(seq 1 15); do
  if curl -s -o /dev/null "http://localhost:5000/health" && curl -s -o /dev/null "http://localhost:9000/minio/health/live"; then
    break
  fi
  sleep 1
done

MINIO_USER=$(kubectl -n minio get secret minio-root -o jsonpath='{.data.rootUser}' | base64 -d)
MINIO_PW=$(kubectl -n minio get secret minio-root -o jsonpath='{.data.rootPassword}' | base64 -d)

export AWS_ACCESS_KEY_ID="$MINIO_USER"
export AWS_SECRET_ACCESS_KEY="$MINIO_PW"
export MLFLOW_S3_ENDPOINT_URL="http://localhost:9000"

python3 scripts/train_demo_model.py 2>&1 | tee -a "$LOG_FILE"

echo
echo "验证:查 Model Registry 确认真的注册上了(不是只有客户端打印成功)"
curl -s "http://localhost:5000/api/2.0/mlflow/registered-models/get?name=demo-rf-classifier" | python3 -m json.tool

echo
echo "完成。详细日志: ${LOG_FILE}"
echo "浏览器打开 http://mlflow.local-lite.test/ 看实验和模型(需要先登录)"
