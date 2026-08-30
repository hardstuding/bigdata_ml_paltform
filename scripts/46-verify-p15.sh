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
      # **不要写成 `... | while read`。** 管道右边是子 shell,里面调 ok/bad
      # 改的是子 shell 里的数组副本 —— 汇总时全部丢失。2026-08-30 实测:
      # 这里明明打了 ✅,汇总里却少一条;更糟的是**这段里的 ❌ 也会被静默
      # 吞掉**,一个验证脚本漏报失败,比没有这个脚本更危险。
      PREVIEW_CHECK="$(echo "$RESULT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
p = json.loads(d['preview']) if d['preview'].strip().startswith('{') else {}
print('PREVIEW_OK' if 'COMMENT' in p.get('ddl','') and 'partitioning' in p.get('ddl','') else 'PREVIEW_BAD:' + str(p)[:200])
")"
      case "$PREVIEW_CHECK" in
        PREVIEW_OK) ok "预览返回的 DDL 带 COMMENT 和 partitioning" ;;
        *)          bad "预览 DDL 不对:$PREVIEW_CHECK" ;;
      esac

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

      # 这里**不看状态码**。第一版断言的是 302,结果永远失败 ——
      # `urllib.request.urlopen` 默认会跟随重定向,拿到的是最终那个 200。
      # 而且状态码本来就不是这条的判据:"被挡住了没有"要看库里那条记录,
      # 下面那条查的就是它。
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
      # **从 Pod 取日志,不要写 `kubectl logs workflow/<name>`。**
      # 后者会报 `no kind "Workflow" is registered ... for logs`,而
      # `2>/dev/null || true` 把它吞成空字符串 —— 于是下面两条检查都在拿
      # **空串**做判断:"有没有 ModuleNotFoundError"必然通过(假阳性),
      # "有没有 2026-08-01"必然失败(假阴性)。2026-08-30 实测撞到,两条
      # 检查同时是错的、而且方向相反,差点让人以为参数功能坏了。
      WFPOD="$(kubectl -n argo-workflows get pods \
                 -l "workflows.argoproj.io/workflow=$WF" \
                 -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
      LOGS=""
      [ -n "$WFPOD" ] && LOGS="$(kubectl -n argo-workflows logs "$WFPOD" -c main 2>/dev/null || true)"

      [ "$PHASE" = "Succeeded" ] && ok "补数作业跑成功" || bad "补数作业状态是 $PHASE"
      if [ -z "$LOGS" ]; then
        # 取不到日志就**明说取不到**,不要拿空串继续判断后面两条。
        bad "取不到作业日志(pod=$WFPOD),下面两条无法判断"
      else
        echo "$LOGS" | grep -q "ModuleNotFoundError" \
          && bad "日志里有 ModuleNotFoundError —— 多文件挂载没生效" \
          || ok "多文件挂载生效(import jobkit 没报错)"
        echo "$LOGS" | grep -q "2026-08-01" \
          && ok "作业真的按 run_date=2026-08-01 跑的(参数生效)" \
          || bad "日志里没看到 2026-08-01 —— 参数没传进去"
      fi
      # **最强的判据是表里真的有那一天的数据**,不是日志里出现了那个字符串。
      # 这个仓库反复强调的"判据要是业务结果" —— 日志能被打印语句骗过去,
      # 表里的行骗不了。
      T_POD2="$(pod table-registration-app 'app=table-registration-app')"
      if [ -n "$T_POD2" ]; then
        ROWS="$(kubectl -n table-registration-app exec "$T_POD2" -- python3 -c "
import os, trino, warnings
warnings.filterwarnings('ignore')
from trino.auth import BasicAuthentication
c = trino.dbapi.connect(host=os.environ['TRINO_HOST'], port=int(os.environ['TRINO_PORT']),
    user=os.environ['TRINO_USER'], http_scheme='https', verify=False,
    auth=BasicAuthentication(os.environ['TRINO_USER'], os.environ['TRINO_PASSWORD']), catalog='iceberg')
