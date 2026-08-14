"""
权限自助申请门户——见 docs/decisions/032-permission-request-app.md 和
docs/decisions/044-tiered-approval-workflow.md(表访问分级审批,ADR-040
需求2 Phase 2)。

单文件 Flask app,故意不用前端框架(轻量级申请是这个门户的定位,不是要做
一个成熟的开发者门户,那条路线评估过 Backstage 之类的工具,判断不划算,
见同一份 ADR)。

身份来自 oauth2-proxy 挡在前面之后传进来的请求头:
- X-Forwarded-User / X-Forwarded-Email:oauth2-proxy 默认就传(legacy
  config 格式下不需要额外配置)。
- X-Forwarded-Access-Token:需要在 oauth2-proxy 配置里显式开
  `pass_access_token = true` 才会有——这个 app 自己把这个 JWT 的 payload
  解出来读 `groups` claim,不校验签名(信任边界是"这个 app 的 Service
  只应该被 oauth2-proxy 代理到",和这个项目其他信任反向代理注入的请求头
  是同一个模型,没有引入新的信任假设)。之所以要自己解 JWT 而不是让
  oauth2-proxy 直接传一个 X-Forwarded-Groups 请求头,是因为 legacy config
  格式没有这个能力(只有更啰嗦的 alpha config 格式支持自定义请求头映射,
  ADR-019 当初决定 legacy 格式够用,不想为了这一个组件推翻那个决定,所以
  绕过去自己解 token)。

表访问分级审批这部分范围边界(ADR-044 详细记录):这里只做"决策与留痕"
——按 ADR-040 原文规则算出谁要审批、记录每一步的批准/拒绝,最终写一份
`table-access-grants.csv` 进 git。**不做**真正的 Trino 查询拦截,那是
Trino OPA 细粒度权限(ADR-028"后续")的独立工作,现在还没有任何执行引擎
去消费这份 grants 数据。
"""
import base64
import csv
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, abort, redirect, render_template_string, request, url_for

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "/data/requests.db")
REPO_URL = os.environ.get("REPO_URL", "https://github.com/hardstuding/bigdata_ml_paltform.git")
GIT_TOKEN = os.environ.get("GIT_TOKEN", "")

# platform-team 不放进来:自助申请不能让人给自己批平台管理员权限,这个组
# 只能走 platform/iam/groups.yaml + memberships.csv 手动改 + PR review。
AVAILABLE_GROUPS = ["data-analysts", "algorithm-team", "viewers"]
APPROVER_GROUP = "platform-team"

# 表访问分级审批用到的组织架构数据(虚拟占位,见 platform/iam/employees.csv
# 文件头注释和 ADR-044——以后接公司真实 HR 数据,只要保持列名一致直接换
# 文件内容,这里的代码不用改)。挂载方式和 app.py 本身一样,是同一个
# ConfigMap 里的另一个 key,见 manifests/app-configmap.yaml。
EMPLOYEES_PATH = os.environ.get("EMPLOYEES_PATH", "/src/employees.csv")

# L3(安全等级3)的"指定负责人"不是从组织架构推导出来的——ADR-040 原文
# 这条本来就是"指定的人",不是职级+N 这种可以从汇报线算出来的角色。
DESIGNATED_ADMIN = os.environ.get("DESIGNATED_ADMIN", "admin")

# 【可插拔基础设施,见 ADR-030】和 table-registration-app 读同一个
# OpenMetadata 实例,查表的 SecurityLevel Tag 和 Owner 用。
OPENMETADATA_URL = os.environ.get("OPENMETADATA_URL", "http://openmetadata.openmetadata.svc.cluster.local:8585")
OPENMETADATA_TOKEN = os.environ.get("OPENMETADATA_TOKEN", "")

