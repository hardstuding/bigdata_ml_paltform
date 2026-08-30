#!/usr/bin/env bash
# 把 docs/project/next-boot-checklist.md 那份手工清单变成一个能跑的脚本。
#
# **为什么值得单独写这个**:2026-08-29 一整天写完了 P1.5 的六条,而它们
# **大部分只有单元测试、没上过集群**。留下的是一份 14 条的手工清单 ——
# 而手工清单会被跳过,这是这个项目反复吃过的亏:一件事只要"需要有人记得
# 去做",迟早就没人做,然后"部署了 + 绿了"被当成"能用了"
# (docs/project/capability-matrix.md 底部那节列了四次)。
#
# **每一条独立执行、独立报告**:一条失败不影响后面的,最后给汇总。这一点
# 是有意的 —— `set -e` 式的脚本第一条挂了就什么都看不到,而这里恰恰想知道
# "14 条里到底有几条是好的"。
#
# **这个脚本不改任何平台状态**,除了几处明确标注的临时数据(建的表带
# `p15verify_` 前缀,用完删掉)。
#
# 有几条**它验不了**,如实列在最后:凡是必须走浏览器 SSO 的(SQL Lab 里
# 真的点一下、门户页面上真的看到黄色警告),脚本模拟不了 oauth2-proxy 的
# 完整登录流程。那几条仍然要人点一次。
#
# 用法:
#   ./scripts/46-verify-p15.sh              # 全跑
#   ./scripts/46-verify-p15.sh groups portal # 只跑指定的几组
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG="logs/verify-p15.log"
: > "$LOG"
log()  { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }
PASS=(); FAIL=(); SKIP=()
ok()   { PASS+=("$1"); echo "  ✅ $1" | tee -a "$LOG"; }
bad()  { FAIL+=("$1"); echo "  ❌ $1" | tee -a "$LOG"; }
skip() { SKIP+=("$1"); echo "  ⏭  $1" | tee -a "$LOG"; }

WANT=("$@")
want() {
  [ ${#WANT[@]} -eq 0 ] && return 0
  local g; for g in "${WANT[@]}"; do [ "$g" = "$1" ] && return 0; done; return 1
}

pod() { kubectl -n "$1" get pod -l "$2" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null; }

# ---------------------------------------------------------------- groups
# 这一组是**其它好几条的前提**:三个 Flask 应用能不能从 access token 里
# 读到 groups。读不到的话,门户按角色显示、代他人建表、审批分流全部走的是
# "拿不到组信息"那条降级分支 —— 它们不会报错,只是行为和预期不同。
if want groups; then
  log "== groups claim =="
  KC_POD="$(pod keycloak 'app.kubernetes.io/instance=keycloak')"
  if [ -z "$KC_POD" ]; then
    skip "Keycloak 没有 Running 的 Pod,groups 这一组全跳过"
  else
    for CLIENT in permission-request-app platform-portal table-registration-app superset; do
      HAS="$(kubectl -n keycloak exec "$KC_POD" -- sh -c '
        /opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8080/auth \
          --realm master --user admin --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null 2>&1
        CID=$(/opt/keycloak/bin/kcadm.sh get clients -r platform -q clientId='"$CLIENT"' --fields id 2>/dev/null \
              | grep -o "\"[a-f0-9-]*\"" | head -1 | tr -d "\"")
        [ -n "$CID" ] || { echo NOCLIENT; exit 0; }
        /opt/keycloak/bin/kcadm.sh get "clients/$CID/default-client-scopes" -r platform 2>/dev/null \
          | grep -c "\"groups\"" || true
      ' 2>/dev/null | tail -1)"
      case "$HAS" in
        NOCLIENT) skip "client $CLIENT 还不存在" ;;
        0|"")     bad  "client $CLIENT 没挂 groups 默认 scope —— 跑 scripts/03-configure-keycloak.sh" ;;
        *)        ok   "client $CLIENT 挂了 groups 默认 scope" ;;
      esac
    done
  fi
fi

