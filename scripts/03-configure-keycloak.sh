#!/usr/bin/env bash
# 这个文件是自动生成的(python3 scripts/render-environment-config.py
# <env>),源头是 templates/scripts/03-configure-keycloak.sh +
# environments/<env>/config.yaml——改动请改模板文件,不要直接改这份
# 生成结果(下次渲染会被覆盖)。见 environments/cloud-full/config.yaml
# 顶部说明背景。
#
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

# 默认 ssoSessionIdleTimeout 只有 30 分钟(Keycloak 默认值)——本地开发/
# 联调这台机器上,人不在电脑前的间隔经常超过 30 分钟,session 过期后每次都
# 要重新走一遍浏览器登录,而且密码类操作没法用脚本代劳(不能替人输入密码,
# 是安全上的硬规则,不是技术做不到)。放宽到 8 小时空闲超时 + 24 小时
# 最大会话长度,减少这种"必须有人在电脑前才能继续调试"的等待。生产环境的
# 会话策略应该由公司自己的安全基线决定,这里的宽松值只适合 local-lite/开发
# 联调场景,cloud-full/prod 部署时应该按公司安全要求重新评估这两个值。
# 【可调参数,见 docs/operations/tuning.md】
kcadm update realms/platform \
  -s ssoSessionIdleTimeout=28800 \
  -s ssoSessionMaxLifespan=86400
echo "已放宽 platform realm 的会话超时(8 小时空闲 / 24 小时最长,仅适合开发环境)"

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
    # 2026-08-19 真实故障(jupyterhub/spark-history-server 都撞到过):
    # 这个分支原来到这里就直接 return,从不检查 k8s Secret 是否真的存在。
    # 如果第一次跑这个脚本时命名空间还不存在(见下面那个分支),client 会
    # 在 Keycloak 里建成功,但 k8s Secret 永远没补上——因为下次重跑,
    # client 已经"存在"了,直接走的是这个分支,根本到不了下面创建 Secret
    # 的代码。真实症状:CreateContainerConfigError,`secret "xxx-oidc-
    # secret" not found`,而且不会自愈,除非手动发现。
    # 补救:client 已存在但 Secret 缺失时,先尝试直接读现有 client secret
    # 的明文(Keycloak Admin API 的 GET .../client-secret 本来就会返回
    # 明文,不需要轮换);读不到才退化成重新生成一个新的。命名空间还没
    # 建好就先跳过,和下面的分支保持一致的容错方式。
    if kubectl get ns "$secret_ns" >/dev/null 2>&1 && ! kubectl -n "$secret_ns" get secret "$secret_name" >/dev/null 2>&1; then
      echo "  !! client 已存在但 ${secret_ns}/${secret_name} 缺失,补一次(会轮换 client secret)"
      local rotated
      rotated=$(kcadm get "clients/${existing}/client-secret" -r platform 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("value",""))' 2>/dev/null || true)
      if [ -z "$rotated" ]; then
        rotated=$(kcadm create "clients/${existing}/client-secret" -r platform -i 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("value",""))' 2>/dev/null || true)
      fi
      if [ -n "$rotated" ]; then
        kubectl -n "$secret_ns" create secret generic "$secret_name" --from-literal="${secret_key}=${rotated}" \
          --dry-run=client -o yaml | kubectl apply -f -
        echo "  已补上 ${secret_ns}/${secret_name}(client secret 已轮换,依赖它的组件需要重启才能生效)"
      else
        echo "  !! 补 Secret 失败(拿不到 client secret 明文),需要人工排查"
      fi
    fi
    return
  fi
  # 命名空间还不存在(组件还 park 着没同步过,和 00-generate-secrets.sh
  # 里同一个跳过逻辑)——先建好 Keycloak client,但暂不写 k8s Secret,
  # 避免 `kubectl apply` 直接报错打断整个脚本(set -euo pipefail)。
  if ! kubectl get ns "$secret_ns" >/dev/null 2>&1; then
    echo "命名空间 ${secret_ns} 还不存在(组件还没 unpark/同步),跳过写 Secret 这步——组件真正部署后重新跑一遍这个脚本补上"
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
# 2026-08-16 cloud-full 真实故障:local-lite 靠 colima 自动转发 80/443,
# 不需要端口;cloud-full 的 ingress-nginx 是 NodePort 32460,所有 client
# 都要注册带端口的回调地址,不然登录跳转回来 404(根因和修法见
# platform/apps/keycloak.yaml 里 KC_HOSTNAME 那段完整说明)。
ARGOCD_REDIRECT_URIS='["http://argocd.local-lite.test/auth/callback","http://argocd.local-lite.test/*","http://argocd.local-lite.test:32460/auth/callback","http://argocd.local-lite.test:32460/*"]'
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
create_client_if_absent grafana '["http://grafana.local-lite.test/login/generic_oauth","http://grafana.local-lite.test:32460/login/generic_oauth"]' monitoring grafana-oidc-secret clientSecret

