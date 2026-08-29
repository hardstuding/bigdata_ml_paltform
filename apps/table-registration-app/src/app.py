"""
建表注册工具——权限 OA 审批系统 Phase 1。见
docs/decisions/043-table-registration-tool.md 和 docs/decisions/040-
enterprise-governance-roadmap.md 需求 1("建表工具:明确表负责人、安全
等级等")。

范围(刻意收窄,不做的部分留给以后的权限 OA 系统):只做"提交建表请求 ->
通过 Trino 真实建表 -> 把负责人/安全等级回写进 OpenMetadata"这一条链路,
不做分级审批(1/2/3 级现在只是记录下来,给以后的审批链路当数据基础,这次
不拦截任何人提交)。

架构上和 apps/permission-request-app/ 是同类"薄自建门户"(单文件 Flask +
ConfigMap 挂源码 + python:3.12-slim 装依赖,不建镜像仓库),但独立成一个
组件而不是塞进那个 app 里——两者是不同的治理动作(权限申请 vs 建表治理),
依赖也不同(这个要连 Trino + OpenMetadata,那个要连 git),符合"组件独立
可升级"的架构原则(architecture.md 原则 3)。

身份:oauth2-proxy 挡在前面,只取 X-Forwarded-User 当默认负责人(可在表单
里改成别人),不需要 groups/JWT 解析——这次没有审批门槛,任何登录用户都能
提交,和 permission-request-app 的"谁都能申请"是同一个设计取向(ADR-032)。
"""
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import requests
import trino
from flask import Flask, abort, redirect, render_template_string, request, url_for
from trino.auth import BasicAuthentication

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "/data/registrations.db")

TRINO_HOST = os.environ.get("TRINO_HOST", "trino.trino.svc.cluster.local")
TRINO_PORT = int(os.environ.get("TRINO_PORT", "8443"))
TRINO_USER = os.environ.get("TRINO_USER", "table_registration_service")
TRINO_PASSWORD = os.environ.get("TRINO_PASSWORD", "")

# 【可插拔基础设施,见 ADR-030】OpenMetadata 的 host/端口是独立环境变量,换成
# 外部 OpenMetadata 实例直接改这两个;OPENMETADATA_TOKEN 是敏感凭据,和
# permission-request-app 的 GIT_TOKEN 同一个模式——不自动生成,需要管理员在
# OpenMetadata 里手动建一个 bot(Settings -> Bots -> Add Bot)生成 JWT token
# 后塞进 Secret,没配之前 OpenMetadata 回写这一步会跳过(表还是能正常建),
# 页面上会提示。
OPENMETADATA_URL = os.environ.get("OPENMETADATA_URL", "http://openmetadata.openmetadata.svc.cluster.local:8585")
OPENMETADATA_TOKEN = os.environ.get("OPENMETADATA_TOKEN", "")

DEFAULT_CATALOG = "iceberg"
SECURITY_LEVELS = [1, 2, 3]

IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
# Trino 常见类型的白名单(可选精度/刻度),不是全量覆盖,够 demo/日常建表用。
TYPE_RE = re.compile(
    r"^(VARCHAR|CHAR|BIGINT|INTEGER|INT|SMALLINT|TINYINT|DOUBLE|REAL|BOOLEAN|DATE|TIMESTAMP)"
    r"(\(\d+(,\s*\d+)?\))?$",
    re.IGNORECASE,
)

# Trino 类型 -> OpenMetadata Column.dataType 枚举的粗略映射,查不到就退化成
# VARCHAR(OpenMetadata 这个字段只是给目录展示用,不影响 Trino 里真实的表结构,
# 映射不精确不是阻塞性问题)。
OM_TYPE_MAP = {
    "VARCHAR": "VARCHAR", "CHAR": "CHAR", "BIGINT": "BIGINT", "INTEGER": "INT",
    "INT": "INT", "SMALLINT": "SMALLINT", "TINYINT": "TINYINT", "DOUBLE": "DOUBLE",
    "REAL": "FLOAT", "BOOLEAN": "BOOLEAN", "DATE": "DATE", "TIMESTAMP": "TIMESTAMP",
}


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requested_by TEXT NOT NULL,
            table_fqn TEXT NOT NULL,
            owner TEXT NOT NULL,
            security_level INTEGER NOT NULL,
            columns_raw TEXT NOT NULL,
            trino_status TEXT NOT NULL,
            openmetadata_status TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def get_current_user():
    return request.headers.get("X-Forwarded-User", "") or request.headers.get("X-Forwarded-Email", "")


