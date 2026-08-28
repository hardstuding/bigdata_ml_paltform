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
# 连 MLflow 查"哪个版本被批准了"(ADR-080)。原来这个脚本只连 MinIO,
# 因为它是直接翻对象存储挑最新目录的;现在要问注册表,所以多一个 port-forward。
kubectl port-forward -n mlflow svc/mlflow-mlflow 15500:5000 >> "$LOG_FILE" 2>&1 &
MLFLOW_PF=$!
trap 'kill $MLFLOW_PF 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  curl -s --max-time 2 http://127.0.0.1:15500/health >/dev/null 2>&1 && break
  sleep 1
done

MINIO_USER=$(kubectl get secret -n minio minio-root -o jsonpath='{.data.rootUser}' | base64 -d)
MINIO_PASS=$(kubectl get secret -n minio minio-root -o jsonpath='{.data.rootPassword}' | base64 -d)

# **只部署被批准过的那个版本**(ADR-080,2026-08-28)。
#
# 这里原来的做法是"挑 MinIO 里时间戳最新的一个 model 目录"——注释里自己
# 写着那是权宜之计。后果比不优雅严重:没有版本概念 ⇒ 谈不上回滚(出事了
# 不知道切回哪个);没有审批 ⇒ 任何人跑一次训练,产物就自动成了下次上线的
# 那个;甚至可能上线一个失败的或纯实验性的产物,只因为它最新。
#
# 现在改成:认 MLflow 注册表里 `production` 这个 alias 指向的版本。
# alias 由 `scripts/41-approve-model.sh` 在审批时设置,`scripts/42` 回滚时
# 改。**"批准"和"会被部署"是同一个动作的两面**,不会出现"批了没生效"或者
# "没批却上线了"。
MODEL_NAME="${MODEL_NAME:-demo-rf-classifier}"
MODEL_URI=$(python3 - "${MODEL_NAME}" <<'PYEOF'
import json, sys, urllib.error, urllib.request

model = sys.argv[1]
B = "http://127.0.0.1:15500"
try:
    mv = json.load(urllib.request.urlopen(
        f"{B}/api/2.0/mlflow/registered-models/alias?name={model}&alias=production",
        timeout=30))["model_version"]
except urllib.error.HTTPError as e:
    raise SystemExit(
        f"!! {model} 没有 production 这个 alias(HTTP {e.code})。\n"
        f"   **这不是 bug,是审批没做**:先跑\n"
        f"     ./scripts/41-approve-model.sh {model} <版本号>\n"
        f"   批准一个版本之后再来部署。拒绝部署未经批准的模型是有意的。")

tags = {t["key"]: t["value"] for t in mv.get("tags", [])}
if tags.get("approval") != "approved":
    raise SystemExit(f"!! v{mv['version']} 上没有 approval=approved 的标记——"
                     "alias 可能是手工改的,绕过了审批。拒绝部署。")

source = mv.get("source") or ""
if not source.startswith("s3://"):
    # MLflow 3.x 有时给的是 models:/m-<id> 这种逻辑地址,KServe 认不了,
    # 要换成真实的 artifact 路径。
    raise SystemExit(f"!! 版本 source 不是 s3:// 地址而是 {source!r},"
                     "KServe 的 storageUri 认不了。需要在这里补一次转换。")
print(source)
sys.stderr.write(f"   将部署 {model} v{mv['version']}"
                 f"(批准人 {tags.get('approved_by','?')},"
                 f"时间 {tags.get('approved_at','?')})\n")
PYEOF
)
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