# ---------------------------------------------------------------- portal
# 门户的按角色显示 / 我的权限 / 作业详情。
#
# **直接在 pod 里 curl localhost:8080**:门户前面挡着 oauth2-proxy,脚本
# 模拟不了完整的 OIDC 登录;而应用层的逻辑恰恰是靠 X-Forwarded-* 头驱动的,
# 在 pod 内部造这两个头,验的就是真实代码路径。**这不代表越权** —— 那条
# NetworkPolicy 只放行 oauth2-proxy 连 8080,集群外和其它 pod 造不出这个
# 请求,能在 pod 内部造是因为已经在 pod 内部了。
if want portal; then
  log "== 门户角色工作台 =="
  P_POD="$(pod platform-portal 'app=platform-portal')"
  if [ -z "$P_POD" ]; then
    skip "platform-portal 没有 Running 的 Pod,门户这一组全跳过"
  else
    mktoken() { python3 -c "
import base64, json, sys
p = base64.urlsafe_b64encode(json.dumps(
    {'preferred_username': sys.argv[1], 'groups': json.loads(sys.argv[2])}).encode()
).decode().rstrip('=')
print('eyJhbGciOiJub25lIn0.' + p + '.')" "$1" "$2"; }

    ANALYST_HTML="$(kubectl -n platform-portal exec "$P_POD" -- python3 -c "
import urllib.request
req = urllib.request.Request('http://localhost:8080/', headers={
    'X-Forwarded-User': 'analyst001',
    'X-Forwarded-Access-Token': '$(mktoken analyst001 '["data-analysts"]')'})
print(urllib.request.urlopen(req, timeout=20).read().decode())" 2>/dev/null || true)"

    if [ -z "$ANALYST_HTML" ]; then
      bad "门户首页取不到内容"
    else
      echo "$ANALYST_HTML" | grep -q "ArgoCD" \
        && bad "分析师身份仍然看得到 ArgoCD(按角色显示没生效)" \
        || ok "分析师看不到运维类工具"
      echo "$ANALYST_HTML" | grep -q "SQL 工作台" \
        && ok "分析师看得到 SQL 工作台入口" \
        || bad "分析师看不到 SQL 工作台入口"
      echo "$ANALYST_HTML" | grep -q "配置问题不是权限问题" \
        && bad "门户报 groups 拿不到 —— 先修 groups 那一组" \
        || ok "门户能读到 groups claim"
      echo "$ANALYST_HTML" | grep -qE "sqllab" \
        && ok "SQL 工作台链接带 /sqllab/ 路径" \
        || bad "SQL 工作台链接没带 /sqllab/ 路径"
    fi

    ADMIN_HTML="$(kubectl -n platform-portal exec "$P_POD" -- python3 -c "
import urllib.request
req = urllib.request.Request('http://localhost:8080/', headers={
    'X-Forwarded-User': 'admin',
    'X-Forwarded-Access-Token': '$(mktoken admin '["platform-team"]')'})
print(urllib.request.urlopen(req, timeout=20).read().decode())" 2>/dev/null || true)"
    echo "$ADMIN_HTML" | grep -q "ArgoCD" \
      && ok "平台组看得到全部工具" \
      || bad "平台组也看不到 ArgoCD(规则配反了?)"

    # 我的表权限:要求 permission-request-app 的 token 已经复制过来。
    #
    # **用 analyst001 而不是 admin 来验。** 第一版拿 admin 测,而 admin 在
    # table-access-grants.csv 里一条 grant 都没有 —— 于是这一栏永远不显示,
    # 脚本只能报"跳过",等于什么都没验到。选一个**真的有 grant 的用户**,
    # 这条才有意义。
    if ! kubectl -n platform-portal get secret permission-request-app-internal >/dev/null 2>&1; then
      bad "platform-portal 命名空间里没有 permission-request-app-internal —— 跑 scripts/00-generate-secrets.sh"
    else
      ok "门户拿到了 permission-request-app 的内部 token"
      GRANTEE="$(awk -F, 'NR>1 && $1!="" {print $1; exit}' platform/iam/table-access-grants.csv 2>/dev/null)"
      if [ -z "$GRANTEE" ]; then
        skip "table-access-grants.csv 里没有任何 grant,验不了「我的表权限」"
      else
        G_HTML="$(kubectl -n platform-portal exec "$P_POD" -- python3 -c "
import urllib.request
req = urllib.request.Request('http://localhost:8080/', headers={
    'X-Forwarded-User': '$GRANTEE',
    'X-Forwarded-Access-Token': '$(mktoken "$GRANTEE" '["data-analysts"]')'})
print(urllib.request.urlopen(req, timeout=20).read().decode())" 2>/dev/null || true)"
        echo "$G_HTML" | grep -q "我的表权限" \
          && ok "「我的表权限」对有 grant 的用户($GRANTEE)渲染出来了" \
          || bad "「我的表权限」对 $GRANTEE 也没显示 —— 门户没读到 grants"
        FIRST_TABLE="$(awk -F, -v u="$GRANTEE" 'NR>1 && $1==u {print $2; exit}' platform/iam/table-access-grants.csv)"
        [ -n "$FIRST_TABLE" ] && { echo "$G_HTML" | grep -q "$FIRST_TABLE" \
          && ok "表名 $FIRST_TABLE 真的出现在页面上(不是空表格)" \
          || bad "页面上没有 $FIRST_TABLE —— 渲染了标题但内容是空的"; }
      fi
    fi
  fi
fi

# ---------------------------------------------- table-registration
if want table; then
  log "== 建表工具 =="
  T_POD="$(pod table-registration-app 'app=table-registration-app')"
  if [ -z "$T_POD" ]; then
    skip "table-registration-app 没有 Running 的 Pod"
  else
    TBL="p15verify_$(date +%s)"
    RESULT="$(kubectl -n table-registration-app exec "$T_POD" -- python3 -c "
import json, urllib.parse, urllib.request
def post(path, form, groups=None):
    headers = {'X-Forwarded-User': 'zhenghe',
               'Content-Type': 'application/x-www-form-urlencoded'}
    if groups is not None:
        import base64
        p = base64.urlsafe_b64encode(json.dumps(
            {'preferred_username': 'zhenghe', 'groups': groups}).encode()).decode().rstrip('=')
        headers['X-Forwarded-Access-Token'] = 'eyJhbGciOiJub25lIn0.' + p + '.'
    req = urllib.request.Request('http://localhost:8080' + path,
                                 data=urllib.parse.urlencode(form, doseq=True).encode(),
                                 headers=headers, method='POST')
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

out = {}
# 预览:和真正建表用同一份 DDL
st, body = post('/preview', {'table_fqn': 'demo.$TBL',
                             'columns': 'id BIGINT # 主键\nts TIMESTAMP',
                             'partitioning': 'day(ts)'})
out['preview_status'] = st
out['preview'] = body
# 真的建一张
st, _ = post('/submit', {'table_fqn': 'demo.$TBL',
                         'columns': 'id BIGINT # 主键\nts TIMESTAMP',
                         'partitioning': 'day(ts)', 'security_level': '1',
                         'quality_rules': ['row_count_not_empty']}, groups=['data-analysts'])
out['submit_status'] = st
# 2 级表非平台组应该被挡住
st, _ = post('/submit', {'table_fqn': 'demo.${TBL}_l2', 'columns': 'id BIGINT',
                         'security_level': '2'}, groups=['data-analysts'])
out['l2_status'] = st
print(json.dumps(out))" 2>/dev/null | tail -1)"

    if [ -z "$RESULT" ]; then
      bad "建表工具没有返回结果"
    else
      echo "$RESULT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
p = json.loads(d['preview']) if d['preview'].strip().startswith('{') else {}
print('PREVIEW_OK' if 'COMMENT' in p.get('ddl','') and 'partitioning' in p.get('ddl','') else 'PREVIEW_BAD:' + str(p)[:200])
" | while read -r line; do
        case "$line" in
          PREVIEW_OK) ok "预览返回的 DDL 带 COMMENT 和 partitioning" ;;
          *)          bad "预览 DDL 不对:$line" ;;
        esac
      done

      # 真实表结构 —— 这才是判据,不是"提交没报错"
      DDL="$(kubectl -n table-registration-app exec "$T_POD" -- python3 -c "
