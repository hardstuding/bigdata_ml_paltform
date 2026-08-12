#!/usr/bin/env bash
# CloudNativePG 的 CRD 装不进 ArgoCD 管的 apps/definitions/
# cloudnative-pg-operator.yaml——实测确认:11 个 CRD 里 clusters/poolers
# 这两个特别大(clusters 那个内嵌了完整的 PostgreSQL 配置 schema),不管
# 加不加 syncOptions: [ServerSideApply=true] 都报
# "metadata.annotations: Too long: may not be more than 262144 bytes"。
# 和 KServe 的 CRD(ADR-027)看起来是同一类问题,但 KServe 靠
# ServerSideApply=true 就解决了,CNPG 这个更大、ArgoCD 走 Helm chart 的
# `crds/` 目录这条路径似乎不完全遵守这个 sync option(具体是 ArgoCD 的
# 已知限制还是 CNPG 这个 CRD 大小已经超出 server-side apply 本身能处理的
# 范围,没有深挖,不重要——反正 GitOps 这条路走不通,和 KServe 的
# ClusterServingRuntime(scripts/10-install-kserve-serving-runtimes.sh)
# 一样,退回到一次性手动脚本 + `kubectl apply --server-side`)。
#
# apps/definitions/cloudnative-pg-operator.yaml 里 chart 的
# `crds.create` 关掉了,不靠 Helm 装,全部 11 个 CRD 都靠这个脚本从官方
# release 清单里的其中一部分应用(用官方发布的合并清单文件,不是拼凑
# config/crd/bases/ 下面一个个下载——那样版本对不齐的风险更高)。
#
# 幂等:kubectl apply --server-side 本身就是幂等的。
#
# 用法:
#   ./scripts/16-install-cloudnative-pg-crds.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/install-cloudnative-pg-crds.log"
echo "=== install-cloudnative-pg-crds $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

CNPG_VERSION="1.30.0"
TMPFILE=$(mktemp /tmp/cnpg-release-XXXXXX.yaml)
trap 'rm -f "$TMPFILE"' EXIT

echo "==> 下载官方 release 清单(v${CNPG_VERSION})"
curl -fsSL -o "$TMPFILE" \
  "https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/main/releases/cnpg-${CNPG_VERSION}.yaml"

echo "==> 只挑 CustomResourceDefinition 这部分 apply(operator 本身的
Deployment/RBAC 等交给 apps/definitions/cloudnative-pg-operator.yaml 那份
Helm Application 管,不要重复管理同一份资源两次)"
python3 -c "
import sys, yaml
docs = list(yaml.safe_load_all(open('$TMPFILE')))
crds = [d for d in docs if d and d.get('kind') == 'CustomResourceDefinition']
print(f'找到 {len(crds)} 个 CRD', file=sys.stderr)
yaml.dump_all(crds, sys.stdout)
" > "${TMPFILE}.crds-only.yaml"

kubectl apply --server-side --force-conflicts -f "${TMPFILE}.crds-only.yaml" 2>&1 | tee -a "$LOG_FILE"
rm -f "${TMPFILE}.crds-only.yaml"

echo
echo "==> 确认"
kubectl get crd | grep postgresql.cnpg.io

echo
echo "完成。详细日志: ${LOG_FILE}"
