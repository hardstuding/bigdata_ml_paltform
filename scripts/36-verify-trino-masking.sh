#!/usr/bin/env bash
# 端到端验证 Trino 的**列级脱敏 + 行级过滤**真的生效——不是"策略里写了"、
# 也不是"OPA 单元测试过了",是**一条真实 SQL 查出来的手机号确实是打码的、
# 查出来的行确实只有自己部门的**。
#
# **为什么单独写这个脚本**:docs/roles.md 里"敏感字段行列级策略"这一格
# 从 ADR-063 落地起就一直是 🟡,原因写得很明白——"Trino 已加载 4 条
# opa.policy(URI 生效了),但还没有真正验证过脱敏的实际效果"。
# 卡点是很具体的:**平台里能用密码登录 Trino 的 5 个账号全部在 OPA 的
# service_accounts 豁免名单里**(它们的查询不代表某个真实终端用户在看
# 数据,所以刻意不脱敏),而真实用户走 OAuth2 浏览器流程,脚本没法模拟。
#
# 这个脚本的做法:**临时**给 analyst001(platform/iam/table-access-grants.csv
# 里已经有它 l1/l2 两条 grant 的那个 demo 用户)加一个 Trino 密码,验证完
# **删掉**。不留常驻测试账号。
#
# 代价是要重启两次 Trino coordinator:密码文件是 subPath 挂载的,Secret
# 改了不会自动同步进容器(这个仓库记录过 subPath 的这个行为)。
#
# 验的是"分级"而不只是"有没有打码",这一点很重要:
#   - phone/email 的门槛是 security_level 2
#   - id_card 的门槛是 3
#   - analyst001 在 access_test_l1 上是 1 级 → 三个字段全打码
#   - 在 access_test_l2 上是 2 级 → phone/email 明文,id_card 仍然打码
# **如果只验"低权限看到打码",漏掉"高权限看到明文",那策略退化成"一律
# 打码"也会通过——那不是分级,是一刀切。**
#
# 行级过滤同理:demo.regional_sales 里放三个部门的数据,analyst001 在
# employees.csv 里是"数据分析组"。**光验"行数变少了"不够**——要确认少掉的
# 正好是别的部门那些行,而且自己部门那些行一条不少。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/verify-trino-masking.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

PROBE_USER="analyst001"
TRA_NS="table-registration-app"
TRA_POD="$(kubectl -n "$TRA_NS" get pod -l app=table-registration-app -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$TRA_POD" ] || { log "table-registration-app 没有 Running 的 Pod。"; exit 1; }

command -v htpasswd >/dev/null || { log "本机没有 htpasswd(apache2-utils / httpd)。"; exit 1; }

restart_trino() {
  kubectl -n trino rollout restart deploy/trino-coordinator >/dev/null
  kubectl -n trino rollout status deploy/trino-coordinator --timeout=240s >/dev/null
  # livenessProbe 的人工补丁每次 Deployment 重建都要重跑(ADR-017)
  ./scripts/07-fix-trino-liveness-probe.sh >/dev/null 2>&1 || true
}

cleanup() {
  log "清理:把 ${PROBE_USER} 的临时密码从 Trino 移除,再重启一次 coordinator"
  local db
  db="$(kubectl -n trino get secret trino-service-account -o jsonpath='{.data.password\.db}' | base64 -d | grep -v "^${PROBE_USER}:" || true)"
  kubectl -n trino patch secret trino-service-account --type merge -p "$(python3 -c "
import json, base64, sys
print(json.dumps({'data': {'password.db': base64.b64encode(sys.argv[1].encode()).decode()}}))
" "$db")" >/dev/null
  restart_trino
  log "清理完成,平台恢复到只有 5 个服务账号的状态。"
}
trap cleanup EXIT

log "1/4 建 demo 表(两张带敏感字段的 + 一张带部门列的,都对应 grants CSV 里已有的 grant)"
kubectl -n "$TRA_NS" exec -i "$TRA_POD" -- python3 - <<'PYEOF' 2>&1 | tee -a "$LOG_FILE"
import os, trino
c = trino.dbapi.connect(host=os.environ["TRINO_HOST"], port=int(os.environ["TRINO_PORT"]),
    user=os.environ["TRINO_USER"], http_scheme="https", verify=False,
    auth=trino.auth.BasicAuthentication(os.environ["TRINO_USER"], os.environ["TRINO_PASSWORD"]))
cur = c.cursor()
for t in ("access_test_l1", "access_test_l2"):
    cur.execute(f"""CREATE TABLE IF NOT EXISTS iceberg.demo.{t} (
        customer_id varchar, phone varchar, email varchar, id_card varchar)""")
    cur.fetchall()
    cur.execute(f"select count(*) from iceberg.demo.{t}")
    if cur.fetchall()[0][0] == 0:
        cur.execute(f"""INSERT INTO iceberg.demo.{t} VALUES
            ('C001','13812345678','alice@example.com','110101199001011234'),
            ('C002','13998765432','bob@example.com','310101199505055678')""")
        cur.fetchall()
    print(f"  {t} 就绪")

