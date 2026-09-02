#!/usr/bin/env bash
# 真正走完一遍 SSO 登录 —— 不是探活,不是伪造请求头,是拿账号密码走完
# 「点登录 → Keycloak 表单 → 跳回来 → 落在已登录页面」这条人会走的路。
#
# **为什么需要这个脚本。** 2026-09-02 发现 Superset 的 Keycloak 登录从
# 08-29 起就是坏的(自定义 SecurityManager 里 userinfo 的相对路径少了一段
# `openid-connect/`,FAB 把异常吞掉、静默回登录页),而**能力表上那一格
# 一直写着「集成验证」**。原因是此前所有验证都是程序化的:
#
#   - impersonation 用 superset.utils.core.override_user 直接构造上下文
#   - 门户按角色显示用伪造的 X-Forwarded-* 请求头
#   - SQL Lab 用 permalink 接口
#
# **一次都没有真的走完登录。** 而那恰恰是每个用户每天要走的第一步。
#
# 用法:
#   ./scripts/49-verify-sso-login.sh <用户名> <密码> [组件...]
#   ./scripts/49-verify-sso-login.sh zhenghe 'xxx' superset
#
# 密码只从命令行读,不落盘;脚本跑完清 cookie。
set -euo pipefail

USER_NAME="${1:-}"; PASSWORD="${2:-}"; shift 2 2>/dev/null || true
TARGETS=("$@")
[ ${#TARGETS[@]} -gt 0 ] || TARGETS=(portal superset airflow openmetadata mlflow \
  grafana argo-workflows permission-request table-registration spark-history \
  jupyterhub)
[ -n "$USER_NAME" ] && [ -n "$PASSWORD" ] || {
  echo "用法: $0 <用户名> <密码> [组件...]"; exit 1; }

DOMAIN="${DOMAIN_SUFFIX:-local-lite.test}"
PORT="${HTTP_PORT_SUFFIX:-:32460}"

# 每个组件三件事:主机名、登录入口、"登进去了"的标志。
#
# **标志必须是只有登录成功才会出现的东西。** 不能用 HTTP 200 —— 每一个
# 组件在登录失败时都返回 200,只是又给你一张登录页,这正是 Superset 那个
# bug 能藏一周的原因。
host_of() { case "$1" in
  portal) echo "portal";;
  permission-request) echo "permission-request";;
  table-registration) echo "table-registration";;
  spark-history) echo "spark-history";;
  *) echo "$1";; esac; }

entry_of() { case "$1" in
  superset) echo "/login/keycloak";;
  airflow) echo "/auth/login/keycloak";;
  # OpenMetadata 走 confidential client:登录入口是后端这个接口,而且
  # **redirectUri 必须和 Keycloak 里登记的完全一致**,差一个尾斜杠就是
  # `Redirect URI must exactly match a trusted redirect URI`(400)。
  # 首页 `/` 是直接 200 的空壳,不会自己弹去登录,所以不能拿它当入口。
  openmetadata) echo "/api/v1/auth/login?redirectUri=${EXTERNAL_SCHEME:-http}://openmetadata.${DOMAIN}${PORT}/callback";;
  grafana) echo "/login/generic_oauth";;
  jupyterhub) echo "/hub/oauth_login";;
  argo-workflows) echo "/oauth2/redirect";;
  # oauth2-proxy 挡着的:访问首页就会被弹去登录
  portal|mlflow|permission-request|table-registration|spark-history) echo "/";;
  *) echo "/";; esac; }

# 登录成功后最终 URL 里应该出现的片段
success_of() { case "$1" in
  superset) echo "/superset/welcome";;
  *) echo "$(host_of "$1").${DOMAIN}";; esac; }

# 登录成功后页面里**不该**出现的东西(出现就说明又被弹回登录页)
LOGIN_MARKERS='Sign in with Keycloak|login-actions/authenticate|oauth2/start|用 Keycloak 登录'
# 被授权层拒绝的标志。oauth2-proxy 的拒绝页固定带这几个词。
DENIED_MARKERS='Forbidden|You do not have permission|not authorized|403 '

# **SPA 的组件不能靠看页面判断。** Airflow 3.x / Grafana / OpenMetadata
# 的首页是个空壳,内容全由 JS 渲染 —— 登录成功和失败拿到的 HTML 一模一样
# (都是那 586 字节的 <div id="root">)。对它们改成登录后拿同一份 cookie
# 去请求一个**需要认证的 API**:不带 cookie 时它返回 401,带上之后返回
# 200,才说明会话真的建起来了。
#
# 2026-09-02 第一版按页面判断,把 Airflow 和 Grafana 都判成"没登进去",
# 而它们其实是好的 —— **验证方法自己错了,比没验更糟**,会让人去修一个
# 不存在的问题。
authcheck_of() { case "$1" in
  airflow) echo "/api/v2/dags";;
  grafana) echo "/api/user";;
  jupyterhub) echo "/hub/api/user";;
  *) echo "";; esac; }

