#!/usr/bin/env bash
# 配 OpenBao 的认证方式、KV 引擎和策略(ADR-089)。**幂等**,可重复跑。
#
# 前置:scripts/49-init-unseal-openbao.sh 跑过(OpenBao 已初始化并解封)。
#
# 配三样:
#
#   1. KV v2 引擎挂在 secret/
#   2. Kubernetes auth —— 给 Pod 用(定时作业、notebook 服务端)。Pod 拿
#      自己的 ServiceAccount token 换 OpenBao token,**不需要分发任何密钥**。
#   3. OIDC auth(Keycloak)—— 给人用。和平台其余工具同一个身份源。
#
# **隔离由 OpenBao 强制,不靠调用方自觉。** 路径策略用 OpenBao 的模板语法
# 让"一个人只能碰自己的路径"由 OpenBao 自己判断,而不是我们在 SDK 或门户
# 里写 if。理由和 ADR-051 把表级授权交给 OPA 是同一条:**写在调用方的检查,
# 绕过调用方就没了**。
set -euo pipefail

NS="${OPENBAO_NAMESPACE:-openbao}"
POD="${OPENBAO_POD:-openbao-0}"
KEYS_SECRET="${OPENBAO_KEYS_SECRET:-openbao-unseal-keys}"
OPENBAO_EXTERNAL_URL="${OPENBAO_EXTERNAL_URL:-http://openbao.local-lite.test:32460}"
KEYCLOAK_ISSUER="${KEYCLOAK_ISSUER:-http://keycloak-keycloakx-http.keycloak.svc.cluster.local/auth/realms/platform}"

mkdir -p logs
LOG_FILE="logs/50-openbao-auth-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== 配 OpenBao 认证/策略 $(date -u +%FT%TZ) ==="

ROOT_TOKEN="$(kubectl -n "$NS" get secret "$KEYS_SECRET" -o jsonpath='{.data.root_token}' | base64 -d)"
[ -n "$ROOT_TOKEN" ] || { echo "!! 读不到 root token,先跑 scripts/49-init-unseal-openbao.sh"; exit 1; }

# 所有 bao 命令都带 root token。**token 只经环境变量传进容器,不出现在
# 命令行参数里** —— 命令行参数会出现在 `ps` 和审计日志里。
bao() { kubectl -n "$NS" exec -i "$POD" --env="BAO_TOKEN=$ROOT_TOKEN" -- bao "$@"; }