echo "==> jupyterhub client"
# oauthenticator 的回调路径固定是 /hub/oauth_callback。
create_client_if_absent jupyterhub '["http://jupyterhub.local-lite.test/hub/oauth_callback","http://jupyterhub.local-lite.test:32460/hub/oauth_callback"]' jupyterhub jupyterhub-oidc-secret clientSecret

echo "==> trino client"
# Trino OAuth2 的回调路径固定是 /oauth2/callback,不能改。
# Trino 走 HTTPS(apps/trino-tls/ 手写的 Ingress,8443),NodePort 是
# 32535(https),不是 32460(http)——和其它组件不一样,单独说明一下避免
# 以后照抄错端口。
create_client_if_absent trino '["http://trino.local-lite.test/oauth2/callback","https://trino.local-lite.test:32535/oauth2/callback"]' trino trino-oidc-secret clientSecret

echo "==> superset client"
# Flask-AppBuilder 的 OAuth 回调路径是 /oauth-authorized/<provider name>,
# provider name 是 apps/definitions/superset.yaml 里 OAUTH_PROVIDERS 配的
# "keycloak",不能改一边不改另一边。
create_client_if_absent superset '["http://superset.local-lite.test/oauth-authorized/keycloak","http://superset.local-lite.test:32460/oauth-authorized/keycloak"]' superset superset-oidc-secret clientSecret

echo "==> airflow client"
# 2026-08-19 新增,zhenghe 反馈 Airflow 一直没接 SSO。Airflow 3.x 默认
# auth_manager 还是 FabAuthManager(和 Superset 同一个 Flask-AppBuilder),
# 回调路径同样是 /oauth-authorized/<provider name>,provider name 对应
# apps/definitions/airflow.yaml 里 apiServerConfig 配的 "keycloak"。
create_client_if_absent airflow '["http://airflow.local-lite.test/oauth-authorized/keycloak","http://airflow.local-lite.test:32460/oauth-authorized/keycloak"]' airflow airflow-oidc-secret clientSecret

echo "==> openmetadata client"
# 不能直接用 create_client_if_absent——OpenMetadata chart 的
# oidcConfiguration.clientId 也是 secretRef(不像其他组件那样直接在 values
# 里写字面量 client_id),这个 Secret 要同时装 clientId 和 clientSecret 两个
# key,helper 函数只管一个 key,这里单独写。
OM_REDIRECT_URIS='["http://openmetadata.local-lite.test/callback","http://openmetadata.local-lite.test:32460/callback"]'
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
MLFLOW_REDIRECT_URIS='["http://mlflow.local-lite.test/oauth2/callback","http://mlflow.local-lite.test:32460/oauth2/callback"]'
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