import os, trino
from trino.auth import BasicAuthentication
c = trino.dbapi.connect(host=os.environ['TRINO_HOST'], port=int(os.environ['TRINO_PORT']),
    user=os.environ['TRINO_USER'], http_scheme='https', verify=False,
    auth=BasicAuthentication(os.environ['TRINO_USER'], os.environ['TRINO_PASSWORD']),
    catalog='iceberg')
cur = c.cursor(); cur.execute('SHOW CREATE TABLE iceberg.demo.$TBL')
print(cur.fetchall()[0][0])" 2>/dev/null || true)"
      if [ -z "$DDL" ]; then
        bad "Trino 里查不到 demo.$TBL —— 建表没成功"
      else
        echo "$DDL" | grep -qi "comment" && ok "Trino 里的表带字段说明" || bad "Trino 里的表没有字段 COMMENT"
        echo "$DDL" | grep -qi "partitioning" && ok "Trino 里的表真的分区了" || bad "Trino 里的表没有分区"
        kubectl -n table-registration-app exec "$T_POD" -- python3 -c "
import os, trino
from trino.auth import BasicAuthentication
c = trino.dbapi.connect(host=os.environ['TRINO_HOST'], port=int(os.environ['TRINO_PORT']),
    user=os.environ['TRINO_USER'], http_scheme='https', verify=False,
    auth=BasicAuthentication(os.environ['TRINO_USER'], os.environ['TRINO_PASSWORD']),
    catalog='iceberg')
