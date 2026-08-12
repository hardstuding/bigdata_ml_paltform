#!/usr/bin/env bash
# 端到端验证:Spark 通过 Spark Operator 提交作业,读写和 Trino 共用的
# Iceberg 表——见 docs/decisions/036-spark-iceberg-pipeline.md。之前这条
# 链路从来没有真实验证过,Phase 1 的退出标准里写着"留到 Spark Operator
# 真正跑作业时一起验证",这个脚本就是补这个缺口。
#
# 前置条件:
#   - Spark Operator 已启用(apps/definitions/spark-operator.yaml)
#   - scripts/08-create-demo-data.sh 已经跑过(iceberg.demo.orders 有数据)
#
# 这些 manifest 不走 GitOps(和 kserve-demo/Superset demo 是同一类一次性
# 验证资源,见对应 ADR 里的说明),直接 kubectl apply。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/spark-iceberg-demo.log"
echo "=== spark-iceberg-demo $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

if ! kubectl get namespace spark-operator >/dev/null 2>&1; then
  echo "spark-operator 这个 namespace 不存在,Spark Operator 是不是还没启用?" >&2
  exit 1
fi

echo "==> 应用 RBAC + 脚本 ConfigMap"
kubectl apply -f apps/spark-iceberg-demo/manifests/spark-rbac.yaml
kubectl apply -f apps/spark-iceberg-demo/manifests/script-configmap.yaml

echo "==> 删掉旧的(如果有),重新提交"
kubectl delete sparkapplication -n spark-operator spark-iceberg-demo --ignore-not-found=true
kubectl apply -f apps/spark-iceberg-demo/manifests/sparkapplication.yaml

echo "==> 等待作业跑完(COMPLETED 或 FAILED)"
for i in $(seq 1 60); do
  STATE=$(kubectl get sparkapplication -n spark-operator spark-iceberg-demo -o jsonpath='{.status.applicationState.state}' 2>/dev/null || echo "")
  echo "  [$i] state=$STATE"
  if [ "$STATE" = "COMPLETED" ] || [ "$STATE" = "FAILED" ]; then
    break
  fi
  sleep 10
done

echo "==> 最终状态"
kubectl get sparkapplication -n spark-operator spark-iceberg-demo

if [ "$STATE" != "COMPLETED" ]; then
  echo "!! 没有 COMPLETED,打印 driver 日志排查" >&2
  kubectl logs -n spark-operator spark-iceberg-demo-driver 2>&1 | tail -60 || true
  exit 1
fi

echo "==> driver 日志(确认真的读到了 Trino 建的表、写回去了)"
kubectl logs -n spark-operator spark-iceberg-demo-driver 2>&1 | grep -A5 "SPARK_ICEBERG_DEMO_OK\|读到\|写入" || true

echo "完成。详细日志: $LOG_FILE"
