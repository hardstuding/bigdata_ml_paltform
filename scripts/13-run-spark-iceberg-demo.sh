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
# 2026-08-21:原本这里还 apply 一个 spark-rbac.yaml,但那个文件早在
# 2026-08-12 的 commit a7f2833 就删掉了(改用 spark-operator chart 自带的
# spark-operator-spark ServiceAccount),脚本这行没跟着删——**这个脚本从那天
# 起就是坏的,第一步就退出**,而 docs/roles.md 里'批处理引擎 ✅'一直挂着。
# 又一个'部署了 ≠ 能用'的例子。
kubectl apply -f apps/spark-iceberg-demo/manifests/script-configmap.yaml

echo "==> 删掉旧的(如果有),重新提交"
kubectl delete sparkapplication -n spark-operator spark-iceberg-demo --ignore-not-found=true
# 2026-08-21:光删 SparkApplication 不够,要等 driver pod 真的消失。
# 实测踩到:删完立刻重新提交,operator 报 "driver pod already exist" 直接把
# 新作业判 FAILED,然后又去杀那个其实正在正常拉镜像的 driver——表现成
# "提交了、几秒就失败了、日志还没来得及产生",很容易误判成镜像或作业本身
# 有问题,实际是重复提交的竞态。
echo "==> 等旧 driver pod 退干净(最多 60 秒)"
for _ in $(seq 1 60); do
  kubectl get pod -n spark-operator spark-iceberg-demo-driver >/dev/null 2>&1 || break
  sleep 1
done
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