APPROVAL_ROLE_LABELS = {
    "manager": "直属上级",
    "manager_manager": "上级的上级",
    "table_owner": "表负责人",
    "designated_admin": "指定管理员(L3)",
}


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            group_name TEXT NOT NULL,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_at TEXT NOT NULL,
            decided_by TEXT,
            decided_at TEXT,
            note TEXT
        )
        """
    )
    # 表访问申请:主表只存申请本身的信息,谁要审批、审到哪一步全部在
    # approval_steps 里,不是简单的一对一状态,是"分级链"。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS table_access_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            table_fqn TEXT NOT NULL,
            security_level INTEGER NOT NULL,
            table_owner TEXT,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_at TEXT NOT NULL,
            decided_at TEXT,
            note TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES table_access_requests(id),
            step_order INTEGER NOT NULL,
            approver_role TEXT NOT NULL,
            approver_username TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            decided_at TEXT,
            UNIQUE(request_id, step_order, approver_username)
        )
        """
    )
    conn.commit()
    conn.close()


def get_current_user():
    username = request.headers.get("X-Forwarded-User", "")
    access_token = request.headers.get("X-Forwarded-Access-Token", "")
    groups = []
    if access_token and access_token.count(".") == 2:
        try:
            payload_b64 = access_token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload_b64))
            groups = claims.get("groups", [])
            username = claims.get("preferred_username", username)
        except Exception:
            pass
    return username, groups


def is_approver(groups):
    return APPROVER_GROUP in groups