cur = c.cursor(); cur.execute('DROP TABLE IF EXISTS iceberg.demo.$TBL'); cur.fetchall()
print('dropped')" >/dev/null 2>&1 && log "  (已清理临时表 demo.$TBL)"
      fi

      echo "$RESULT" | grep -q '"l2_status": 302' \
        && ok "2 级表的提交被受理(下一步看它有没有被落成 rejected)" \
        || bad "2 级表提交返回的不是 302"
      L2NOTE="$(kubectl -n table-registration-app exec "$T_POD" -- python3 -c "
import sqlite3, os
c = sqlite3.connect(os.environ.get('DB_PATH', '/data/registrations.db'))
r = c.execute(\"SELECT trino_status, note FROM registrations WHERE table_fqn LIKE '%${TBL}_l2' ORDER BY id DESC LIMIT 1\").fetchone()
print(r[0] + '|' + (r[1] or '') if r else 'NONE')" 2>/dev/null | tail -1)"
      case "$L2NOTE" in
        rejected\|*先审批*) ok "2 级表被挡住,记录里写明了要先审批" ;;
        NONE)              bad "找不到 2 级表那条记录" ;;
        *)                 bad "2 级表没有被挡住(拿到的是:$L2NOTE)" ;;
      esac
    fi
  fi
fi

# ---------------------------------------------------------------- jobs
if want jobs; then
  log "== 作业发布(多文件 + 补数) =="
  if ! kubectl -n argo-workflows get cronworkflow daily-order-summary >/dev/null 2>&1; then
    skip "argo-workflows 里没有 daily-order-summary 这个 CronWorkflow"
  else
    WF="$(kubectl -n argo-workflows create -f <(kubectl -n argo-workflows get cronworkflow daily-order-summary -o json | python3 -c "
import json, sys
cw = json.load(sys.stdin)
spec = cw['spec']['workflowSpec']
spec.setdefault('arguments', {}).setdefault('parameters', [])
for p in spec['arguments']['parameters']:
    if p['name'] == 'run_date':
        p['value'] = '2026-08-01'
print(json.dumps({'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Workflow',
  'metadata': {'generateName': 'p15verify-backfill-', 'namespace': 'argo-workflows',
               'labels': {'platform-sdk/submitted-by': 'p15verify'}},
  'spec': spec}))") -o jsonpath='{.metadata.name}' 2>/dev/null || true)"
    if [ -z "$WF" ]; then
      bad "补数 workflow 提交失败"
    else
      log "  提交了补数作业 $WF(run_date=2026-08-01),等它跑完…"
      kubectl -n argo-workflows wait --for=condition=Completed "workflow/$WF" --timeout=420s >/dev/null 2>&1
      PHASE="$(kubectl -n argo-workflows get workflow "$WF" -o jsonpath='{.status.phase}' 2>/dev/null)"
      LOGS="$(kubectl -n argo-workflows logs "workflow/$WF" -c main 2>/dev/null || true)"
      [ "$PHASE" = "Succeeded" ] && ok "补数作业跑成功(多文件 import jobkit 没问题)" \
                                 || bad "补数作业状态是 $PHASE"
      echo "$LOGS" | grep -q "ModuleNotFoundError" \
        && bad "日志里有 ModuleNotFoundError —— 多文件挂载没生效" \
        || ok "日志里没有 ModuleNotFoundError"
      echo "$LOGS" | grep -q "2026-08-01" \
        && ok "作业真的按 run_date=2026-08-01 跑的(参数生效)" \
        || bad "日志里没看到 2026-08-01 —— 参数没传进去"
      kubectl -n argo-workflows delete "workflow/$WF" >/dev/null 2>&1 && log "  (已清理 $WF)"
    fi
  fi
