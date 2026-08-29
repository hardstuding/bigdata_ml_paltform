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
if source.startswith("models:/"):
    # **MLflow 3.x 给的是逻辑地址,不是存储路径。** 2026-08-28 实测:
    # model_version.source = "models:/m-7cb31...",而 KServe 的 storageUri
    # 只认真实的 s3:// 路径。用 logged-models 接口换成 artifact_uri:
    #   GET /api/2.0/mlflow/logged-models/<model_id>
    #   -> artifact_uri = s3://mlflow/1/models/m-.../artifacts
    #
    # 这一步是这次改造里唯一"猜不出来只能实测"的地方——所以第一版写的是
    # 明确报错而不是猜一个转换规则。宁可让它停在这里,也不要部署出一个
    # KServe 认不了的 storageUri、几十秒后才以 Pod 启动失败的形式暴露。
    model_id = source.split("models:/", 1)[1]
    lm = json.load(urllib.request.urlopen(
        f"{B}/api/2.0/mlflow/logged-models/{model_id}", timeout=30))
    info = lm.get("model", lm)
    info = info.get("info", info)
    source = info.get("artifact_uri") or ""

if not source.startswith("s3://"):
    raise SystemExit(f"!! 解析不出 s3:// 地址(拿到的是 {source!r})。"
                     "KServe 的 storageUri 认不了,先别部署。")
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

# ---- 灰度(ADR-080 的"还没做的"第 2 条,2026-08-28 补)----
#
# KServe 原生的做法:更新 InferenceService 的 storageUri 时带上
# `canaryTrafficPercent: N`,它会**保留上一个 revision 继续服务 (100-N)%**,
# 新 revision 拿 N%。也就是说灰度不是"部署两套",是"一次更新 + 一个百分比"。
#
# **为什么做成部署脚本的一个参数,而不是单写一个 43-canary 脚本**:灰度和
# 全量上线走的是同一条部署路径,只差一个字段。拆成两个脚本意味着两份几乎
# 一样的 YAML 生成逻辑,而它们迟早会漂移——这个仓库在"两处源码人工同步"上
# 已经吃过亏(BACKLOG 2.2)。
#
# 用法:
#   CANARY_PERCENT=10 ./scripts/11-deploy-demo-inference-service.sh   # 新版本拿 10%
#   ./scripts/11-deploy-demo-inference-service.sh                     # 全量(默认)
#
# 提升到全量就是不带 CANARY_PERCENT 再跑一次;回退是
# `scripts/42-rollback-model.sh` 切回旧版本之后再跑一次。**灰度期间旧版本
# 还在服务,所以回退不需要重新拉模型**,这正是 canary 比"直接换掉"强的地方。
CANARY_LINE=""
if [ -n "${CANARY_PERCENT:-}" ]; then
  # **先确认这套 KServe 支不支持灰度。** 2026-08-28 实测:这个平台的
  # `defaultDeploymentMode` 是 `Standard`(RawDeployment,见
  # apps/components/kserve-resources.yaml——刻意不装 Knative),而
  # `canaryTrafficPercent` **需要 Knative 的流量切分**。
  #
  # 在 RawDeployment 下,这个字段会被 CRD 老实收下、然后**完全不生效**:
  # 实测带 `canaryTrafficPercent: 10` 部署之后,集群里仍然只有 1 个
  # Deployment、0 个 Revision,新版本**拿走了 100% 流量**。
  #
  # **这正是这个仓库反复吃亏的那种形态**:字段接受了、apply 成功了、
  # 状态是 Ready,而语义完全没实现。所以这里明确拒绝,而不是"配上去看看"
  # ——一个自以为在灰度、实际全量切换的上线,比不做灰度危险得多。
  MODE=$(kubectl -n kserve get cm inferenceservice-config -o jsonpath='{.data.deploy}' 2>/dev/null | tr -d ' \n' || true)
  case "$MODE" in
    *Serverless*) : ;;   # Knative 模式,支持
    *)
      echo "!! 这套 KServe 是 RawDeployment/Standard 模式(没装 Knative),"
      echo "   canaryTrafficPercent **不生效**——带上它的结果是新版本直接拿 100% 流量,"
      echo "   而你以为只放了 ${CANARY_PERCENT}%。拒绝执行。"
      echo "   要真做灰度,见 docs/decisions/080-model-approval-and-rollback.md 的「灰度」一节。"
      exit 1 ;;
  esac
  case "$CANARY_PERCENT" in
    ''|*[!0-9]*) echo "!! CANARY_PERCENT 必须是 0-100 的整数,收到 '${CANARY_PERCENT}'"; exit 1 ;;
  esac
  if [ "$CANARY_PERCENT" -lt 1 ] || [ "$CANARY_PERCENT" -gt 99 ]; then
    # 0 和 100 都不该走灰度这条路:0 等于不部署,100 等于全量(不带这个参数即可)。
    # 明确拒绝而不是"照做",免得有人写 0 以为是"先别放流量"、实际得到一个
    # 谁都不知道是什么状态的服务。
    echo "!! CANARY_PERCENT 要在 1-99 之间。全量就别带这个参数;0 没有意义。"
    exit 1
  fi
  CANARY_LINE="    canaryTrafficPercent: ${CANARY_PERCENT}
"
  echo "=== 灰度模式:新版本拿 ${CANARY_PERCENT}% 流量,旧 revision 继续服务其余部分 ==="
fi

echo "=== 部署 InferenceService ==="
cat <<EOF | kubectl apply -f -
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: demo-rf-classifier
  namespace: ${NS}
  annotations:
    # 让 KServe 在 predictor pod 上打出 prometheus 的抓取注解。
    # **这个注解本身不会让 Prometheus 来抓**(KServe 文档自己写明了),
    # 它只是让 pod 带上 prometheus.io/* 注解;真正来抓的是
    # apps/kserve-inference-monitoring/ 里那个 PodMonitor。两半都要有。
    serving.kserve.io/enable-prometheus-scraping: "true"
spec:
  predictor:
${CANARY_LINE}    serviceAccountName: kserve-minio-sa
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