cur = c.cursor()
cur.execute(\"SELECT count(*) FROM iceberg.demo.orders_by_region_daily WHERE run_date = DATE '2026-08-01'\")
print(cur.fetchall()[0][0])" 2>/dev/null | tail -1)"
        [ "${ROWS:-0}" -gt 0 ] 2>/dev/null \
          && ok "补数结果真的落进表里了(run_date=2026-08-01 有 $ROWS 行)" \
          || bad "表里没有 run_date=2026-08-01 的数据(拿到:$ROWS)"
      else
        skip "没有 table-registration-app pod,查不了 Trino,补数结果无法从表侧确认"
      fi

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

# ---------------------------------------------------------------- sqllab
# SQL Lab 走的那条 Trino 连接,身份到底是谁(ADR-084 唯一没验过的一环)。
#
# **原本以为这条只能靠人点浏览器**,后来发现不用:SQL Lab 用的就是
# `Database.get_sqla_engine()` 这条路,在 pod 里用 Superset 自己的
# `override_user` 把身份放进去,走的是同一份代码。
#
# **注意别用 `flask_login.login_user`**:2026-08-30 第一次就是这么写的,
# 结果 `current_user` 返回 `superset_service`,差点当成"impersonation 坏了"
# 报出去 —— 实际是 Superset 读的不是 login_user 设的那个地方。
# 一个测试装置写错、结论方向完全相反,和这个脚本自己那三个 bug 是一类。
if want sqllab; then
  log "== SQL Lab 的身份和权限 =="
  if ! kubectl -n superset get deploy/superset >/dev/null 2>&1; then
    skip "superset 没部署"
  else
    OUT="$(kubectl -n superset exec deploy/superset -c superset -- python3 -c "
import sys, warnings; sys.path.insert(0,'/app/pythonpath'); warnings.filterwarnings('ignore')
from superset.app import create_app
app = create_app()
with app.app_context():
    from superset.models.core import Database
    from superset.extensions import db, security_manager as sm
    from superset.utils.core import override_user
    d = db.session.query(Database).filter_by(database_name='Trino').one()
    print('IMPERSONATE=' + str(d.impersonate_user))
    u = sm.find_user(username='analyst001')
    if not u:
        print('NOUSER'); raise SystemExit
    with app.test_request_context(), override_user(u):
        with d.get_sqla_engine() as eng:
            print('WHOAMI=' + str(eng.execute('SELECT current_user').fetchall()[0][0]))
            try:
                eng.execute('SELECT count(*) FROM iceberg.demo.access_test_l1').fetchall()
                print('GRANTED=ok')
            except Exception as e:
                print('GRANTED=fail:' + str(e)[:60])
            try:
                eng.execute('SELECT count(*) FROM iceberg.demo.orders').fetchall()
                print('UNGRANTED=allowed')
            except Exception as e:
                print('UNGRANTED=denied' if 'PERMISSION_DENIED' in str(e) else 'UNGRANTED=other')
            try:
                row = eng.execute('SELECT phone FROM iceberg.demo.access_test_l1 LIMIT 1').fetchall()[0][0]
                print('MASKED=' + ('yes' if '*' in str(row) else 'no:' + str(row)))
            except Exception as e:
                print('MASKED=err')
" 2>/dev/null || true)"
    echo "$OUT" | grep -q "IMPERSONATE=True"   && ok "Trino 这个 database 开着 impersonation" || bad "Trino database 没开 impersonation"
    echo "$OUT" | grep -q "NOUSER" && skip "Superset 里还没有 analyst001,后面几条跳过" || {
      echo "$OUT" | grep -q "WHOAMI=analyst001"  && ok "SQL Lab 的连接上 current_user 是登录者本人" || bad "current_user 不是 analyst001($(echo "$OUT" | grep WHOAMI))"
      echo "$OUT" | grep -q "GRANTED=ok"         && ok "有 grant 的表查得到" || bad "有 grant 的表查不到"
      echo "$OUT" | grep -q "UNGRANTED=denied"   && ok "没 grant 的表被 PERMISSION_DENIED 拒掉" || bad "没 grant 的表**没有**被拒($(echo "$OUT" | grep UNGRANTED))"
      echo "$OUT" | grep -q "MASKED=yes"         && ok "列级脱敏在这条路径上生效" || bad "脱敏没生效($(echo "$OUT" | grep MASKED))"
    }
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