echo "==> spark-history-server client(给挡在前面的 oauth2-proxy 用,和 mlflow 同一个模式,见 ADR-029)"
if kubectl -n spark-operator get secret oauth2-proxy-secret >/dev/null 2>&1; then
  SHS_REDIRECT_URIS='["http://spark-history.local-lite.test/oauth2/callback","http://spark-history.local-lite.test:32460/oauth2/callback"]'
  shs_client_id=$(kcadm get clients -r platform -q clientId=spark-history-server --fields id 2>/dev/null | grep -o '"[a-f0-9-]*"' | head -1 | tr -d '"' || true)
  if [ -n "$shs_client_id" ]; then
    echo "client spark-history-server 已存在,只同步 redirectUris,不轮换密钥"
    kcadm update "clients/${shs_client_id}" -r platform -s "redirectUris=${SHS_REDIRECT_URIS}"
  else
    SHS_OAUTH_SECRET="$(gen_password)"
    kcadm create clients -r platform \
      -s clientId=spark-history-server -s enabled=true -s protocol=openid-connect -s publicClient=false \
      -s secret="$SHS_OAUTH_SECRET" \
      -s "redirectUris=${SHS_REDIRECT_URIS}" \
      -s standardFlowEnabled=true -s directAccessGrantsEnabled=false
    kubectl -n spark-operator patch secret oauth2-proxy-secret --type merge \
      -p "{\"stringData\":{\"client-secret\":\"${SHS_OAUTH_SECRET}\"}}"
    echo "已创建 client spark-history-server,密钥写入 spark-operator/oauth2-proxy-secret 的 client-secret"
  fi
else
  echo "跳过 spark-history-server client(spark-operator/oauth2-proxy-secret 还不存在,Spark Operator 还没启用)"
fi

echo "==> permission-request-app client(给挡在前面的 oauth2-proxy 用,见 ADR-032)"
if kubectl -n permission-request-app get secret oauth2-proxy-secret >/dev/null 2>&1; then
  # 2026-08-16 cloud-full 真实故障:local-lite 靠 colima 自动把 80/443
  # 转发到 127.0.0.1,回调地址不带端口;cloud-full 的 ingress-nginx 是
  # NodePort(32460),回调地址必须带端口,不然登录跳转回来直接 404
  # (对应 apps/definitions/permission-request-app-oauth2-proxy.yaml 那边
  # 同步改过 redirect_url)。两个都注册进去,同一个脚本两边都能用,不需要
  # 按环境分叉。
  PRA_REDIRECT_URIS='["http://permission-request.local-lite.test/oauth2/callback","http://permission-request.local-lite.test:32460/oauth2/callback"]'
  pra_client_id=$(kcadm get clients -r platform -q clientId=permission-request-app --fields id 2>/dev/null | grep -o '"[a-f0-9-]*"' | head -1 | tr -d '"' || true)
  if [ -n "$pra_client_id" ]; then
    echo "client permission-request-app 已存在,只同步 redirectUris,不轮换密钥"
    kcadm update "clients/${pra_client_id}" -r platform -s "redirectUris=${PRA_REDIRECT_URIS}"
  else
    PRA_OAUTH_SECRET="$(gen_password)"
    kcadm create clients -r platform \
      -s clientId=permission-request-app -s enabled=true -s protocol=openid-connect -s publicClient=false \
      -s secret="$PRA_OAUTH_SECRET" \
      -s "redirectUris=${PRA_REDIRECT_URIS}" \
      -s standardFlowEnabled=true -s directAccessGrantsEnabled=false
    kubectl -n permission-request-app patch secret oauth2-proxy-secret --type merge \
      -p "{\"stringData\":{\"client-secret\":\"${PRA_OAUTH_SECRET}\"}}"
    echo "已创建 client permission-request-app,密钥写入 permission-request-app/oauth2-proxy-secret 的 client-secret"
  fi
else
  echo "跳过 permission-request-app client(permission-request-app/oauth2-proxy-secret 还不存在,等这个 Application 先同步一次)"
fi

echo "==> table-registration-app client(给挡在前面的 oauth2-proxy 用,见 ADR-043)"
if kubectl -n table-registration-app get secret oauth2-proxy-secret >/dev/null 2>&1; then
  # 同上一个 client 的注释(cloud-full NodePort 32460 需要带端口的回调地址)。
  TRA_REDIRECT_URIS='["http://table-registration.local-lite.test/oauth2/callback","http://table-registration.local-lite.test:32460/oauth2/callback"]'
  tra_client_id=$(kcadm get clients -r platform -q clientId=table-registration-app --fields id 2>/dev/null | grep -o '"[a-f0-9-]*"' | head -1 | tr -d '"' || true)
  if [ -n "$tra_client_id" ]; then
    echo "client table-registration-app 已存在,只同步 redirectUris,不轮换密钥"
    kcadm update "clients/${tra_client_id}" -r platform -s "redirectUris=${TRA_REDIRECT_URIS}"
  else
    TRA_OAUTH_SECRET="$(gen_password)"
    kcadm create clients -r platform \
      -s clientId=table-registration-app -s enabled=true -s protocol=openid-connect -s publicClient=false \
      -s secret="$TRA_OAUTH_SECRET" \
      -s "redirectUris=${TRA_REDIRECT_URIS}" \
      -s standardFlowEnabled=true -s directAccessGrantsEnabled=false
    kubectl -n table-registration-app patch secret oauth2-proxy-secret --type merge \
      -p "{\"stringData\":{\"client-secret\":\"${TRA_OAUTH_SECRET}\"}}"
    echo "已创建 client table-registration-app,密钥写入 table-registration-app/oauth2-proxy-secret 的 client-secret"
  fi
