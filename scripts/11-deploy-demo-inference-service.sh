#!/usr/bin/env bash
# 端到端 demo(AI/ML 主线)收尾:把已经在 scripts/09-train-demo-model.sh 里
# 训练并注册到 MLflow Model Registry 的 demo-rf-classifier 模型,通过
# KServe 部署成一个真实的 InferenceService,闭环"训练 -> 注册 -> 上线推理"。
#
# 模型用的是 MLflow 3.x 默认的 skops 序列化(不是老式 pickle),KServe 的
# sklearn runtime(kserve-sklearnserver)只认 model.joblib,不认 MLflow 的
# 目录结构;能直接吃 MLflow 模型目录(MLmodel + model.skops + ...)的是
# mlserver runtime 的 mlflow 格式支持(modelFormat.name: mlflow,mlserver
# 内部就是调 mlflow.pyfunc.load_model,原生认 skops)。
#
# storageUri 直接指到 MinIO 里 MLflow 存模型的路径(s3://mlflow/2/models/
# <model_id>/artifacts),不经过 MLflow 服务本身——MLflow tracking server
# 这会儿是 park 状态(见 environments/cloud-full/pending-definitions/
# mlflow.yaml),但 KServe 推理不需要它,只需要 MinIO 里的模型文件,这和
# ADR-023 里"训练任务直连 MinIO Service,不走 MLflow/oauth2-proxy"是同一个
# 道理。
#
# 部署到独立的 kserve-demo namespace(不是 kserve 本身那个 namespace,那个
# 是给 controller 用的),不通过 ArgoCD 管理——这是一次性验证用的 demo
# 资源,不是平台基础设施,和 scripts/08-create-demo-data.sh 建的 Superset
# demo dashboard 是同一类东西。

set -euo pipefail

LOG_FILE="/tmp/kserve-demo-deploy.log"
exec > >(tee -a "$LOG_FILE") 2>&1

NS="kserve-demo"
MINIO_USER=$(kubectl get secret -n minio minio-root -o jsonpath='{.data.rootUser}' | base64 -d)
MINIO_PASS=$(kubectl get secret -n minio minio-root -o jsonpath='{.data.rootPassword}' | base64 -d)

# 挑 MinIO 里时间戳最新的一个 model 目录(scripts/09 可能被重复跑过,mlflow
# 3.x 的 model registry 用 model_id 而不是老式 run_id/artifacts/model 路径)。
MODEL_URI=$(python3 -c "
import boto3
s3 = boto3.client('s3', endpoint_url='http://localhost:9000', aws_access_key_id='${MINIO_USER}', aws_secret_access_key='${MINIO_PASS}')
paginator = s3.get_paginator('list_objects_v2')
latest = None
for page in paginator.paginate(Bucket='mlflow', Prefix='2/models/'):
    for obj in page.get('Contents', []):
        if obj['Key'].endswith('/MLmodel'):
            if latest is None or obj['LastModified'] > latest[0]:
                latest = (obj['LastModified'], obj['Key'])
prefix = latest[1].rsplit('/', 1)[0]
print(f's3://mlflow/{prefix}')
")
echo "MODEL_URI=${MODEL_URI}"

echo "=== 建 namespace / S3 凭据 / ServiceAccount ==="
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: kserve-minio-s3-creds
  namespace: ${NS}
  annotations:
    serving.kserve.io/s3-endpoint: minio.minio.svc.cluster.local:9000
    serving.kserve.io/s3-usehttps: "0"
    serving.kserve.io/s3-region: us-east-1
    serving.kserve.io/s3-verifyssl: "0"
type: Opaque
stringData:
  AWS_ACCESS_KEY_ID: "${MINIO_USER}"
  AWS_SECRET_ACCESS_KEY: "${MINIO_PASS}"
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kserve-minio-sa
  namespace: ${NS}
secrets:
  - name: kserve-minio-s3-creds
EOF

echo "=== 部署 InferenceService ==="
cat <<EOF | kubectl apply -f -
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: demo-rf-classifier
  namespace: ${NS}
spec:
  predictor:
    serviceAccountName: kserve-minio-sa
    model:
      modelFormat:
        name: mlflow
      protocolVersion: v2
      storageUri: "${MODEL_URI}"
      resources:
        requests:
          cpu: 500m
          memory: 1Gi
        limits:
          cpu: "1"
          memory: 2Gi
EOF

echo "=== 等待 InferenceService Ready ==="
kubectl wait --for=condition=Ready --timeout=300s inferenceservice/demo-rf-classifier -n "$NS" || {
  echo "!!! 没在超时内 Ready,打印诊断信息 !!!"
  kubectl get inferenceservice -n "$NS" demo-rf-classifier -o yaml
  kubectl get pods -n "$NS"
  exit 1
}

kubectl get inferenceservice -n "$NS"
kubectl get pods -n "$NS"

echo "=== 完成 $(date) ==="