fi

# ------------------------------------------------------------- approval
if want approval; then
  log "== 审批体验(到期提醒 / 续期 / 拒绝原因) =="
  A_POD="$(pod permission-request-app 'app=permission-request-app')"
  if [ -z "$A_POD" ]; then
    skip "permission-request-app 没有 Running 的 Pod"
  else
    HTML="$(kubectl -n permission-request-app exec "$A_POD" -- python3 -c "
import urllib.request
req = urllib.request.Request('http://localhost:8080/', headers={'X-Forwarded-User': 'analyst001'})
print(urllib.request.urlopen(req, timeout=20).read().decode())" 2>/dev/null || true)"
    if [ -z "$HTML" ]; then
      bad "权限门户首页取不到内容"
    else
      echo "$HTML" | grep -q "toLocaleString" \
        && ok "页面带了按浏览器时区换算时间的脚本" \
        || bad "页面上没有时区换算脚本"
      echo "$HTML" | grep -qE "等待审批|已通过|已拒绝" \
        && ok "状态显示的是中文" \
        || skip "页面上没有任何申请记录,看不出状态中文化(不算失败)"
      # 这条不带 access token,所以必然触发提示 —— 验的是"提示本身在"
      echo "$HTML" | grep -q "pass_access_token" \
        && ok "拿不到 groups 时页面上明确说了是配置问题" \
        || bad "拿不到 groups 时没有任何提示(又变回静默走 else 了)"
    fi

    # 拒绝必须带原因
    ST="$(kubectl -n permission-request-app exec "$A_POD" -- python3 -c "
import urllib.request, urllib.error
req = urllib.request.Request('http://localhost:8080/table-access/step/999999/reject',
                             data=b'', headers={'X-Forwarded-User': 'nobody'}, method='POST')
try:
    print(urllib.request.urlopen(req, timeout=15).status)
except urllib.error.HTTPError as e:
    print(e.code)" 2>/dev/null | tail -1)"
    # 999999 这个 step 不存在,期望 403(不是 500) —— 验的是这条路径没被
    # 新加的 comment 校验搞崩
    [ "$ST" = "403" ] && ok "拒绝接口对不存在的步骤返回 403 而不是 500" \
                      || bad "拒绝接口返回了 $ST(期望 403)"
  fi
fi

# ------------------------------------------------------------- 汇总
log ""
log "================ 汇总 ================"
log "通过 ${#PASS[@]} 条,失败 ${#FAIL[@]} 条,跳过 ${#SKIP[@]} 条"
if [ ${#FAIL[@]} -gt 0 ]; then
  log "失败的:"
  for f in "${FAIL[@]}"; do log "  - $f"; done
fi
log ""
log "这个脚本**验不了**的(必须人点一次,见 docs/project/next-boot-checklist.md):"
log "  - SQL Lab 里 SELECT current_user 是不是登录者本人(要走浏览器 SSO)"
log "  - 用两个真实账号验越权(A 打不开 B 的作业详情)"
log "  - 组权限申请的批准按钮(要 platform-team 真实登录)"
log "  - 门户上「我的作业」点进去的详情页外观"
log ""
log "完整日志:$LOG"

# **全跳过不算成功。**
#
# 第一版写的是 `[ ${#FAIL[@]} -eq 0 ]`,于是"集群没连上、5 组全跳过"会得到
# 退出码 0 —— 一个"什么都没验"的运行被报告成通过。这正是这个项目一整天
# 在修的那个模式(检查存在、但永远走 else),差点自己又犯一次。
if [ ${#FAIL[@]} -gt 0 ]; then
  log "结论:有失败项。"
  exit 1
fi
if [ ${#PASS[@]} -eq 0 ]; then
  log "结论:**一条都没真的验到**(全部跳过),不算通过。"
  log "      多半是没连上集群 —— 确认 KUBECONFIG,以及云主机开着。"
  exit 2
fi
log "结论:跑到的都通过了(${#PASS[@]} 条)。跳过的 ${#SKIP[@]} 条见上面。"