def parse_columns(raw: str):
    """每行 `列名 类型`,比如 `order_id BIGINT`。抛 ValueError 说明校验失败的
    具体原因(直接显示给用户,不是笼统的 400)。"""
    columns = []
    for line_no, line in enumerate(raw.strip().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise ValueError(f"第 {line_no} 行格式不对(应该是`列名 类型`):{line}")
        name, dtype = parts[0], parts[1].strip().upper()
        if not IDENT_RE.match(name):
            raise ValueError(f"第 {line_no} 行列名不合法:{name}")
        if not TYPE_RE.match(dtype):
            raise ValueError(f"第 {line_no} 行类型不在支持列表里:{dtype}")
        columns.append((name, dtype))
    if not columns:
        raise ValueError("至少要有一列")
    return columns


def parse_table_fqn(fqn: str):
    parts = fqn.strip().split(".")
    if len(parts) == 2:
        catalog, schema, table = DEFAULT_CATALOG, parts[0], parts[1]
    elif len(parts) == 3:
        catalog, schema, table = parts
    else:
        raise ValueError("表名要写成 `schema.table` 或 `catalog.schema.table`")
    for part in (catalog, schema, table):
        if not IDENT_RE.match(part):
            raise ValueError(f"表名里有不合法的部分:{part}")
    return catalog, schema, table


def create_table_in_trino(catalog: str, schema: str, table: str, columns):
    if not TRINO_PASSWORD:
        raise RuntimeError("TRINO_PASSWORD 没配置,table_registration_service 这个服务账号密码没读到")
    conn = trino.dbapi.connect(
        host=TRINO_HOST, port=TRINO_PORT, user=TRINO_USER,
        http_scheme="https", verify=False,
        auth=BasicAuthentication(TRINO_USER, TRINO_PASSWORD),
        catalog=catalog,
    )
    cur = conn.cursor()
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
    cur.fetchall()
    col_defs = ", ".join(f"{name} {dtype}" for name, dtype in columns)
    cur.execute(f"CREATE TABLE IF NOT EXISTS {catalog}.{schema}.{table} ({col_defs})")
    cur.fetchall()
    conn.close()


def om_request(method: str, path: str, json_body=None):
    resp = requests.request(
        method, f"{OPENMETADATA_URL}{path}",
        headers={"Authorization": f"Bearer {OPENMETADATA_TOKEN}"},
        json=json_body, timeout=15,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else None


def ensure_table_custom_properties():
    """幂等地在 OpenMetadata 的 Table 实体类型上注册 registeredOwner(字符串)
    /securityLevel(整数)这两个自定义属性——`extension` 字段不是自由 JSON
    (2026-08-15 实测踩到的真实坑:第一次真正打通写权限之后,PUT /api/v1/
    tables 报 `400 Unknown custom field registeredOwner`,才发现 OpenMetadata
    要求 extension 里用到的每个字段必须先在实体类型上登记过,不是随便塞
    键值对就行)。

    注册用的是 `PATCH .../metadata/types/{id}` + JSON Patch `add` 操作,不是
    PUT——PUT 整个类型对象会报 `Invalid request format`。`propertyType`
    这个引用必须同时带 `id`/`type`/`name` 三个字段,只给 `id`+`type` 会在
    服务端抛 NPE(`Cannot invoke "Object.hashCode()" because "key" is null`,
    实测确认,不是文档写的)。"""
    table_type = om_request("GET", "/api/v1/metadata/types/name/table")
    existing = {p.get("name") for p in (table_type.get("customProperties") or [])}
    to_add = [
        ("registeredOwner", "建表注册工具记录的负责人(OpenMetadata owner 关联查不到时的降级字段)",
         "c09a54a2-583b-4662-a37a-a0146fd32568", "string"),
        ("securityLevel", "建表注册工具记录的数据安全等级(1/2/3)",
         "c68083c5-bc02-4422-ba94-bfe06d3d90ca", "integer"),
    ]
    for name, description, type_id, type_name in to_add:
        if name in existing:
            continue
        resp = requests.request(
            "PATCH", f"{OPENMETADATA_URL}/api/v1/metadata/types/{table_type['id']}",
            headers={"Authorization": f"Bearer {OPENMETADATA_TOKEN}", "Content-Type": "application/json-patch+json"},
            json=[{"op": "add", "path": "/customProperties/-", "value": {
                "name": name, "description": description,
                "propertyType": {"id": type_id, "type": "type", "name": type_name},
            }}],
            timeout=15,
        )
        resp.raise_for_status()


def ensure_om_hierarchy_and_tags():
    """幂等:createOrUpdate(PUT)语义,已存在就是更新,不存在就创建。"""
    om_request("PUT", "/api/v1/services/databaseServices", {
        "name": "trino", "serviceType": "Trino",
        "connection": {"config": {"type": "Trino", "hostPort": f"{TRINO_HOST}:{TRINO_PORT}", "scheme": "trino"}},
    })
    om_request("PUT", "/api/v1/databases", {"name": DEFAULT_CATALOG, "service": "trino"})
    try:
        om_request("PUT", "/api/v1/classifications", {
            "name": "SecurityLevel",
            "description": "数据安全等级(1/2/3)。等级决定申请这张表的权限要走几级审批,也决定敏感字段对谁脱敏。",   # 这段会显示在 OpenMetadata 界面上给使用者看,所以说的是"它有什么用",不是内部的 ADR 编号
        })
    except requests.HTTPError:
        pass  # 已存在时某些版本会 4xx,忽略,后面 tag 创建/引用不受影响
    for level in SECURITY_LEVELS:
        om_request("PUT", "/api/v1/tags", {
            "name": f"Level{level}", "classification": "SecurityLevel",
            "description": f"安全等级 {level}",
        })
    ensure_table_custom_properties()


def register_table_in_openmetadata(catalog: str, schema: str, table: str, columns, owner: str, security_level: int):
    ensure_om_hierarchy_and_tags()
    om_request("PUT", "/api/v1/databaseSchemas", {"name": schema, "database": f"trino.{catalog}"})

    owners = []
    try:
        user = om_request("GET", f"/api/v1/users/name/{owner}")
        if user and user.get("id"):
            owners = [{"id": user["id"], "type": "user"}]
    except requests.HTTPError:
        pass  # owner 还没在 OpenMetadata 里出现过(比如从没登录过 OM UI),先不挂 owner

    om_columns = []
    for name, dtype in columns:
        base = dtype.split("(")[0]
        om_type = OM_TYPE_MAP.get(base, "VARCHAR")
        col = {"name": name, "dataType": om_type}
        if om_type in ("VARCHAR", "CHAR", "BINARY", "VARBINARY"):
            # OpenMetadata 这几个类型强制要求 dataLength(2026-08-15 实测踩到的
            # 真实坑,`400 dataLength must not be null`)。有括号里的长度就用
            # 那个数字(Trino 侧真实建表用的也是这个值),没写就给个够用的默认值
            # ——这个字段只是给 OpenMetadata 目录展示用,不影响 Trino 里真实的
            # 列定义,不追求精确。
            m = re.search(r"\((\d+)", dtype)
            col["dataLength"] = int(m.group(1)) if m else 255
        om_columns.append(col)
    body = {
        "name": table,
        "databaseSchema": f"trino.{catalog}.{schema}",
        "columns": om_columns,
        "tags": [{
            "tagFQN": f"SecurityLevel.Level{security_level}",
            "source": "Classification", "labelType": "Manual", "state": "Confirmed",
        }],
        "extension": {"registeredOwner": owner, "securityLevel": security_level},
    }
    if owners:
        body["owners"] = owners
    om_request("PUT", "/api/v1/tables", body)
    if not owners:
        return f"表已登记进 OpenMetadata,但负责人 {owner} 还没在 OpenMetadata 里出现过(需要先用这个账号登录一次 OpenMetadata UI),owners 字段没挂上,安全等级/负责人信息已经记在 extension 里"
    return "表结构 + 负责人 + 安全等级已经登记进 OpenMetadata"


TEMPLATE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>建表注册</title>
<style>
  body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; color: #222; }
  h1 { font-size: 1.4em; } h2 { font-size: 1.1em; margin-top: 2em; border-bottom: 1px solid #eee; padding-bottom: 6px; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 0.9em; }
  th, td { border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }
  th { background: #fafafa; }
  textarea { width: 100%; height: 80px; font-family: monospace; }
  input[type=text], select { padding: 6px; margin: 4px 0; }
  .field { margin-bottom: 12px; }
  label { display: block; font-weight: bold; margin-bottom: 2px; }
  .hint { color: #888; font-size: 0.85em; }
  .ok { color: #228b22; } .err { color: #b22222; } .warn { color: #b8860b; }
  button { cursor: pointer; padding: 6px 16px; }
</style></head>
<body>
<h1>建表注册工具</h1>
<p>当前登录:<b>{{ username }}</b></p>
{# 这里原来写着"权限 OA 审批系统 Phase 1……见 ADR-043" —— 那是给我们自己
   看的内部术语,用这个工具建表的人不需要知道 Phase 几、也不会去翻 ADR。
   换成对他有用的两句:建完之后会发生什么。 #}
<p class="hint">在这里建的表会同时登记进数据目录(负责人、安全等级),
别人才能搜到它、才能对它发起权限申请。<b>直接在 Trino 里手写 DDL 建的表
不会被登记</b>,那样建出来的表在目录里是隐形的。</p>

<h2>提交建表请求</h2>
<form method="post" action="{{ url_for('submit') }}">
  <div class="field">
    <label>表名(schema.table 或 catalog.schema.table,不写 catalog 默认 iceberg)</label>
    <input type="text" name="table_fqn" placeholder="demo.my_table" size="50" required>
  </div>
  <div class="field">
    <label>列定义(每行一列:`列名 类型`)</label>
    <textarea name="columns" placeholder="order_id BIGINT&#10;customer_name VARCHAR&#10;amount DECIMAL(10,2)" required></textarea>
  </div>
  <div class="field">
    <label>负责人</label>
    <input type="text" value="{{ username }}" size="30" disabled>
    <p class="hint">负责人就是你(登录身份),不能改。表负责人是这张表访问申请的
      第一级审批人 —— 能随便填别人的话,就能给自己安排一个好说话的审批人,
      或者干脆填自己然后批自己。要代别人建表,先联系平台组。</p>
  </div>
  <div class="field">
    <label>安全等级</label>
    <select name="security_level">
      <option value="1">1 级(默认,低敏感)</option>
      <option value="2">2 级</option>
      <option value="3">3 级(高敏感)</option>
    </select>
  </div>
  <button type="submit">提交并建表</button>
</form>

<h2>我的建表记录</h2>
<table>
<tr><th>ID</th><th>表名</th><th>负责人</th><th>安全等级</th><th>Trino</th><th>OpenMetadata</th><th>时间</th></tr>
{% for r in my_registrations %}
<tr>
<td>{{ r.id }}</td><td>{{ r.table_fqn }}</td><td>{{ r.owner }}</td><td>{{ r.security_level }}</td>
<td class="{{ 'ok' if r.trino_status == 'ok' else 'err' }}">{{ r.trino_status }}</td>
<td class="{{ 'ok' if r.openmetadata_status == 'ok' else ('warn' if r.openmetadata_status == 'skipped' else 'err') }}">{{ r.openmetadata_status }}{% if r.note %}<br><span class="hint">{{ r.note }}</span>{% endif %}</td>
<td>{{ r.created_at }}</td>
</tr>
{% else %}
<tr><td colspan="7" class="hint">还没提交过</td></tr>
{% endfor %}
</table>
</body></html>
"""


@app.route("/")
def index():
    username = get_current_user()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    my_registrations = conn.execute(
        "SELECT * FROM registrations WHERE requested_by=? ORDER BY id DESC", (username,)
    ).fetchall()
    conn.close()
    return render_template_string(TEMPLATE, username=username, my_registrations=my_registrations)


@app.route("/submit", methods=["POST"])
def submit():
    username = get_current_user()
    if not username:
        abort(401)

    table_fqn = request.form.get("table_fqn", "")
    # **负责人只能是登录者本人,表单传什么都不看。**(2026-08-29)
    #
    # 这里原来是 `request.form.get("owner", username)` —— 一个自由填写的
    # 字段。而表负责人在 permission-request-app 里是**第一级审批人**
    # (build_approval_steps 的 table_owner 角色),所以"建表时把 owner
    # 填成自己 → 之后申请这张表的权限 → 自己批自己"这条路是通的;组织架构
    # 里查不到上级的人,这条链上甚至只有他一个人。
    #
    # permission-request-app 那边也加了兜底(申请人不能出现在自己的审批链
    # 里),两层都要有:那边防的是"不管 owner 怎么来的",这边防的是"一开始
    # 就不该能乱填"。
    #
    # 代他人建表这个需求是真的(平台组帮业务方建),但它需要判断"你是不是
    # 平台组",而那要 oauth2-proxy 传 access token + Keycloak client 加
    # groups scope —— 加 scope 这件事在 MLflow 上炸过一次(client 没配这个
    # scope,Keycloak 直接 invalid_scope,登录页都进不去)。所以单独做、
    # 单独实机验证,见 roadmap。
    owner = username[:200]
    try:
        security_level = int(request.form.get("security_level", "1"))
        if security_level not in SECURITY_LEVELS:
            raise ValueError
    except ValueError:
        security_level = 1

    trino_status, om_status, note = "pending", "pending", ""
    try:
        catalog, schema, table = parse_table_fqn(table_fqn)
        columns = parse_columns(request.form.get("columns", ""))
    except ValueError as e:
        trino_status, om_status, note = "rejected", "skipped", str(e)
        catalog = schema = table = None
        columns = []

    if catalog:
        try:
            create_table_in_trino(catalog, schema, table, columns)
            trino_status = "ok"
        except Exception as e:
            trino_status = "failed"
            note = f"Trino 建表失败:{e}"

        if trino_status == "ok":
            if not OPENMETADATA_TOKEN:
                om_status = "skipped"
                note = "没配置 OPENMETADATA_TOKEN,治理元数据没有回写,需要管理员在 OpenMetadata 建 bot 生成 token 后配置"
            else:
                try:
                    note = register_table_in_openmetadata(catalog, schema, table, columns, owner, security_level)
                    om_status = "ok"
                except Exception as e:
                    om_status = "failed"
                    note = f"OpenMetadata 回写失败(表本身已经建好了):{e}"

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO registrations (requested_by, table_fqn, owner, security_level, columns_raw, trino_status, openmetadata_status, note, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (username, table_fqn, owner, security_level, request.form.get("columns", ""),
         trino_status, om_status, note, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


# 对账 + 重试(roadmap P1.5「建表注册工具」的验收项之一)。
#
# **为什么这个半成功状态特别糟**:Trino 里表建好了、OpenMetadata 里没有,
# 于是这张表在数据目录里查不到、**也查不到安全等级** —— 而
# permission-request-app 查不到安全等级时会**直接拒绝**这张表的所有权限
# 申请。也就是说:表存在,但没有任何人能通过正常流程拿到它的权限,而且
# 谁也不会想到去建表工具的历史记录里翻那一行 `openmetadata_status=failed`。
#
# 触发方式和 permission-request-app 的 /internal/* 一样:共享密钥,
# 给 CronJob 调,不走 oauth2-proxy。
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "")


@app.route("/internal/reconcile-openmetadata", methods=["POST"])
def reconcile_openmetadata():
    """把所有"Trino 建好了但 OpenMetadata 没写进去"的记录重新回写一遍。

    幂等:成功的记录不会被重复处理;还是失败的原样留着等下一轮,并把最新
    的错误覆盖进 note —— 保留最新一次的失败原因,比留着第一次那条更有用。
    """
    if not INTERNAL_TOKEN or request.headers.get("X-Internal-Token") != INTERNAL_TOKEN:
        abort(403)
    if not OPENMETADATA_TOKEN:
        return {"retried": 0, "skipped": "没配置 OPENMETADATA_TOKEN"}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM registrations WHERE trino_status='ok' "
        "AND openmetadata_status IN ('failed','skipped') ORDER BY id"
    ).fetchall()

    fixed, still_failing = [], []
    for r in rows:
        try:
            catalog, schema, table = parse_table_fqn(r["table_fqn"])
            columns = parse_columns(r["columns_raw"])
            note = register_table_in_openmetadata(
                catalog, schema, table, columns, r["owner"], r["security_level"])
            conn.execute(
                "UPDATE registrations SET openmetadata_status='ok', note=? WHERE id=?",
                (f"对账时补写成功。{note}", r["id"]))
            fixed.append(r["table_fqn"])
        except Exception as e:   # noqa: BLE001 —— 一条失败不能影响其余的
            conn.execute(
                "UPDATE registrations SET openmetadata_status='failed', note=? WHERE id=?",
                (f"对账重试仍然失败:{e}", r["id"]))
            still_failing.append(r["table_fqn"])
    conn.commit()
    conn.close()
    return {"retried": len(rows), "fixed": fixed, "still_failing": still_failing}


@app.route("/internal/reconcile-status")
def reconcile_status():
    """有多少张表卡在"Trino 有、目录里没有"的半成功状态。

    单独开一个只读端点,是为了能拿它做告警指标 —— 这个数字长期不为零,
    意味着有表在目录里是隐形的,而不是"偶尔失败一次"。
    """
    if not INTERNAL_TOKEN or request.headers.get("X-Internal-Token") != INTERNAL_TOKEN:
        abort(403)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT table_fqn, openmetadata_status, note FROM registrations "
        "WHERE trino_status='ok' AND openmetadata_status IN ('failed','skipped')"
    ).fetchall()
    conn.close()
    return {"pending": len(rows),
            "tables": [{"table": r["table_fqn"], "status": r["openmetadata_status"],
                        "note": r["note"]} for r in rows]}


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