def apply_to_git(username: str, group_name: str):
    """把这条申请写进 platform/iam/memberships.csv 并 push。没配 GIT_TOKEN
    (还没让用户去 GitHub 建一个 fine-grained PAT 塞进 Secret 之前的状态)
    就不硬失败,退化成"告诉你要手动加哪一行",申请记录仍然是"批准"状态,
    只是应用这一步需要人补一刀——好过用户点了批准却完全没反应、以为是
    bug。见 ADR-032。"""
    new_line = f"{username},{group_name}"
    if not GIT_TOKEN:
        return False, f"没有配置 git 写权限(GIT_TOKEN 未设置),请手动把这行加进 platform/iam/memberships.csv 并 push:{new_line}"

    tmpdir = tempfile.mkdtemp()
    try:
        auth_url = REPO_URL.replace("https://", f"https://{GIT_TOKEN}@")
        subprocess.run(
            ["git", "clone", "--depth", "1", auth_url, tmpdir],
            check=True, capture_output=True, text=True, timeout=60,
        )
        csv_path = Path(tmpdir) / "platform" / "iam" / "memberships.csv"
        lines = [l for l in csv_path.read_text().splitlines() if l.strip()]
        if new_line in lines:
            return True, "已经在 memberships.csv 里了,不用重复加"
        lines.append(new_line)
        csv_path.write_text("\n".join(lines) + "\n")

        subprocess.run(["git", "-C", tmpdir, "config", "user.email", "permission-request-app@platform.local"], check=True)
        subprocess.run(["git", "-C", tmpdir, "config", "user.name", "Permission Request App"], check=True)
        subprocess.run(["git", "-C", tmpdir, "add", "platform/iam/memberships.csv"], check=True)
        subprocess.run(
            ["git", "-C", tmpdir, "commit", "-m", f"iam: {username} 加入 {group_name}(自助申请门户批准)"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(["git", "-C", tmpdir, "push"], check=True, capture_output=True, text=True, timeout=60)
        return True, "已提交进 git,几分钟内 iam-sync CronJob(ADR-031)会同步进 Keycloak"
    except subprocess.CalledProcessError as e:
        stderr = e.stderr if hasattr(e, "stderr") else str(e)
        return False, f"git 操作失败,请人工处理:{stderr}\n应该加的这行:{new_line}"
    except subprocess.TimeoutExpired:
        return False, f"git 操作超时(可能是网络问题),请人工处理,应该加的这行:{new_line}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def apply_grant_to_git(username: str, table_fqn: str, security_level: int):
    """审批全部通过后,把这条访问授权记录写进
    platform/iam/table-access-grants.csv——和 apply_to_git() 同一个
    clone/改文件/commit/push 模式,复用同一套 GIT_TOKEN。这份文件现在
    只是决策留痕,不会被任何东西读去真正拦截 Trino 查询(见模块顶部的
    范围边界说明)。"""
    now = datetime.now(timezone.utc).isoformat()
    new_line = f"{username},{table_fqn},{security_level},{now},"
    if not GIT_TOKEN:
        return False, f"没有配置 git 写权限(GIT_TOKEN 未设置),请手动把这行加进 platform/iam/table-access-grants.csv 并 push:{new_line}"

    tmpdir = tempfile.mkdtemp()
    try:
        auth_url = REPO_URL.replace("https://", f"https://{GIT_TOKEN}@")
        subprocess.run(
            ["git", "clone", "--depth", "1", auth_url, tmpdir],
            check=True, capture_output=True, text=True, timeout=60,
        )
        csv_path = Path(tmpdir) / "platform" / "iam" / "table-access-grants.csv"
        header = "username,table_fqn,security_level,granted_at,expires_at"
        if csv_path.exists():
            lines = [l for l in csv_path.read_text().splitlines() if l.strip()]
        else:
            lines = [header]
        lines.append(new_line)
        csv_path.write_text("\n".join(lines) + "\n")

        subprocess.run(["git", "-C", tmpdir, "config", "user.email", "permission-request-app@platform.local"], check=True)
        subprocess.run(["git", "-C", tmpdir, "config", "user.name", "Permission Request App"], check=True)
        subprocess.run(["git", "-C", tmpdir, "add", "platform/iam/table-access-grants.csv"], check=True)
        subprocess.run(
            ["git", "-C", tmpdir, "commit", "-m", f"iam: {username} 获批访问 {table_fqn}(分级审批通过)"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(["git", "-C", tmpdir, "push"], check=True, capture_output=True, text=True, timeout=60)
        return True, "已提交进 git(platform/iam/table-access-grants.csv)"
    except subprocess.CalledProcessError as e:
        stderr = e.stderr if hasattr(e, "stderr") else str(e)
        return False, f"git 操作失败,请人工处理:{stderr}\n应该加的这行:{new_line}"
    except subprocess.TimeoutExpired:
        return False, f"git 操作超时(可能是网络问题),请人工处理,应该加的这行:{new_line}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def load_employees():
    """读 employees.csv,返回 {username: {"name":..., "manager_username":...}}。
    manager_id 是自关联外键,这里解成直接可用的 manager_username,调用方
    不用自己再去查一次 employee_id -> username 的映射。"""
    result = {}
    if not Path(EMPLOYEES_PATH).exists():
        return result
    with open(EMPLOYEES_PATH, newline="") as f:
        rows = list(csv.DictReader(f))
    by_id = {r["employee_id"]: r for r in rows}
    for r in rows:
        manager_username = None
        manager_id = (r.get("manager_id") or "").strip()
        if manager_id and manager_id in by_id:
            manager_username = by_id[manager_id]["username"]
        result[r["username"]] = {"name": r.get("name", ""), "manager_username": manager_username}
    return result


def get_manager_chain(username: str, levels: int = 2):
    """返回 [直属上级, 上级的上级, ...],最多 levels 个,遇到断链(没有上级
    数据,比如顶层或者这个用户压根不在 employees.csv 里)就提前结束——
    不是报错,是"这一级审批人是空的",路由逻辑那边会跳过。"""
    employees = load_employees()
    chain = []
    current = username
    for _ in range(levels):
        info = employees.get(current)
        if not info or not info["manager_username"]:
            break
        chain.append(info["manager_username"])
        current = info["manager_username"]
    return chain


def om_request(method: str, path: str):
    resp = requests.request(
        method, f"{OPENMETADATA_URL}{path}",
        headers={"Authorization": f"Bearer {OPENMETADATA_TOKEN}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else None


def lookup_table_governance(table_fqn: str):
    """查 OpenMetadata 里这张表的安全等级和负责人。table_fqn 是完整 FQN
    (比如 trino.iceberg.demo.orders,和 table-registration-app 建表时
    用的 databaseSchema 前缀拼法一致)。查不到/没配 token 时返回
    (None, None),调用方要处理这种情况,不能假设一定查得到。"""
    if not OPENMETADATA_TOKEN:
        return None, None
    try:
        data = om_request("GET", f"/api/v1/tables/name/{table_fqn}?fields=owners,tags")
    except requests.HTTPError:
        return None, None
    security_level = None
    for tag in data.get("tags", []) or []:
        tag_fqn = tag.get("tagFQN", "")
        if tag_fqn.startswith("SecurityLevel.Level"):
            try:
                security_level = int(tag_fqn.rsplit("Level", 1)[1])
            except ValueError:
                pass
    owner_username = None
    for o in (data.get("owners") or []):
        if o.get("type") == "user":
            owner_username = o.get("name")
            break
    # register_table_in_openmetadata() 里,owner 从没登录过 OM 时会降级
    # 写进 extension 字段而不是 owners 关联(见 table-registration-app),
    # 这里同样兜底读一次,不然那种表永远走不出"查不到负责人"的死路。
    ext = data.get("extension") or {}
    if security_level is None:
        security_level = ext.get("securityLevel")
    if owner_username is None:
        owner_username = ext.get("registeredOwner")
    return security_level, owner_username


def build_approval_steps(applicant_username: str, table_owner: str, security_level: int):
    """按 ADR-040 原文规则(L2 在 L1 基础上叠加,不是只要 +2)算出这次申请
    需要哪几级、每级谁来审。返回 [(step_order, role, username), ...]。

    同一个人可能在一级里身兼多个角色(比如申请人的直属上级正好也是这张表
    的负责人),这种情况只记一行,不用同一个人对同一级批两次。"""
    chain = get_manager_chain(applicant_username, levels=2)
    manager = chain[0] if len(chain) >= 1 else None
    manager_manager = chain[1] if len(chain) >= 2 else None

    levels = []
    l1 = []
    if manager:
        l1.append(("manager", manager))
    if table_owner:
        l1.append(("table_owner", table_owner))
    levels.append(l1)

    if security_level >= 2:
        l2 = list(l1)
        if manager_manager:
            l2.append(("manager_manager", manager_manager))
        levels.append(l2)

    if security_level >= 3:
        l3 = list(levels[-1])
        l3.append(("designated_admin", DESIGNATED_ADMIN))
        levels.append(l3)

    result = []
    for step_order, approvers in enumerate(levels, start=1):
        seen = set()
        for role, uname in approvers:
            if uname in seen:
                continue
            seen.add(uname)
            result.append((step_order, role, uname))
    return result


def current_step_order(conn, request_id: int):
    """这个申请现在卡在第几级——第一个还有 pending 行的 step_order。全部
    批完了返回 None。"""
    row = conn.execute(
        "SELECT MIN(step_order) AS s FROM approval_steps WHERE request_id=? AND status='pending'",
        (request_id,),
    ).fetchone()
    return row["s"]


def finalize_table_request_if_done(conn, request_id: int):
    """一个 approval_steps 行状态变化后调用:检查这个申请是不是该终态了
    (全部批准,或者有任何一步被拒)。是的话更新 table_access_requests 的
    status,是"approved"的话顺便写 grants.csv。"""
    rows = conn.execute(
        "SELECT status FROM approval_steps WHERE request_id=?", (request_id,)
    ).fetchall()
    statuses = [r["status"] for r in rows]
    if "rejected" in statuses:
        conn.execute(
            "UPDATE table_access_requests SET status='rejected', decided_at=? WHERE id=? AND status='pending'",
            (datetime.now(timezone.utc).isoformat(), request_id),
        )
        # 已经被拒了,其余还没审的步骤没有意义了,标成 skipped 避免审批人
        # 列表里一直挂着一条其实已经作废的待办。
        conn.execute(
            "UPDATE approval_steps SET status='skipped' WHERE request_id=? AND status='pending'",
            (request_id,),
        )
        return
    if all(s == "approved" for s in statuses):
        req = conn.execute("SELECT * FROM table_access_requests WHERE id=?", (request_id,)).fetchone()
        ok, note = apply_grant_to_git(req["username"], req["table_fqn"], req["security_level"])
        conn.execute(
            "UPDATE table_access_requests SET status='approved', decided_at=?, note=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), note, request_id),
        )


TEMPLATE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>平台权限申请</title>
<style>
  body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 980px; margin: 40px auto; padding: 0 20px; color: #222; }
  h1 { font-size: 1.4em; } h2 { font-size: 1.1em; margin-top: 2em; border-bottom: 1px solid #eee; padding-bottom: 6px; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 0.92em; }
  th, td { border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }
  th { background: #fafafa; }
  .status-pending { color: #b8860b; } .status-applied, .status-approved { color: #228b22; }
  .status-rejected { color: #b22222; } .status-approved_pending_apply { color: #ff8c00; }
  form.inline { display: inline; margin-right: 4px; }
  button { cursor: pointer; padding: 4px 10px; }
  .hint { color: #888; font-size: 0.85em; }
  select, input[type=text] { padding: 5px; margin-right: 6px; }
  .steps { margin: 0; padding-left: 18px; font-size: 0.9em; }
  .steps li.done { color: #228b22; } .steps li.rejected { color: #b22222; } .steps li.waiting { color: #b8860b; }
  .steps li.future { color: #aaa; }
</style>
</head>
<body>
<h1>平台权限申请</h1>
<p>当前登录:<b>{{ username }}</b>{% if is_approver %} <span class="hint">(你在 platform-team,可以审批组权限申请)</span>{% endif %}</p>

<h2>提交新申请:加入某个组</h2>
<form method="post" action="{{ url_for('submit_request') }}">
  <select name="group_name" required>
    {% for g in available_groups %}<option value="{{ g }}">{{ g }}</option>{% endfor %}
  </select>
  <input type="text" name="reason" placeholder="申请理由(可选)" size="40">
  <button type="submit">提交申请</button>
</form>
<p class="hint">platform-team(平台管理员)不能自助申请,需要走 git PR 人工 review,见 platform/iam/groups.yaml。</p>

<h2>我的申请记录</h2>
<table>
<tr><th>ID</th><th>申请的组</th><th>理由</th><th>状态</th><th>申请时间</th></tr>
{% for r in my_requests %}
<tr>
<td>{{ r.id }}</td><td>{{ r.group_name }}</td><td>{{ r.reason or '' }}</td>
<td class="status-{{ r.status }}">{{ r.status }}{% if r.note %}<br><span class="hint">{{ r.note }}</span>{% endif %}</td>
<td>{{ r.requested_at }}</td>
</tr>
{% else %}
<tr><td colspan="5" class="hint">还没提交过申请</td></tr>
{% endfor %}
</table>

{% if is_approver %}
<h2>待审批:组权限申请({{ pending|length }} 条)</h2>
<table>
<tr><th>ID</th><th>申请人</th><th>申请的组</th><th>理由</th><th>时间</th><th>操作</th></tr>
{% for r in pending %}
<tr>
<td>{{ r.id }}</td><td>{{ r.username }}</td><td>{{ r.group_name }}</td><td>{{ r.reason or '' }}</td><td>{{ r.requested_at }}</td>
<td>
<form class="inline" method="post" action="{{ url_for('approve', req_id=r.id) }}"><button type="submit">批准</button></form>
<form class="inline" method="post" action="{{ url_for('reject', req_id=r.id) }}"><button type="submit">拒绝</button></form>
</td>
</tr>
{% else %}
<tr><td colspan="6" class="hint">没有待审批的申请</td></tr>
{% endfor %}
</table>
{% endif %}

<h2>提交新申请:表访问(分级审批)</h2>
<form method="post" action="{{ url_for('submit_table_access') }}">
  <input type="text" name="table_fqn" placeholder="完整表名,比如 trino.iceberg.demo.orders" size="45" required>
  <input type="text" name="reason" placeholder="申请理由(可选)" size="30">
  <button type="submit">提交申请</button>
</form>
<p class="hint">安全等级和负责人从 OpenMetadata 里这张表的登记信息读取(建表注册工具回写的,见 ADR-043);审批链按 ADR-040 规则算,见 <a href="{{ url_for('table_access_help') }}">怎么用这个流程</a>。</p>

<h2>我的表访问申请</h2>
<table>
<tr><th>ID</th><th>表名</th><th>安全等级</th><th>状态</th><th>审批进度</th><th>时间</th></tr>
{% for r in my_table_requests %}
<tr>
<td>{{ r.id }}</td><td>{{ r.table_fqn }}</td><td>L{{ r.security_level }}</td>
<td class="status-{{ r.status }}">{{ r.status }}{% if r.note %}<br><span class="hint">{{ r.note }}</span>{% endif %}</td>
<td>
<ol class="steps">
{% for s in r.steps %}
<li class="{{ 'done' if s.status == 'approved' else ('rejected' if s.status == 'rejected' else ('waiting' if s.step_order == r.current_step else 'future')) }}">
第{{ s.step_order }}级 · {{ role_labels[s.approver_role] }}({{ s.approver_username }}):{{ s.status }}
</li>
{% endfor %}
</ol>
</td>
<td>{{ r.requested_at }}</td>
</tr>
{% else %}
<tr><td colspan="6" class="hint">还没提交过表访问申请</td></tr>
{% endfor %}
</table>

<h2>待我审批:表访问({{ my_actionable|length }} 条)</h2>
<table>
<tr><th>申请ID</th><th>申请人</th><th>表名</th><th>安全等级</th><th>我的角色</th><th>理由</th><th>操作</th></tr>
{% for s in my_actionable %}
<tr>
<td>{{ s.request_id }}</td><td>{{ s.username }}</td><td>{{ s.table_fqn }}</td><td>L{{ s.security_level }}</td>
<td>{{ role_labels[s.approver_role] }}</td><td>{{ s.reason or '' }}</td>
<td>
<form class="inline" method="post" action="{{ url_for('approve_table_step', step_id=s.step_id) }}"><button type="submit">批准</button></form>
<form class="inline" method="post" action="{{ url_for('reject_table_step', step_id=s.step_id) }}"><button type="submit">拒绝</button></form>
</td>
</tr>
{% else %}
<tr><td colspan="7" class="hint">没有轮到你审批的表访问申请</td></tr>
{% endfor %}
</table>
</body>
</html>
"""

HELP_TEMPLATE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>怎么用表访问审批流程</title>
<style>body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 760px; margin: 40px auto; padding: 0 20px; color: #222; line-height: 1.7; }
h1 { font-size: 1.3em; } code { background: #f5f5f5; padding: 1px 5px; border-radius: 3px; }</style>
</head><body>
<h1>怎么申请/审批表访问权限</h1>
<p><a href="{{ url_for('index') }}">&larr; 返回申请页</a></p>

<h2>申请人</h2>
<ol>
<li>先确认要访问的表已经通过建表注册工具登记过(有 owner 和安全等级),没登记过的表这里查不到,申请会失败。</li>
<li>填完整表名(比如 <code>trino.iceberg.demo.orders</code>)提交,系统会自动算出这张表要走几级审批、分别是谁审。</li>
<li>在"我的表访问申请"里能看到每一级的实时进度,轮到谁审、审完没有一目了然。</li>
</ol>

<h2>审批规则(见 ADR-040/044)</h2>
<ul>
<li><b>安全等级 1</b>:你的直属上级 + 表负责人,两个人都批准才算过。</li>
<li><b>安全等级 2</b>:在等级 1 的基础上,再加你上级的上级——是叠加,不是只要多一个人审。</li>
<li><b>安全等级 3</b>:在等级 2 的基础上,再加平台指定的管理员。</li>
</ul>
<p class="hint">上下级关系来自 <code>platform/iam/employees.csv</code>(目前是占位的虚拟组织架构,公司真实 HR 数据接入后会替换,规则和这里描述的一样)。</p>

<h2>审批人</h2>
<p>首页"待我审批"只会显示<b>轮到你</b>的申请——比如你是某张表的二级审批人,一级还没审完之前你在这里看不到这条申请,不用自己判断"是不是该我审了"。</p>

<h2>范围边界(如实说明,别误解)</h2>
<p>这套流程现在只负责<b>决策和留痕</b>——全部批准后会把这条授权记录写进
<code>platform/iam/table-access-grants.csv</code>,但<b>不会真的去拦截 Trino 查询</b>,
没批准也一样能连 Trino 查数据(细粒度权限执行是 Trino OPA 的独立工作,还没做)。
这是当前阶段刻意的范围收窄,不是 bug。</p>
</body></html>
"""


@app.route("/")
def index():
    username, groups = get_current_user()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    my_requests = conn.execute(
        "SELECT * FROM requests WHERE username=? ORDER BY id DESC", (username,)
    ).fetchall()
    pending = []
    approver = is_approver(groups)
    if approver:
        pending = conn.execute("SELECT * FROM requests WHERE status='pending' ORDER BY id").fetchall()

    my_table_requests_raw = conn.execute(
        "SELECT * FROM table_access_requests WHERE username=? ORDER BY id DESC", (username,)
    ).fetchall()
    my_table_requests = []
    for r in my_table_requests_raw:
        steps = conn.execute(
            "SELECT * FROM approval_steps WHERE request_id=? ORDER BY step_order, id", (r["id"],)
        ).fetchall()
        row = dict(r)
        row["steps"] = steps
        row["current_step"] = current_step_order(conn, r["id"])
        my_table_requests.append(row)

    my_actionable = conn.execute(
        """
        SELECT s.id AS step_id, s.approver_role, s.request_id, s.step_order,
               t.username, t.table_fqn, t.security_level, t.reason
        FROM approval_steps s
        JOIN table_access_requests t ON t.id = s.request_id
        WHERE s.approver_username = ?
          AND s.status = 'pending'
          AND s.step_order = (
              SELECT MIN(step_order) FROM approval_steps
              WHERE request_id = s.request_id AND status = 'pending'
          )
        ORDER BY s.request_id
        """,
        (username,),
    ).fetchall()
    conn.close()
    return render_template_string(
        TEMPLATE, username=username, available_groups=AVAILABLE_GROUPS,
        my_requests=my_requests, pending=pending, is_approver=approver,
        my_table_requests=my_table_requests, my_actionable=my_actionable,
        role_labels=APPROVAL_ROLE_LABELS,
    )


@app.route("/table-access/help")
def table_access_help():
    return render_template_string(HELP_TEMPLATE)


@app.route("/request", methods=["POST"])
def submit_request():
    username, _ = get_current_user()
    if not username:
        abort(401)
    group_name = request.form.get("group_name", "")
    if group_name not in AVAILABLE_GROUPS:
        abort(400)
    reason = request.form.get("reason", "")[:500]
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO requests (username, group_name, reason, requested_at) VALUES (?,?,?,?)",
        (username, group_name, reason, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/requests/<int:req_id>/approve", methods=["POST"])
def approve(req_id):
    username, groups = get_current_user()
    if not is_approver(groups):
        abort(403)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
    if not row or row["status"] != "pending":
        conn.close()
        abort(404)
    ok, note = apply_to_git(row["username"], row["group_name"])
    status = "applied" if ok else "approved_pending_apply"
    conn.execute(
        "UPDATE requests SET status=?, decided_by=?, decided_at=?, note=? WHERE id=?",
        (status, username, datetime.now(timezone.utc).isoformat(), note, req_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/requests/<int:req_id>/reject", methods=["POST"])
def reject(req_id):
    username, groups = get_current_user()
    if not is_approver(groups):
        abort(403)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE requests SET status='rejected', decided_by=?, decided_at=? WHERE id=? AND status='pending'",
        (username, datetime.now(timezone.utc).isoformat(), req_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/table-access/request", methods=["POST"])
def submit_table_access():
    username, _ = get_current_user()
    if not username:
        abort(401)
    table_fqn = request.form.get("table_fqn", "").strip()[:300]
    reason = request.form.get("reason", "")[:500]
    if not table_fqn:
        abort(400)

    security_level, table_owner = lookup_table_governance(table_fqn)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if security_level is None:
        conn.execute(
            "INSERT INTO table_access_requests (username, table_fqn, security_level, table_owner, reason, status, requested_at, note) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (username, table_fqn, 0, table_owner, reason, "rejected", datetime.now(timezone.utc).isoformat(),
             "在 OpenMetadata 里查不到这张表的安全等级(没登记过,或者 OPENMETADATA_TOKEN 没配置),请先用建表注册工具登记这张表"),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("index"))

    cur = conn.execute(
        "INSERT INTO table_access_requests (username, table_fqn, security_level, table_owner, reason, requested_at) "
        "VALUES (?,?,?,?,?,?)",
        (username, table_fqn, security_level, table_owner, reason, datetime.now(timezone.utc).isoformat()),
    )
    request_id = cur.lastrowid
    steps = build_approval_steps(username, table_owner, security_level)
    for step_order, role, approver_username in steps:
        conn.execute(
            "INSERT INTO approval_steps (request_id, step_order, approver_role, approver_username) VALUES (?,?,?,?)",
            (request_id, step_order, role, approver_username),
        )
    if not steps:
        # 组织架构里查不到申请人的任何上级、这张表也没有 owner——理论上
        # 不该发生(至少 table_owner 应该有),但真出现时不能让申请卡死
        # 在没有任何审批人的状态,直接拒绝并说明原因。
        conn.execute(
            "UPDATE table_access_requests SET status='rejected', note=? WHERE id=?",
            ("算不出任何审批人(申请人不在组织架构里,表也没有负责人),请联系平台管理员手动处理", request_id),
        )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/table-access/step/<int:step_id>/approve", methods=["POST"])
def approve_table_step(step_id):
    username, _ = get_current_user()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    step = conn.execute("SELECT * FROM approval_steps WHERE id=?", (step_id,)).fetchone()
    if not step or step["status"] != "pending" or step["approver_username"] != username:
        conn.close()
        abort(403)
    if current_step_order(conn, step["request_id"]) != step["step_order"]:
        # 前面还有没审完的级别,不该轮到这一级——正常 UI 不会出现这个按钮,
        # 但直接 POST 绕过前端的话要兜底拒绝,不能让后面的级别插队先批。
        conn.close()
        abort(409)
    conn.execute(
        "UPDATE approval_steps SET status='approved', decided_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), step_id),
    )
    finalize_table_request_if_done(conn, step["request_id"])
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/table-access/step/<int:step_id>/reject", methods=["POST"])
def reject_table_step(step_id):
    username, _ = get_current_user()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    step = conn.execute("SELECT * FROM approval_steps WHERE id=?", (step_id,)).fetchone()
    if not step or step["status"] != "pending" or step["approver_username"] != username:
        conn.close()
        abort(403)
    conn.execute(
        "UPDATE approval_steps SET status='rejected', decided_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), step_id),
    )
    finalize_table_request_if_done(conn, step["request_id"])
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
