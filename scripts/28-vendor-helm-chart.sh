#!/usr/bin/env bash
# 把一个第三方 Helm chart 原样解包进这个仓库(vendor),让 ArgoCD 从 git 读,
# 不再在同步时去外网拉。
#
# **为什么需要这个,不是洁癖**:2026-08-22 实测,从境内云主机拉
# grafana.github.io / prometheus-community.github.io 的 index.yaml
# (都超过 1.4MB)速度只有约 12KB/s,而 **helm 自己有一个写死的 120 秒
# HTTP 超时**(实测 repo-server 日志 time_ms=120030,而 ArgoCD 的
# ARGOCD_EXEC_TIMEOUT 明明是 180s)——也就是说这个问题**没法靠调大 ArgoCD
# 的超时解决**,helm 会先自己放弃。后果是这些 Application 永远拉不到
# chart,在一个全新集群上根本装不起来。
#
# kube-prometheus-stack 有官方 OCI 仓库可以绕过(OCI 不需要 index),
# 但 Grafana 目前没发 OCI(ghcr.io 下三个候选路径实测全是 403/not found),
# 只能 vendor。
#
# 这也顺带满足"生产可能没有外网"这条一直挂着的顾虑:vendor 进来的 chart
# 不需要任何外网访问就能部署。
#
# 用法:
#   ./scripts/28-vendor-helm-chart.sh <repoURL> <chart> <版本> <目标目录>
# 例:
#   ./scripts/28-vendor-helm-chart.sh https://grafana.github.io/helm-charts alloy 1.11.1 platform/alloy-chart
#
# 升级一个已经 vendor 的 chart = 改版本号重跑这条命令,然后 review diff。
# 目标目录会被**整个重建**(先删后建),不要往里面手动加东西。
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -ne 4 ]; then
  echo "用法: $0 <repoURL> <chart> <版本> <目标目录>" >&2
  exit 1
fi
REPO_URL="$1"; CHART="$2"; VERSION="$3"; DEST="$4"

command -v helm >/dev/null || { echo "找不到 helm" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> helm pull ${CHART} ${VERSION} (来自 ${REPO_URL})"
helm pull "$CHART" --repo "$REPO_URL" --version "$VERSION" --destination "$TMP"
TGZ="${TMP}/${CHART}-${VERSION}.tgz"
[ -f "$TGZ" ] || { echo "没拉到 ${TGZ}" >&2; exit 1; }
DIGEST="$(shasum -a 256 "$TGZ" | awk '{print $1}')"

echo "==> 解包到 ${DEST}(先整个删掉再重建)"
rm -rf "$DEST"
mkdir -p "$DEST"
tar xzf "$TGZ" -C "$DEST" --strip-components=1

cat > "${DEST}/VENDORED.md" <<EOF
# 这是 vendor 进来的第三方 chart,不要手改

| | |
|---|---|
| chart | \`${CHART}\` |
| 版本 | \`${VERSION}\` |
| 上游 | ${REPO_URL} |
| 打包文件 sha256 | \`${DIGEST}\` |
| vendor 时间 | $(date -u +%F) |

**任何本地修改都会在下次升级时被覆盖**——目标目录是整个删掉重建的。要改
行为请改对应 ArgoCD Application 里的 \`helm.valuesObject\`,不要改 chart
模板本身。

升级:

\`\`\`bash
./scripts/28-vendor-helm-chart.sh ${REPO_URL} ${CHART} <新版本> ${DEST}
\`\`\`

为什么要 vendor 而不是让 ArgoCD 直接拉:见
\`scripts/28-vendor-helm-chart.sh\` 顶部注释,以及
[ADR-061](../../docs/decisions/061-vendor-grafana-charts.md)。
EOF

echo "==> 完成。sha256=${DIGEST}"
echo "    文件数:$(find "$DEST" -type f | wc -l | tr -d ' ')"
echo "    记得 git add ${DEST} 并 review diff。"
