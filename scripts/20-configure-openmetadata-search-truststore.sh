#!/usr/bin/env bash
# OpenMetadata 用 https 连 OpenSearch 时,OpenSearch 官方镜像自带的是
# 自签名 demo 证书(root-ca.pem,打包在镜像里,同一个镜像版本每次启动都
# 一样,不是运行时随机生成的),OpenMetadata 的 Java 客户端默认不信任它,
# 报 PKIX path building failed。
#
# 这个脚本从运行中的 OpenSearch pod 里把 root-ca.pem 导出、做成一个 Java
# truststore(JKS),存进 Secret,给 OpenMetadata 通过
# elasticsearch.trustStore + extraVolumes 挂载信任。
#
# 之所以退回命令式脚本、不走 GitOps 声明:truststore 是从运行中容器里
# 提取的二进制内容,GitOps 声明不了,和 scripts/03-configure-keycloak.sh
# 这类"建 Secret/导出凭据"的操作是同一类模式。
#
# 幂等:Secret 已存在时不重新生成(避免不必要的 openmetadata 重启)。
# 想强制重新生成,先手动删除 openmetadata-search-truststore 这个 Secret。
#
# 前置条件:opensearch-cluster-master-0 这个 pod 必须是 Running(先跑
# apps/definitions/opensearch.yaml 对应的 Application 并等它 Healthy)。
set -euo pipefail

LOG_FILE="logs/configure-openmetadata-search-truststore.log"
mkdir -p logs
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

NAMESPACE="openmetadata"
TRUSTSTORE_SECRET="openmetadata-search-truststore"
PASSWORD_SECRET="openmetadata-search-truststore-password"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

if kubectl get secret "$TRUSTSTORE_SECRET" -n "$NAMESPACE" >/dev/null 2>&1; then
  log "Secret $TRUSTSTORE_SECRET 已存在,跳过(幂等)。想强制重新生成,先手动删除这个 Secret。"
  exit 0
fi

# 这台 Mac 上没有真正的本地 JRE(/usr/bin/keytool 只是触发安装弹窗的
# 桩程序),改成在 opensearch 容器自己带的 JDK 里生成 truststore(容器里
# 确认过有 /usr/share/opensearch/jdk/bin/keytool),再把生成的二进制文件
# 通过 base64 传出来,不依赖 kubectl cp(容器镜像里没有 tar 会导致
# kubectl cp 失败)。

TRUSTSTORE_PASSWORD="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"
REMOTE_JKS="/tmp/openmetadata-truststore.jks"

log "在 opensearch 容器内生成 JKS truststore ..."
kubectl exec -n "$NAMESPACE" opensearch-cluster-master-0 -- \
  /usr/share/opensearch/jdk/bin/keytool -importcert -noprompt \
    -alias opensearch-demo-ca \
    -file /usr/share/opensearch/config/root-ca.pem \
    -keystore "$REMOTE_JKS" \
    -storepass "$TRUSTSTORE_PASSWORD" 2>&1 | tee -a "$LOG_FILE"

log "把生成的 truststore 传出到本地 ..."
kubectl exec -n "$NAMESPACE" opensearch-cluster-master-0 -- \
  sh -c "base64 < $REMOTE_JKS" | base64 -d > "$WORKDIR/truststore.jks"
kubectl exec -n "$NAMESPACE" opensearch-cluster-master-0 -- rm -f "$REMOTE_JKS" 2>&1 | tee -a "$LOG_FILE"

log "创建 Secret $TRUSTSTORE_SECRET / $PASSWORD_SECRET ..."
kubectl create secret generic "$TRUSTSTORE_SECRET" -n "$NAMESPACE" \
  --from-file=truststore.jks="$WORKDIR/truststore.jks" 2>&1 | tee -a "$LOG_FILE"
kubectl create secret generic "$PASSWORD_SECRET" -n "$NAMESPACE" \
  --from-literal=password="$TRUSTSTORE_PASSWORD" 2>&1 | tee -a "$LOG_FILE"

log "完成。接下来需要 apps/definitions/openmetadata.yaml 里的
  elasticsearch.trustStore.enabled=true + extraVolumes/extraVolumeMounts
  挂载这个 Secret(见对应 commit),ArgoCD 同步后重启 openmetadata
  Deployment 才会生效。"
