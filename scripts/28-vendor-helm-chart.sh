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
#   ./scripts/28-vendor-helm-chart.sh <repoURL> <chart> <版本> <目标目录> [--exclude-crds <发布命名空间>]
#
# `--exclude-crds <发布命名空间>`:把 chart 渲染出来的 CRD 从 templates/ 里摘出去,单独存成
# 一份纯 YAML(`<目标目录>/crds-out-of-band/crds.yaml`),由 kubectl
# --server-side 单独装,不走 ArgoCD。
#
# **什么时候需要**:CRD 超过 262144 字节(K8s 对 annotation 的硬限制)时,
# ArgoCD 同步会报 `metadata.annotations: Too long`。这个仓库已经在
# kube-prometheus-stack(scripts/04)、CloudNativePG(scripts/16)、
# argo-workflows(scripts/25)上踩过三次,**而且已经确认过 syncOptions 里的
# ServerSideApply=true 解决不了**(理论上 SSA 不写 last-applied 注解,但
# ArgoCD 对 chart 里的 CRD 这条路径不完全遵守——见
# apps/components/cloudnative-pg-operator.yaml 里那段记录)。2026-08-23 装
# Kueue 时第四次撞上(workloads CRD 单个文件 1.4MB),这次不再每个组件写
# 一个一次性脚本,而是把"摘 CRD"这个动作固化进 vendor 流程本身。
#
# 前三次那几个组件没有回头改造成这条路径:它们的 chart 不是 vendor 进来的,
# 改造要先 vendor,超出当时的范围。新增组件优先用这个开关。
# 例:
#   ./scripts/28-vendor-helm-chart.sh https://grafana.github.io/helm-charts alloy 1.11.1 platform/alloy-chart
#
# 升级一个已经 vendor 的 chart = 改版本号重跑这条命令,然后 review diff。
# 目标目录会被**整个重建**(先删后建),不要往里面手动加东西。
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -ne 4 ] && [ $# -ne 6 ]; then
  echo "用法: $0 <repoURL> <chart> <版本> <目标目录> [--exclude-crds <发布命名空间>]" >&2
  exit 1
fi
REPO_URL="$1"; CHART="$2"; VERSION="$3"; DEST="$4"; EXCLUDE_CRDS="${5:-}"; RELEASE_NS="${6:-}"
if [ -n "$EXCLUDE_CRDS" ] && [ "$EXCLUDE_CRDS" != "--exclude-crds" ]; then
  echo "第 5 个参数只能是 --exclude-crds(收到:$EXCLUDE_CRDS)" >&2
  exit 1
fi
# **命名空间必须显式给,不给默认值。** 2026-08-23 实测:第一版没传 -n,
# helm 用了默认的 `default`,渲染出来的 CRD 里 conversion webhook 指向
# `kueue-webhook-service.default.svc` ——CRD 装得上、controller 也 Running,
# 但**任何 ClusterQueue/LocalQueue 对象都建不出来**,报 "service not found"。
# 这类"装完了看着都对、用的时候才炸"正是这个项目最忌讳的失败模式,所以
# 宁可多传一个参数,也不给一个大概率错的默认值。
if [ "$EXCLUDE_CRDS" = "--exclude-crds" ] && [ -z "$RELEASE_NS" ]; then
  echo "--exclude-crds 后面必须跟这个 chart 实际部署到的命名空间" >&2
  exit 1
fi

command -v helm >/dev/null || { echo "找不到 helm" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> helm pull ${CHART} ${VERSION} (来自 ${REPO_URL})"
# OCI 仓库和传统 HTTP 仓库的 pull 语法不一样:OCI 是 `helm pull oci://<仓库>/<chart>`
# 一个完整引用,传 `--repo` 会直接报 "invalid reference"。这里按前缀分流。
# (2026-08-23 vendor kueue 时踩到——之前只 vendor 过 grafana 那种 HTTP 仓库。)
if [ "${REPO_URL#oci://}" != "$REPO_URL" ]; then
  helm pull "${REPO_URL%/}/${CHART}" --version "$VERSION" --destination "$TMP"
else
  helm pull "$CHART" --repo "$REPO_URL" --version "$VERSION" --destination "$TMP"
fi
TGZ="${TMP}/${CHART}-${VERSION}.tgz"
[ -f "$TGZ" ] || { echo "没拉到 ${TGZ}" >&2; exit 1; }
DIGEST="$(shasum -a 256 "$TGZ" | awk '{print $1}')"

echo "==> 解包到 ${DEST}(先整个删掉再重建)"
rm -rf "$DEST"
mkdir -p "$DEST"
tar xzf "$TGZ" -C "$DEST" --strip-components=1

if [ "$EXCLUDE_CRDS" = "--exclude-crds" ]; then
  echo "==> 摘出 CRD:先用 helm template 渲染,再从 templates/ 删掉"
  # 必须先渲染再删:CRD 文件里有 {{- if .Values.xxx }} 这类模板语法,直接
  # 复制原文件出去的话 kubectl 根本 apply 不了。
  #
  # values 用 chart 默认值(CRD 内容不该依赖我们覆盖的资源规格/副本数),
  # **但命名空间必须传对**——CRD 里的 conversion webhook 地址是
  # `{{ .Release.Namespace }}` 渲染出来的,这条真的依赖上下文。
  mkdir -p "${DEST}/crds-out-of-band"
  helm template "$CHART" "$DEST" --namespace "$RELEASE_NS" | python3 -c "
import sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if d and d.get('kind') == 'CustomResourceDefinition']
if not docs:
    sys.exit('!! 传了 --exclude-crds,但这个 chart 渲染不出任何 CRD,参数是不是给错了')
print(f'摘出 {len(docs)} 个 CRD', file=sys.stderr)
yaml.safe_dump_all(docs, sys.stdout)
" > "${DEST}/crds-out-of-band/crds.yaml"
  find "${DEST}/templates" -type d -name 'crd*' -exec rm -rf {} + 2>/dev/null || true
  echo "    -> ${DEST}/crds-out-of-band/crds.yaml($(wc -c < "${DEST}/crds-out-of-band/crds.yaml" | tr -d ' ') 字节)"
fi

cat > "${DEST}/VENDORED.md" <<EOF
# 这是 vendor 进来的第三方 chart,不要手改

| | |
|---|---|
| chart | \`${CHART}\` |
| 版本 | \`${VERSION}\` |
| 上游 | ${REPO_URL} |
| 打包文件 sha256 | \`${DIGEST}\` |
| vendor 时间 | $(date -u +%F) |

${EXCLUDE_CRDS:+**CRD 不在 \`templates/\` 里**,被摘到了 \`crds-out-of-band/crds.yaml\`,
需要用 \`kubectl apply --server-side\` 单独装(ArgoCD 装不了,CRD 超过
262144 字节的 annotation 上限)。原因见 \`scripts/28-vendor-helm-chart.sh\`
顶部 \`--exclude-crds\` 那段。

}**任何本地修改都会在下次升级时被覆盖**——目标目录是整个删掉重建的。要改
行为请改对应 ArgoCD Application 里的 \`helm.valuesObject\`,不要改 chart
模板本身。

升级:

\`\`\`bash
./scripts/28-vendor-helm-chart.sh ${REPO_URL} ${CHART} <新版本> ${DEST} ${EXCLUDE_CRDS} ${RELEASE_NS}
\`\`\`

为什么要 vendor 而不是让 ArgoCD 直接拉:见
\`scripts/28-vendor-helm-chart.sh\` 顶部注释,以及
[ADR-061](../../docs/decisions/061-vendor-grafana-charts.md)。
EOF

echo "==> 完成。sha256=${DIGEST}"
echo "    文件数:$(find "$DEST" -type f | wc -l | tr -d ' ')"
echo "    记得 git add ${DEST} 并 review diff。"
