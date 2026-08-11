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
  echo "已存在,跳过创建"
else
  kcadm create realms -s realm=platform -s enabled=true -s displayName="Data+AI Platform"
fi

# 审计日志(见 docs/decisions/024):登录事件和管理员操作事件默认是关闭的,
# 光配置 apps/apps/keycloak.yaml 里那两个 JBOSS_LOGGING 日志级别环境变量
# 没用——那两个只控制"打印成什么级别",这个开关控制"要不要记事件"。
# 每次跑都无条件 update(不是 create-if-absent),保证配置漂移了也能收敛回来。
kcadm update realms/platform \
  -s eventsEnabled=true \
  -s adminEventsEnabled=true \
  -s adminEventsDetailsEnabled=true \
  -s 'eventsListeners=["jboss-logging"]'
echo "已开启 platform realm 的登录事件 + 管理员事件审计日志"

create_client_if_absent() {
  local client_id="$1" redirect_uris="$2" secret_ns="$3" secret_name="$4" secret_key="$5"
  local existing
  # 客户端不存在时 grep 找不到匹配会返回非零,配合 pipefail 会直接把整个脚本
  # 打断(而且不报错,看起来像是卡住了)。用 `|| true` 把"没找到"当成正常情况,
  # 不是失败——这是本来就该有的容错,不是绕过真正的错误。
  existing=$(kcadm get clients -r platform -q clientId="$client_id" --fields id 2>/dev/null | grep -o '"[a-f0-9-]*"' | head -1 | tr -d '"' || true)
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
argocd_client_id=$(kcadm get clients -r platform -q clientId=argocd --fields id 2>/dev/null | grep -o '"[a-f0-9-]*"' | head -1 | tr -d '"' || true)
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

echo "==> jupyterhub client"
# oauthenticator 的回调路径固定是 /hub/oauth_callback。
create_client_if_absent jupyterhub '["http://jupyterhub.local-lite.test/hub/oauth_callback"]' jupyterhub jupyterhub-oidc-secret clientSecret

echo "==> trino client"
# Trino OAuth2 的回调路径固定是 /oauth2/callback,不能改。
create_client_if_absent trino '["http://trino.local-lite.test/oauth2/callback"]' trino trino-oidc-secret clientSecret

echo "==> superset client"
# Flask-AppBuilder 的 OAuth 回调路径是 /oauth-authorized/<provider name>,
# provider name 是 apps/definitions/superset.yaml 里 OAUTH_PROVIDERS 配的
# "keycloak",不能改一边不改另一边。
create_client_if_absent superset '["http://superset.local-lite.test/oauth-authorized/keycloak"]' superset superset-oidc-secret clientSecret

echo "==> openmetadata client"
# 不能直接用 create_client_if_absent——OpenMetadata chart 的
# oidcConfiguration.clientId 也是 secretRef(不像其他组件那样直接在 values
# 里写字面量 client_id),这个 Secret 要同时装 clientId 和 clientSecret 两个
# key,helper 函数只管一个 key,这里单独写。
OM_REDIRECT_URIS='["http://openmetadata.local-lite.test/callback"]'
om_client_id=$(kcadm get clients -r platform -q clientId=openmetadata --fields id 2>/dev/null | grep -o '"[a-f0-9-]*"' | head -1 | tr -d '"' || true)
if [ -n "$om_client_id" ]; then
  echo "client openmetadata 已存在,只同步 redirectUris,不轮换密钥"
  kcadm update "clients/${om_client_id}" -r platform -s "redirectUris=${OM_REDIRECT_URIS}"
else
  OM_SECRET="$(gen_password)"
  kcadm create clients -r platform \
    -s clientId=openmetadata -s enabled=true -s protocol=openid-connect -s publicClient=false \
    -s secret="$OM_SECRET" \
    -s "redirectUris=${OM_REDIRECT_URIS}" \
    -s standardFlowEnabled=true -s directAccessGrantsEnabled=false
  kubectl -n openmetadata create secret generic openmetadata-oidc-secret \
    --from-literal=clientId=openmetadata \
    --from-literal=clientSecret="$OM_SECRET"
  echo "已创建 client openmetadata,密钥写入 openmetadata/openmetadata-oidc-secret"
fi

echo "==> mlflow client(给挡在前面的 oauth2-proxy 用,MLflow 自己不接 OIDC)"
# oauth2-proxy 的回调路径固定是 /oauth2/callback。这里不能用
# create_client_if_absent——密钥要 patch 进 mlflow/oauth2-proxy-secret 的
# client-secret 这个 key,不是建一个新 Secret。
MLFLOW_REDIRECT_URIS='["http://mlflow.local-lite.test/oauth2/callback"]'
mlflow_client_id=$(kcadm get clients -r platform -q clientId=mlflow --fields id 2>/dev/null | grep -o '"[a-f0-9-]*"' | head -1 | tr -d '"' || true)
if [ -n "$mlflow_client_id" ]; then
  echo "client mlflow 已存在,只同步 redirectUris,不轮换密钥"
  kcadm update "clients/${mlflow_client_id}" -r platform -s "redirectUris=${MLFLOW_REDIRECT_URIS}"
else
  MLFLOW_OAUTH_SECRET="$(gen_password)"
  kcadm create clients -r platform \
    -s clientId=mlflow -s enabled=true -s protocol=openid-connect -s publicClient=false \
    -s secret="$MLFLOW_OAUTH_SECRET" \
    -s "redirectUris=${MLFLOW_REDIRECT_URIS}" \
    -s standardFlowEnabled=true -s directAccessGrantsEnabled=false
  kubectl -n mlflow patch secret oauth2-proxy-secret --type merge \
    -p "{\"stringData\":{\"client-secret\":\"${MLFLOW_OAUTH_SECRET}\"}}"
  echo "已创建 client mlflow,密钥写入 mlflow/oauth2-proxy-secret 的 client-secret"
fi

echo "==> argo-workflows client"
# 和 openmetadata 同一个原因不能用 create_client_if_absent——argo-workflows
# chart 的 server.sso.clientId/clientSecret 都是 secretRef(key 名字是
# client-id/client-secret,chart 自己约定的,不能改),这个 Secret 要同时
# 装两个 key。
AW_REDIRECT_URIS='["http://argo-workflows.local-lite.test/oauth2/callback"]'
aw_client_id=$(kcadm get clients -r platform -q clientId=argo-workflows --fields id 2>/dev/null | grep -o '"[a-f0-9-]*"' | head -1 | tr -d '"' || true)
if [ -n "$aw_client_id" ]; then
  echo "client argo-workflows 已存在,只同步 redirectUris,不轮换密钥"
  kcadm update "clients/${aw_client_id}" -r platform -s "redirectUris=${AW_REDIRECT_URIS}"
else
  AW_SECRET="$(gen_password)"
  kcadm create clients -r platform \
    -s clientId=argo-workflows -s enabled=true -s protocol=openid-connect -s publicClient=false \
    -s secret="$AW_SECRET" \
    -s "redirectUris=${AW_REDIRECT_URIS}" \
    -s standardFlowEnabled=true -s directAccessGrantsEnabled=false
  kubectl -n argo-workflows create secret generic argo-workflows-oidc-secret \
    --from-literal=client-id=argo-workflows \
    --from-literal=client-secret="$AW_SECRET"
  echo "已创建 client argo-workflows,密钥写入 argo-workflows/argo-workflows-oidc-secret"
fi

echo "==> 初始登录用户: ${INITIAL_USER}"
if kcadm get users -r platform -q username="$INITIAL_USER" --fields id 2>/dev/null | grep -q '"id"'; then
  echo "已存在,跳过(不会重置密码)"
else
  USER_PW="$(gen_password | cut -c1-16)"
  # firstName/lastName 不是随便填的:Keycloak 的 User Profile 校验把这两个
  # 字段标成必填,账号没填就登录不了(password grant 报 invalid_grant /
  # "Account is not fully set up",错误信息完全看不出跟 first/last name 有
  # 关系)。之前"admin"这个账号没踩到是因为它很早就通过浏览器登录时被要求
  # "更新个人资料"补填过一次,新建账号(比如 scripts/12-sync-iam.py 建的)
  # 不会有这个补救机会,必须一开始就填。
  kcadm create users -r platform -s username="$INITIAL_USER" -s email="$INITIAL_EMAIL" \
    -s firstName="$INITIAL_USER" -s lastName="$INITIAL_USER" \
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
