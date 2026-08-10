#!/usr/bin/env bash
# 一次性把 Trino coordinator Deployment 的 livenessProbe 从 chart 硬编码的
# httpGet(打已经关掉的 8080 端口,永远失败,kubelet 会不停强杀重启容器)
# 改成和 readiness/startup 一样的 exec 健康检查脚本。
#
# 为什么不在 values 里配:trino chart 的 deployment-coordinator.yaml 模板
# 把 httpGet 写死了,values 只能覆盖这个探针的几个数字字段(delay/period/
# timeout/threshold),换不了探针类型本身,helm template 验证过。
#
# 为什么脚本化而不是永久手动状态:apps/definitions/trino.yaml 里配了
# spec.ignoreDifferences 让 ArgoCD 不再管这个字段,所以这个 patch 打一次
# 就一直有效(ArgoCD 不会把它覆盖回去),但 Trino 的 Deployment 每次从
# pending-definitions 收进来重新创建时,这个字段又会是 chart 的默认值,
# 需要重新跑一次这个脚本。
#
# 用法:
#   ./scripts/07-fix-trino-liveness-probe.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/fix-trino-liveness-probe.log"
echo "=== fix-trino-liveness-probe $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

if ! kubectl get deploy trino-coordinator -n trino >/dev/null 2>&1; then
  echo "trino-coordinator 这个 Deployment 不存在,Trino 是不是还没起来?" >&2
  exit 1
fi

PATCH='[{"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe","value":{"exec":{"command":["/usr/lib/trino/bin/health-check"]},"initialDelaySeconds":30,"periodSeconds":10,"timeoutSeconds":5,"failureThreshold":6,"successThreshold":1}}]'

kubectl patch deploy trino-coordinator -n trino --type=json -p "$PATCH" | tee -a "$LOG_FILE"

echo "已 patch。等新 pod 起来确认不再被强杀重启:"
echo "  kubectl get pods -n trino -w"
