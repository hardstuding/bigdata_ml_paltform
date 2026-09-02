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
[ ${#TARGETS[@]} -gt 0 ] || TARGETS=(superset)
[ -n "$USER_NAME" ] && [ -n "$PASSWORD" ] || {
  echo "用法: $0 <用户名> <密码> [组件...]"; exit 1; }

DOMAIN="${DOMAIN_SUFFIX:-local-lite.test}"
PORT="${HTTP_PORT_SUFFIX:-:32460}"

# 每个组件:登录入口 + 登录成功后应该出现的标志
entry_of() { case "$1" in
  superset) echo "/login/keycloak";;
  *) echo "/login/";; esac; }
success_of() { case "$1" in
  superset) echo "/superset/welcome/";;
  *) echo "/";; esac; }

fail=0
for app in "${TARGETS[@]}"; do
  BASE="http://${app}.${DOMAIN}${PORT}"
  JAR=$(mktemp); OUT=$(mktemp)
  echo "==> $app"

  # 1. 点「用 Keycloak 登录」,跟到 Keycloak 的登录表单
  FORM=$(curl -s -c "$JAR" -L -m 20 "${BASE}$(entry_of "$app")" 2>/dev/null \
    | grep -oE 'action="[^"]*login-actions/authenticate[^"]*"' | head -1 \
    | sed 's/action="//; s/"$//; s/&amp;/\&/g')
  if [ -z "$FORM" ]; then
    echo "  !! 没跳到 Keycloak 的登录表单 —— 要么跳转配错,要么已经登录着"
    fail=1; rm -f "$JAR" "$OUT"; continue
  fi

  # 2. 提交账号密码,跟随跳转回组件
  FINAL=$(curl -s -b "$JAR" -c "$JAR" -L -m 30 -o "$OUT" -w '%{url_effective}' \
    --data-urlencode "username=${USER_NAME}" \
    --data-urlencode "password=${PASSWORD}" \
    --data-urlencode "credentialId=" "$FORM" 2>/dev/null)

  # 3. **判据是"落在已登录页面",不是 HTTP 200。**
  #    坏掉的时候 Superset 也返回 200 —— 只是又给了你一张登录页。
  #    这正是这个 bug 能藏一周的原因。
  if [[ "$FINAL" == *"$(success_of "$app")"* ]] \
     && ! grep -qiE "Sign in with Keycloak|login-actions/authenticate" "$OUT"; then
    echo "  ✓ 登录成功,落在 $FINAL"
    grep -qi "$USER_NAME" "$OUT" && echo "    页面里出现用户名,身份带过来了"
  else
    echo "  ✗ 没登进去 —— 最终停在 $FINAL"
    echo "    (HTTP 200 不算成功:坏的时候它也返回 200,只是又给一张登录页)"
    fail=1
  fi
  rm -f "$JAR" "$OUT"
done

exit $fail