# **OpenMetadata 这一条 curl 走不完,而且不是它坏了。**
# 它的回调是 `/callback#id_token=...` —— token 放在 URL 的 **fragment** 里,
# 浏览器不会把 fragment 发给服务器,而是交给前端 JS 去读。curl 跟随重定向时
# 只会把 `/callback`(没有 fragment)再请求一遍,后端认为这是个无效回调,
# **把刚发的 OM_SESSION 清掉**,然后跳 /signin。
#
# 所以对它的判据改成看重定向链里的证据:后端**发过**一个非空的 OM_SESSION,
# 并且回调 URL 里带着 id_token。这两件事同时成立,就说明认证换取会话这一步
# 是通的,剩下的是浏览器 JS 的事。
#
# 记这一段是因为:2026-09-02 第一版按"最终落在哪个页面"判,把 OpenMetadata
# 判成登录失败 —— **验证方法自己错了比没验更糟**,会让人去修一个不存在的
# 问题。同一天在 Airflow 和 Grafana 上也犯过一次(SPA 空壳页面)。
uses_fragment_callback() { [ "$1" = "openmetadata" ]; }

fail=0; passed=0
for app in "${TARGETS[@]}"; do
  BASE="http://$(host_of "$app").${DOMAIN}${PORT}"
  JAR=$(mktemp); OUT=$(mktemp); HDR=$(mktemp)
  printf '==> %-20s' "$app"

  # 1. 从入口出发,跟到 Keycloak 的登录表单
  FORM=$(curl -s -c "$JAR" -L -m 25 "${BASE}$(entry_of "$app")" 2>/dev/null \
    | grep -oE 'action="[^"]*login-actions/authenticate[^"]*"' | head -1 \
    | sed 's/action="//; s/"$//; s/&amp;/\&/g')
  if [ -z "$FORM" ]; then
    echo "✗ 没跳到 Keycloak 的登录表单 —— 跳转配错,或者这个组件根本没接 SSO"
    fail=$((fail+1)); rm -f "$JAR" "$OUT" "$HDR"; continue
  fi

  # 2. 提交账号密码,跟随跳转回组件
  FINAL=$(curl -s -b "$JAR" -c "$JAR" -L -m 40 -o "$OUT" -D "$HDR" -w '%{url_effective}' \
    --data-urlencode "username=${USER_NAME}" \
    --data-urlencode "password=${PASSWORD}" \
    --data-urlencode "credentialId=" "$FORM" 2>/dev/null)

  # 3. **判据是"会话真的建起来了",不是 HTTP 200。**
  #    坏的时候组件一样返回 200,只是又给你一张登录页。
  API=$(authcheck_of "$app")
  if uses_fragment_callback "$app"; then
    if grep -qiE "^set-cookie: *OM_SESSION=[A-Za-z0-9]" "$HDR" \
       && grep -qiE "^location:.*#id_token=" "$HDR"; then
      echo "✓ 登录成功(后端发了 OM_SESSION,回调带 id_token)"
      passed=$((passed+1))
    else
      echo "✗ 没登进去 —— 重定向链里没有 OM_SESSION 或 id_token"
      fail=$((fail+1))
    fi
  elif [ -n "$API" ]; then
    # SPA:拿登录后的 cookie 去请求一个需要认证的接口
    CODE=$(curl -s -b "$JAR" -m 25 -o /dev/null -w '%{http_code}' "${BASE}${API}" 2>/dev/null)
    if [ "$CODE" = "200" ]; then
      echo "✓ 登录成功(${API} 带 cookie 返回 200)"
      passed=$((passed+1))
    else
      echo "✗ 没登进去 —— ${API} 返回 ${CODE}(不带 cookie 时是 401)"
      fail=$((fail+1))
    fi
  elif grep -qiE "$DENIED_MARKERS" "$OUT"; then
    # **被 403 不等于登录坏了,恰恰相反。** oauth2-proxy 的 allowed_groups
    # 生效时就是这个结果:认证过了、身份和组都拿到了,只是这个账号不在
    # 允许的组里。这条链是通的,所以算通过 —— 但要和"真的登进去了"区分
    # 开,否则会把一个正确的拒绝读成"能用"。
    #
    # 2026-09-02 第一版没有这一档,把 MLflow 和 Spark History 的 403 页面
    # 判成了登录成功(它确实"最终落在了本站、页面里没有登录入口")。
    echo "⊘ 认证通过但被拒 —— 这个账号不在 allowed_groups 里(授权生效,符合预期)"
    passed=$((passed+1))
  elif [[ "$FINAL" == *"$(success_of "$app")"* ]] \
     && ! grep -qiE "$LOGIN_MARKERS" "$OUT"; then
    echo "✓ 登录成功 → ${FINAL}"
    passed=$((passed+1))
  else
    echo "✗ 没登进去,最终停在 ${FINAL}"
    if grep -qiE "$LOGIN_MARKERS" "$OUT"; then
      echo "     页面里还有登录入口 —— 认证过了但会话没建起来(HTTP 200 不算数)"
    fi
    fail=$((fail+1))
  fi
  rm -f "$JAR" "$OUT" "$HDR"
done

echo
echo "=== ${passed} 个组件登录成功,${fail} 个失败 ==="
exit $([ "$fail" -eq 0 ] && echo 0 || echo 1)