# 行级过滤的 demo 表:三个部门各两行。analyst001 在 employees.csv 里是
# "数据分析组",所以它应该只看得到那两行。
cur.execute("""CREATE TABLE IF NOT EXISTS iceberg.demo.regional_sales (
    region varchar, department varchar, amount double)""")
cur.fetchall()
cur.execute("select count(*) from iceberg.demo.regional_sales")
if cur.fetchall()[0][0] == 0:
    cur.execute("""INSERT INTO iceberg.demo.regional_sales VALUES
        ('华东','数据分析组',1000.0), ('华南','数据分析组',2000.0),
        ('华北','算法组',3000.0),     ('西南','算法组',4000.0),
        ('华中','平台组',5000.0),     ('东北','平台组',6000.0)""")
    cur.fetchall()
print("  regional_sales 就绪(3 个部门各 2 行)")
PYEOF

log "2/4 临时给 ${PROBE_USER} 加一个 Trino 密码(验证完会删)"
PROBE_PW="$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')"
EXISTING="$(kubectl -n trino get secret trino-service-account -o jsonpath='{.data.password\.db}' | base64 -d | grep -v "^${PROBE_USER}:" || true)"
HASH="$(htpasswd -nbBC 10 "$PROBE_USER" "$PROBE_PW")"
NEW_DB="$(printf '%s\n%s' "$EXISTING" "$HASH")"
kubectl -n trino patch secret trino-service-account --type merge -p "$(python3 -c "
import json, base64, sys
print(json.dumps({'data': {'password.db': base64.b64encode(sys.argv[1].encode()).decode()}}))
" "$NEW_DB")" >/dev/null

log "3/4 重启 coordinator 让密码文件生效(subPath 挂载不会自动同步)"
restart_trino

log "4/4 以 ${PROBE_USER} 的身份查这三张表,核对脱敏 + 行级过滤结果"
if kubectl -n "$TRA_NS" exec -i "$TRA_POD" -- python3 - "$PROBE_USER" "$PROBE_PW" <<'PYEOF' 2>&1 | tee -a "$LOG_FILE" | grep -q "MASKING_OK"; then
import os, sys, trino
user, pw = sys.argv[1], sys.argv[2]
c = trino.dbapi.connect(host=os.environ["TRINO_HOST"], port=int(os.environ["TRINO_PORT"]),
    user=user, http_scheme="https", verify=False,
    auth=trino.auth.BasicAuthentication(user, pw))
cur = c.cursor()
res = {}
for t in ("access_test_l1", "access_test_l2"):
    cur.execute(f"select phone, email, id_card from iceberg.demo.{t} order by customer_id limit 1")
    res[t] = cur.fetchall()[0]
    print(f"  {t}: phone={res[t][0]!r} email={res[t][1]!r} id_card={res[t][2]!r}")

# 行级过滤:analyst001 是"数据分析组",应该只看到那 2 行
cur.execute("select region, department from iceberg.demo.regional_sales order by region")
rows = cur.fetchall()
print(f"  regional_sales 看到 {len(rows)} 行: {rows}")

l1, l2 = res["access_test_l1"], res["access_test_l2"]
problems = []
depts = {r[1] for r in rows}
if len(rows) != 2:
    problems.append(f"行级过滤:应该只看到本部门的 2 行,实际 {len(rows)} 行")
if depts != {"数据分析组"}:
    problems.append(f"行级过滤:看到了别的部门的行 {depts}")
if {r[0] for r in rows} != {"华东", "华南"}:
    problems.append(f"行级过滤:本部门的行不全或者串了 {[r[0] for r in rows]}")
# l1(1 级):三个字段都够不到门槛,应该全打码
if l1[0] == "13812345678": problems.append("l1 的 phone 是明文,没脱敏")
if l1[1] == "alice@example.com": problems.append("l1 的 email 是明文,没脱敏")
if l1[2] == "110101199001011234": problems.append("l1 的 id_card 是明文,没脱敏")
# l2(2 级):phone/email 门槛是 2,够了 → 明文;id_card 门槛是 3 → 仍然打码
if l2[0] != "13812345678": problems.append(f"l2 的 phone 应该是明文,实际 {l2[0]!r} —— 分级没生效,退化成一刀切")
if l2[1] != "alice@example.com": problems.append(f"l2 的 email 应该是明文,实际 {l2[1]!r}")
if l2[2] == "110101199001011234": problems.append("l2 的 id_card 是明文,但它的门槛是 3 级,应该还打着码")
if problems:
    for p in problems: print("  !! " + p)
    sys.exit(1)
print("MASKING_OK")
PYEOF
  log "通过:1 级看到的是打码值,2 级 phone/email 恢复明文而 id_card 仍打码——**分级真的在起作用**。"
else
  log "!! 失败,上面列出了具体哪一项不符合预期。"
  exit 1
fi