else
  echo "跳过 table-registration-app client(table-registration-app/oauth2-proxy-secret 还不存在,等这个 Application 先同步一次)"
fi

echo "==> platform-portal client(给挡在前面的 oauth2-proxy 用,见 ADR-047)"
if kubectl -n platform-portal get secret oauth2-proxy-secret >/dev/null 2>&1; then
  # 同上面两个 client 的注释(cloud-full NodePort 32460 需要带端口的回调地址)。
  PORTAL_REDIRECT_URIS='["http://portal.local-lite.test/oauth2/callback","http://portal.local-lite.test:32460/oauth2/callback"]'
  portal_client_id=$(kcadm get clients -r platform -q clientId=platform-portal --fields id 2>/dev/null | grep -o '"[a-f0-9-]*"' | head -1 | tr -d '"' || true)
  if [ -n "$portal_client_id" ]; then
    echo "client platform-portal 已存在,只同步 redirectUris,不轮换密钥"
    kcadm update "clients/${portal_client_id}" -r platform -s "redirectUris=${PORTAL_REDIRECT_URIS}"
  else
    PORTAL_OAUTH_SECRET="$(gen_password)"
    kcadm create clients -r platform \
      -s clientId=platform-portal -s enabled=true -s protocol=openid-connect -s publicClient=false \
      -s secret="$PORTAL_OAUTH_SECRET" \
      -s "redirectUris=${PORTAL_REDIRECT_URIS}" \
      -s standardFlowEnabled=true -s directAccessGrantsEnabled=false
    kubectl -n platform-portal patch secret oauth2-proxy-secret --type merge \
      -p "{\"stringData\":{\"client-secret\":\"${PORTAL_OAUTH_SECRET}\"}}"
    echo "已创建 client platform-portal,密钥写入 platform-portal/oauth2-proxy-secret 的 client-secret"
  fi
else
  echo "跳过 platform-portal client(platform-portal/oauth2-proxy-secret 还不存在,等这个 Application 先同步一次)"
fi

echo "==> argo-workflows client"
# 和 openmetadata 同一个原因不能用 create_client_if_absent——argo-workflows
# chart 的 server.sso.clientId/clientSecret 都是 secretRef(key 名字是
# client-id/client-secret,chart 自己约定的,不能改),这个 Secret 要同时
# 装两个 key。
AW_REDIRECT_URIS='["http://argo-workflows.local-lite.test/oauth2/callback","http://argo-workflows.local-lite.test:32460/oauth2/callback"]'
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