sealed=$(bao status -format=json 2>/dev/null | python3 -c 'import json,sys
try: print(str(json.load(sys.stdin)["sealed"]).lower())
except Exception: print("unknown")')
[ "$sealed" = "false" ] || { echo "!! OpenBao 还是封印状态(sealed=$sealed),先跑 scripts/49"; exit 1; }

echo "==> 1/7 KV v2 引擎"
if bao secrets list -format=json 2>/dev/null | grep -q '"secret/"'; then
  echo "  secret/ 已挂载,跳过"
else
  bao secrets enable -path=secret -version=2 kv
  echo "  已挂载 secret/(KV v2)"
fi

echo "==> 2/7 Kubernetes auth(给 Pod 用)"
if bao auth list -format=json 2>/dev/null | grep -q '"kubernetes/"'; then
  echo "  已启用,跳过"
else
  bao auth enable kubernetes
fi
# **不写死 API server 地址和 CA。** OpenBao 跑在集群里,用自己的 SA token
# 去问 API server 就够(disable_local_ca_jwt=false 是默认)。写死地址的话
# 集群重建/换 IP 之后这里会静默失效。
bao write auth/kubernetes/config \
  kubernetes_host="https://\$KUBERNETES_PORT_443_TCP_ADDR:443" >/dev/null
echo "  已配 kubernetes auth"

echo "==> 3/7 策略(accessor 收集)"
# ---- 用户自己的凭据 ----
#
# `{{identity.entity.aliases.<mount_accessor>.name}}` 是 OpenBao 的策略
# 模板:它在**鉴权时**展开成当前登录者的用户名。所以同一条策略对所有人
# 生效,而每个人只能碰自己那一段路径 —— 这是 OpenBao 自己保证的,不是
# 我们的代码保证的。
OIDC_ACCESSOR=""
if bao auth list -format=json 2>/dev/null | grep -q '"oidc/"'; then
  OIDC_ACCESSOR=$(bao auth list -format=json | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(d.get("oidc/", {}).get("accessor", ""))')
fi

echo "==> 4/7 OIDC auth(给人用,身份源是 Keycloak)"
if bao auth list -format=json 2>/dev/null | grep -q '"oidc/"'; then
  echo "  已启用,跳过 enable"
else
  bao auth enable oidc
fi
OIDC_CLIENT_SECRET="$(kubectl -n "$NS" get secret openbao-oidc-secret -o jsonpath='{.data.clientSecret}' 2>/dev/null | base64 -d || true)"
if [ -z "$OIDC_CLIENT_SECRET" ]; then
  echo "  !! 读不到 openbao/openbao-oidc-secret —— 先跑 scripts/03-configure-keycloak.sh"
  echo "     (它会建 openbao 这个 Keycloak client 并把密钥写进那个 Secret)"
  echo "     OIDC 这一段跳过,kubernetes auth 和策略已经配好了。"
else
  bao write auth/oidc/config \
    oidc_discovery_url="$KEYCLOAK_ISSUER" \
    oidc_client_id="openbao" \
    oidc_client_secret="$OIDC_CLIENT_SECRET" \
    default_role="platform-user" >/dev/null
  echo "  已配 oidc auth"
  OIDC_ACCESSOR=$(bao auth list -format=json | python3 -c '
import json, sys
print(json.load(sys.stdin).get("oidc/", {}).get("accessor", ""))')
fi

echo "==> 5/7 JWT auth(给 SDK 用:拿用户的 id_token 直接换 OpenBao token)"
# **和上面的 oidc 是两个口,不是一个。** oidc 那个是浏览器跳转流程(给人
# 在 UI 上登录用),而 notebook 里的 `platform_sdk.secret()` 手上只有一个
# id_token —— 那要走 JWT 登录(`auth/jwt/login`),不能跳浏览器。
#
# 两个口共用同一个 Keycloak issuer,所以是同一个身份;区别只是拿到 token
# 的方式。策略里两个 accessor 的路径都写上,谁登录都按自己的用户名隔离。
if bao auth list -format=json 2>/dev/null | grep -q '"jwt/"'; then
  echo "  已启用,跳过 enable"
else
  bao auth enable jwt
fi
bao write auth/jwt/config oidc_discovery_url="$KEYCLOAK_ISSUER" >/dev/null
JWT_ACCESSOR=$(bao auth list -format=json | python3 -c '
import json, sys
print(json.load(sys.stdin).get("jwt/", {}).get("accessor", ""))')
# bound_audiences 要能对上 id_token 的 aud。Keycloak 签给某个 client 的
# id_token,aud 是那个 client_id —— notebook 里拿到的是 jupyterhub 那个
# client 签的,不是 openbao 的。**这一条写错的症状是 "invalid audience",
# 而不是权限不足**,不要往策略上查。
bao write auth/jwt/role/platform-user \
  role_type="jwt" \
  bound_audiences="jupyterhub,openbao,account" \
  user_claim="preferred_username" \
  groups_claim="groups" \
  policies="platform-user" \
  ttl="1h" >/dev/null
echo "  已建 jwt role: platform-user(accessor=${JWT_ACCESSOR})"


if [ -n "$OIDC_ACCESSOR" ]; then
  # 策略模板要用到 accessor,所以这一段必须在 oidc 启用之后。
  # **两个 accessor 的路径都写。** 同一个人从 UI 登录(oidc)和从 SDK 登录
  # (jwt)是两条不同的 alias,策略模板按 accessor 展开 —— 只写一个的话,
  # 另一条路进来的人会**看不到自己的凭据**,而且不报权限错,只是列出来是空的。
  bao policy write platform-user - <<EOF
# 自己的凭据:读写自己那一段,别人的碰不到。
# 这条判断由 OpenBao 在鉴权时做,不是调用方自觉(ADR-089)。
path "secret/data/users/{{identity.entity.aliases.${OIDC_ACCESSOR}.name}}/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "secret/metadata/users/{{identity.entity.aliases.${OIDC_ACCESSOR}.name}}/*" {
  capabilities = ["read", "list", "delete"]
}
path "secret/metadata/users/{{identity.entity.aliases.${OIDC_ACCESSOR}.name}}" {
  capabilities = ["list"]
}
path "secret/data/users/{{identity.entity.aliases.${JWT_ACCESSOR}.name}}/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "secret/metadata/users/{{identity.entity.aliases.${JWT_ACCESSOR}.name}}/*" {
  capabilities = ["read", "list", "delete"]
}
path "secret/metadata/users/{{identity.entity.aliases.${JWT_ACCESSOR}.name}}" {
  capabilities = ["list"]
}
EOF
  echo "  已写策略 platform-user(按登录者用户名隔离)"

  # allowed_redirect_uris 要把 UI 那条也列进来,不然浏览器登录会被 OpenBao
  # 自己拒(它校验回调地址,和 Keycloak 那边是两套各自的白名单)。
  bao write auth/oidc/role/platform-user \
    bound_audiences="openbao" \
    allowed_redirect_uris="http://localhost:8250/oidc/callback,${OPENBAO_EXTERNAL_URL}/ui/vault/auth/oidc/oidc/callback" \
    user_claim="preferred_username" \
    groups_claim="groups" \
    policies="platform-user" \
    oidc_scopes="openid,profile,email,groups" >/dev/null
  echo "  已建 oidc role: platform-user"
else
  echo "  跳过 platform-user 策略(需要 oidc accessor)"
fi

echo "==> 6/7 组共享凭据的策略"
# 组共享:一个团队共用一个源库账号是真实需求(每人一个账号反而更难管)。
# **读写分开**:组内成员能读,只有 platform-team 能写 —— 不然任何一个组员
# 都能悄悄把凭据换掉,而其他人只会看到"连不上了"。
for grp in platform-team data-analysts algorithm-team viewers; do
  bao policy write "group-${grp}" - <<EOF
path "secret/data/shared/${grp}/*" {
  capabilities = ["read", "list"]
}
path "secret/metadata/shared/${grp}/*" {
  capabilities = ["read", "list"]
}
path "secret/metadata/shared/${grp}" {
  capabilities = ["list"]
}
EOF
done
bao policy write group-platform-team-write - <<'EOF'
# platform-team 能写所有组的共享凭据。
path "secret/data/shared/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "secret/metadata/shared/*" {
  capabilities = ["read", "list", "delete"]
}
EOF
echo "  已写 4 个组策略 + platform-team 的写策略"

echo "==> 7/7 把 Keycloak 的组映射成 OpenBao 的身份组"
# **组共享凭据的授权就落在这一步。** OIDC role 上配了 groups_claim="groups",
# 登录时 OpenBao 会拿 token 里的组名去找**同名的外部身份组**;找到了就把
# 那个组的策略加到这次会话上。
#
# 没有这一步的话:每个人只有自己那一段路径,`secret/shared/<组名>/` 谁都
# 读不到 —— 而且**不会报错**,只表现为"列出来是空的"。这和 2026-08-29
# 连修三处(Superset 全员管理员、权限门户全员 403、Trino 的
# is_platform_admin 是摆设)是同一个形态:代码里有一个按组判断的分支,
# 而组信息压根没传过来。
if [ -n "$OIDC_ACCESSOR" ]; then
  for grp in platform-team data-analysts algorithm-team viewers; do
    policies="group-${grp}"
    # platform-team 额外拿写权限(见上面那条策略的说明:组内成员能读,
    # 只有 platform-team 能写,不然任何组员都能悄悄换掉凭据)。
    [ "$grp" = "platform-team" ] && policies="group-${grp},group-platform-team-write"

    gid=$(bao read -format=json "identity/group/name/${grp}" 2>/dev/null | python3 -c '
import json, sys
try: print(json.load(sys.stdin)["data"]["id"])
except Exception: pass' || true)
    if [ -z "$gid" ]; then
      gid=$(bao write -format=json identity/group name="${grp}" type="external" \
              policies="${policies}" | python3 -c '
import json, sys
print(json.load(sys.stdin)["data"]["id"])')
      echo "  已建身份组 ${grp}"
    else
      # 幂等:已存在就只更新策略(策略清单可能变了)
      bao write "identity/group/id/${gid}" name="${grp}" type="external" \
        policies="${policies}" >/dev/null
      echo "  身份组 ${grp} 已存在,已同步策略"
    fi

    # 组别名:告诉 OpenBao "OIDC 里那个叫 <grp> 的组,就是这个身份组"。
    # 没有别名的话身份组建了也匹配不上,同样是静默失效。
    existing_alias=$(bao list -format=json identity/group-alias/id 2>/dev/null | python3 -c '
import json, sys
try: print("\n".join(json.load(sys.stdin)))
except Exception: pass' || true)
    found=""
    for aid in $existing_alias; do
      aname=$(bao read -format=json "identity/group-alias/id/${aid}" 2>/dev/null | python3 -c '
import json, sys
try: print(json.load(sys.stdin)["data"]["name"])
except Exception: pass' || true)
      [ "$aname" = "$grp" ] && { found="$aid"; break; }
    done
    if [ -z "$found" ]; then
      bao write identity/group-alias name="${grp}" \
        mount_accessor="${OIDC_ACCESSOR}" canonical_id="${gid}" >/dev/null
      echo "    已建组别名 ${grp}"
    fi
  done
else
  echo "  跳过(需要 oidc accessor)"
fi

echo
echo "=== 完成。日志:$LOG_FILE ==="
echo "**注意:作业以 owner 身份读 secret/users/<owner>/ 这条路还没开** ——"
echo "见 ADR-089 第 6 条:谁能改 job.yaml 谁就能把 owner 写成别人,而 owner"
echo "对账现在因为 employees.csv 是占位数据、每次都走「拿不到身份 → 放行」。"
