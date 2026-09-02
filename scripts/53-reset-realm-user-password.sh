#!/usr/bin/env bash
# 重设 Keycloak platform 域某个用户的密码,**并写回凭据文件**。
#
# **为什么要有这个脚本,而不是直接 kcadm set-password。**
#
# 2026-09-02 踩到:验证过程中(要真实用户的 token 才能验 OpenBao 的按人隔离、
# 门户凭据页)临时改过几个用户的密码,验完按惯例换成随机值 —— 但**没有回写
# `secrets/generated-credentials.txt`**。结果是那个文件里 5 条 Keycloak 用户
# 密码有 3 条是死的,而使用方照着它登录,登不进去。
#
# 更糟的是 `show-credentials.sh --audit-file` 当时报的是"**已失效 0 条**"
# —— 那些行的标签里没有斜杠,被审计的正则**静默跳过**了。
# **一份说"全都有效"的报告,比没有报告更容易让人放心地用错密码。**
# (那个 bug 同一天修了:现在会明确报"查不了",不再假装不存在。)
#
# 所以:**任何改 Keycloak 用户密码的操作都走这个脚本**,它保证两边一致。
#
# 用法:
#   ./scripts/53-reset-realm-user-password.sh <用户名>            # 随机密码
#   ./scripts/53-reset-realm-user-password.sh <用户名> <密码>     # 指定密码
#   ./scripts/53-reset-realm-user-password.sh --all               # 全部重设
set -euo pipefail

NS="${KEYCLOAK_NAMESPACE:-keycloak}"
POD="${KEYCLOAK_POD:-keycloak-keycloakx-0}"
REALM="${KEYCLOAK_REALM:-platform}"
CRED_FILE="${CRED_FILE:-secrets/generated-credentials.txt}"

usage() { echo "用法: $0 <用户名> [密码]   或   $0 --all"; exit 1; }
[ $# -ge 1 ] || usage

kc() { kubectl -n "$NS" exec "$POD" -- "$@"; }

login() {
  kc sh -c '/opt/keycloak/bin/kcadm.sh config credentials \
    --server http://localhost:8080/auth --realm master \
    --user admin --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null 2>&1' || {
    echo "!! 连不上 Keycloak 或者 master admin 密码不对"; exit 1; }
}

reset_one() {  # $1=用户名 $2=密码(可空)
  local u="$1" pw="${2:-}"
  # **不要用 `tr -dc ... </dev/urandom | head -c N`。** head 读够就关管道,
  # tr 收到 SIGPIPE 退出非零,而 `set -o pipefail` 会把整条命令判成失败 ——
  # 脚本在 `set -e` 下**静默退出**,不打印任何东西。2026-09-02 实测踩到。
  [ -n "$pw" ] || pw=$(head -c 32 /dev/urandom | base64 | tr -d '=+/' | cut -c1-20)
  local id
  id=$(kc sh -c "/opt/keycloak/bin/kcadm.sh get users -r $REALM -q username=$u --fields id 2>/dev/null" \
       | grep -o '"[a-f0-9-]\{36\}"' | head -1 | tr -d '"')
  if [ -z "$id" ]; then echo "  !! $REALM 域里没有用户 $u"; return 1; fi
  kc sh -c "/opt/keycloak/bin/kcadm.sh set-password -r $REALM --userid $id --new-password '$pw'" >/dev/null
  echo "$u $pw"
}

login

TMP=$(mktemp)
if [ "$1" = "--all" ]; then
  users=$(kc sh -c "/opt/keycloak/bin/kcadm.sh get users -r $REALM --fields username 2>/dev/null" \
          | grep -o '"username" *: *"[^"]*"' | sed 's/.*: *"//; s/"$//')
  for u in $users; do reset_one "$u" || true; done > "$TMP"
else
  reset_one "$1" "${2:-}" > "$TMP"
fi

# ---- 写回凭据文件 ----
# **这一步是这个脚本存在的理由。** 只改 Keycloak 不回写,就是这次踩的坑。
if [ -f "$CRED_FILE" ]; then
  python3 - "$CRED_FILE" "$TMP" "$REALM" <<'PY'
import pathlib, sys
cred, tmp, realm = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
label = f"keycloak-{realm}-realm"
new = {}
for line in tmp.read_text().split("\n"):
    if not line.strip(): continue
    u, pw = line.split()
    new[u] = f"{label} {u} / {pw}"
lines = cred.read_text(encoding="utf-8").split("\n")
out, done = [], set()
for l in lines:
    parts = l.split()
    if len(parts) >= 4 and parts[0] == label and parts[1] in new:
        out.append(new[parts[1]]); done.add(parts[1])
    else:
        out.append(l)
# 文件里原来没有的,补在同类的最后一条后面(没有同类就追加)
missing = [u for u in new if u not in done]
if missing:
    idx = max((i for i, l in enumerate(out) if l.startswith(label)), default=len(out) - 1)
    for u in sorted(missing, reverse=True):
        out.insert(idx + 1, new[u])
cred.write_text("\n".join(out), encoding="utf-8")
print(f"  已回写 {cred}:更新 {len(done)} 条,新增 {len(missing)} 条")
PY
else
  echo "  !! 找不到 $CRED_FILE,只改了 Keycloak,没有回写"
fi

echo
echo "新密码:"
sed 's/^/  /' "$TMP"
rm -f "$TMP"
echo
echo "验证能不能真的登录(判据不是 HTTP 200,见脚本注释):"
echo "  ./scripts/52-verify-sso-login.sh <用户名> '<密码>' superset"
