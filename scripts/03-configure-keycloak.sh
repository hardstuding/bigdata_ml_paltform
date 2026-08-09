#!/usr/bin/env bash
# 建 Keycloak 的 platform realm、ArgoCD/Grafana 的 OIDC client、以及一个初始
# 登录用户。这一步是命令式操作(用 kcadm.sh 敲命令),不在 GitOps 管理范围内
# ——Keycloak 本身没有官方支持的、简单可靠的"用 YAML 声明 realm"方案,
# 见 docs/decisions/009-keycloak-oidc-integration.md。
#
# 幂等:realm/client/user 已存在的会跳过,不会报错也不会覆盖。
#
# 前置条件:scripts/00-generate-secrets.sh 和 scripts/01/02 已经跑过,
# Keycloak 和 ArgoCD 都在正常运行。
#
# 用法:
#   ./scripts/03-configure-keycloak.sh [初始用户名,默认 admin] [初始用户邮箱]
set -euo pipefail

KC_POD="keycloak-keycloakx-0"
KC_NS="keycloak"
INITIAL_USER="${1:-admin}"
INITIAL_EMAIL="${2:-admin@example.com}"

kcadm() {
  kubectl -n "$KC_NS" exec "$KC_POD" -- /opt/keycloak/bin/kcadm.sh "$@"
}

gen_password() {
  openssl rand -base64 24 | tr -d '/+=' | cut -c1-32
}

echo "==> 登录 Keycloak admin CLI"
KC_ADMIN_PW=$(kubectl -n keycloak get secret keycloak-admin -o jsonpath='{.data.password}' | base64 -d)
# 注意 /auth 前缀:codecentric/keycloakx 这个 chart 默认保留了旧版路径约定,
# 见 docs/operations/troubleshooting.md,不是随手加的。
kcadm config credentials --server http://localhost:8080/auth --realm master --user admin --password "$KC_ADMIN_PW"

echo "==> platform realm"
if kcadm get realms/platform >/dev/null 2>&1; then
  echo "已存在,跳过"
else
  kcadm create realms -s realm=platform -s enabled=true -s displayName="Data+AI Platform"
fi

create_client_if_absent() {
  local client_id="$1" redirect_uris="$2" secret_ns="$3" secret_name="$4" secret_key="$5"
  local existing
  existing=$(kcadm get clients -r platform -q clientId="$client_id" --fields id 2>/dev/null | grep -o '"[a-f0-9-]*"' | head -1 | tr -d '"')
  if [ -n "$existing" ]; then
    echo "client ${client_id} 已存在,只同步 redirectUris,不轮换密钥"
    kcadm update "clients/${existing}" -r platform -s "redirectUris=${redirect_uris}"
    return
  fi
  local secret
  secret="$(gen_password)"
  kcadm create clients -r platform \
    -s clientId="$client_id" \
    -s enabled=true \
    -s protocol=openid-connect \
    -s publicClient=false \
    -s secret="$secret" \
    -s "redirectUris=${redirect_uris}" \
    -s standardFlowEnabled=true \
    -s directAccessGrantsEnabled=false
  kubectl -n "$secret_ns" create secret generic "$secret_name" --from-literal="${secret_key}=${secret}" \
    --dry-run=client -o yaml | kubectl apply -f -
  echo "已创建 client ${client_id},密钥写入 ${secret_ns}/${secret_name}"
}

echo "==> argocd client"
# ArgoCD 比较特殊:它只认自己 argocd-secret 里的 key 做变量替换(见
# configs.cm.oidc.config 里的 $oidc.keycloak.clientSecret),不是独立 Secret。
# redirectUris 是 http——argocd-server 现在 server.insecure=true,自己不再起
# TLS,由 ingress-nginx 接 http 明文流量(见 platform/bootstrap/argocd-values.yaml),
# 不是随手改的。
ARGOCD_REDIRECT_URIS='["http://argocd.local-lite.test/auth/callback","http://argocd.local-lite.test/*"]'
argocd_client_id=$(kcadm get clients -r platform -q clientId=argocd --fields id 2>/dev/null | grep -o '"[a-f0-9-]*"' | head -1 | tr -d '"')
if [ -n "$argocd_client_id" ]; then
  echo "client argocd 已存在,跳过创建(只同步 redirectUris,不轮换密钥)"
  kcadm update "clients/${argocd_client_id}" -r platform -s "redirectUris=${ARGOCD_REDIRECT_URIS}"
else
  ARGOCD_SECRET="$(gen_password)"
  kcadm create clients -r platform \
    -s clientId=argocd -s enabled=true -s protocol=openid-connect -s publicClient=false \
    -s secret="$ARGOCD_SECRET" \
    -s "redirectUris=${ARGOCD_REDIRECT_URIS}" \
    -s standardFlowEnabled=true -s directAccessGrantsEnabled=false
  kubectl -n argocd patch secret argocd-secret --type merge \
    -p "{\"stringData\":{\"oidc.keycloak.clientSecret\":\"${ARGOCD_SECRET}\"}}"
  echo "已创建 client argocd,密钥写入 argocd/argocd-secret 的 oidc.keycloak.clientSecret"
fi

echo "==> grafana client"
create_client_if_absent grafana '["http://grafana.local-lite.test/login/generic_oauth"]' monitoring grafana-oidc-secret clientSecret

echo "==> 初始登录用户: ${INITIAL_USER}"
if kcadm get users -r platform -q username="$INITIAL_USER" --fields id 2>/dev/null | grep -q '"id"'; then
  echo "已存在,跳过(不会重置密码)"
else
  USER_PW="$(gen_password | cut -c1-16)"
  kcadm create users -r platform -s username="$INITIAL_USER" -s email="$INITIAL_EMAIL" \
    -s enabled=true -s emailVerified=true
  kcadm set-password -r platform --username "$INITIAL_USER" --new-password "$USER_PW" --temporary=false
  mkdir -p secrets
  echo "keycloak-platform-realm ${INITIAL_USER} / ${USER_PW}" >> secrets/generated-credentials.txt
  echo "已创建用户 ${INITIAL_USER},密码写进 secrets/generated-credentials.txt(不进 git)"
fi

echo
echo "完成。ArgoCD/Grafana 现在应该能看到 Keycloak 登录选项了。"
echo "如果 ArgoCD/kube-prometheus-stack 是先于这一步同步的,需要让它们重新读一次配置:"
echo "  kubectl -n argocd rollout restart deploy/argocd-server"
echo "  kubectl -n argocd patch application kube-prometheus-stack --type merge -p '{\"operation\":{\"initiatedBy\":{\"username\":\"admin\"},\"sync\":{}}}'"