echo "==> groups 这个 client scope(真实故障,2026-08-19 发现):没有任何一个
# client 有 groups 这个 claim mapper,realm 也没有配 defaultDefaultClientScopes。
# 之前 Grafana(ADR-028)/JupyterHub(ADR-025)自称"按 group 收紧已验证"是不
# 准确的——用真实 curl 端到端登录测试(给 MLflow 补 allowed_groups 之后)才
# 发现拿到的 id_token 里根本没有 groups 这个字段(用 python3 解 JWT payload
# 直接确认过,不是猜的),Grafana/JupyterHub 的 allowed_groups/
# role_attribute_path 大概率从来没有真的按组生效过,只是没人拿真实非
# platform-team 账号测过登录,没暴露出来。
#
# 建一个 realm 级别的 "groups" client scope,挂
# oidc-group-membership-mapper(claim 名 "groups",群组路径不带前导 /),
# 设成所有相关 client 的默认 scope(不用每个 app 自己在 scope 参数里额外
# 请求 "groups",和这几个 client 已经在用的 allowed_groups/
# role_attribute_path 配置本身不用改)。"
# 2026-08-19 真实踩坑:client-scopes 这个 list 接口的 `-q name=` 过滤器
# 不生效(实测确认——不是文档写的那种支持按名字查询的接口,`kcadm get
# client-scopes -q name=groups` 会把完整未过滤列表原样返回,`-q` 被静默
# 忽略),用它判断"是否已存在"会拿到列表第一项的 id(这次真实拿到的是
# "roles" 这个内置 scope 的 id),导致误判"已存在"、创建步骤被跳过,
# 后面全部操作都是在挂错的 scope 上,不会报错但完全没有效果。改成拉
# 完整列表用 python3 按 name 字段精确匹配,不依赖这个接口的过滤能力。
groups_scope_id=$(kcadm get client-scopes -r platform 2>/dev/null | python3 -c '
import json, sys
data = json.load(sys.stdin)
for s in data:
    if s["name"] == "groups":
        print(s["id"])
        break
' || true)
if [ -z "$groups_scope_id" ]; then
  kcadm create client-scopes -r platform \
    -s name=groups -s protocol=openid-connect \
    -s 'attributes={"include.in.token.scope":"true","display.on.consent.screen":"false"}'
  echo "已创建 client scope: groups"
  groups_scope_id=$(kcadm get client-scopes -r platform 2>/dev/null | python3 -c '
import json, sys
data = json.load(sys.stdin)
for s in data:
    if s["name"] == "groups":
        print(s["id"])
        break
')
else
  echo "client scope groups 已存在(id=${groups_scope_id}),跳过创建"
fi
existing_mapper=$(kcadm get "client-scopes/${groups_scope_id}/protocol-mappers/models" -r platform 2>/dev/null | python3 -c '
import json, sys
data = json.load(sys.stdin)
for m in data:
    if m["name"] == "groups":
        print(m["id"])
        break
' || true)
if [ -z "$existing_mapper" ]; then
  kcadm create "client-scopes/${groups_scope_id}/protocol-mappers/models" -r platform \
    -s name=groups -s protocol=openid-connect -s protocolMapper=oidc-group-membership-mapper \
    -s 'config={"full.path":"false","id.token.claim":"true","access.token.claim":"true","userinfo.token.claim":"true","claim.name":"groups"}'
  echo "已给 groups scope 挂上 group-membership mapper"
else
  echo "groups scope 的 mapper 已存在,跳过"
fi

# 挂到每一个用 allowed_groups/role_attribute_path 做组权限收拢的 client 上
# (不是全部 12 个 client 都需要——不用 groups 的 client 加了也没坏处,但
# 只列真正用到的,避免让读代码的人以为每个 client 都依赖这个)。2026-08-19
# 补 spark-history-server:第一版漏掉了它,登录测试时才发现它也配了
# allowed_groups(和 mlflow 同一份文件家族写出来的,一开始 grep 检查
# allowed_groups 用法列表时漏看了一处)。同一天再补 argo-workflows:
# server.sso.rbac 的 ServiceAccount 匹配规则(见
# templates/apps-definitions/argo-workflows.yaml)靠 id_token 里的
# groups claim 判断是不是 platform-team 组,不挂这个 scope 的话 groups
# 字段根本不存在,规则永远匹配不上,直接 403。
for gc in grafana jupyterhub mlflow spark-history-server argo-workflows; do
  gcid=$(kcadm get clients -r platform -q clientId="$gc" --fields id 2>/dev/null | grep -o '"[a-f0-9-]*"' | head -1 | tr -d '"' || true)
  if [ -z "$gcid" ]; then
    echo "client ${gc} 还不存在,跳过挂 groups scope"
    continue
  fi
  # PUT 到 default-client-scopes/{scopeId} 是幂等的(已经挂了再挂一次不报错)
  kcadm update "clients/${gcid}/default-client-scopes/${groups_scope_id}" -r platform
  echo "已给 client ${gc} 挂上 groups 默认 scope"
done

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
