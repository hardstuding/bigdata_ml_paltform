"""
权限自助申请门户——见 docs/decisions/032-permission-request-app.md。

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
"""
import base64
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, redirect, render_template_string, request, url_for

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "/data/requests.db")
REPO_URL = os.environ.get("REPO_URL", "https://github.com/hardstuding/bigdata_ml_paltform.git")
GIT_TOKEN = os.environ.get("GIT_TOKEN", "")

# platform-team 不放进来:自助申请不能让人给自己批平台管理员权限,这个组
# 只能走 platform/iam/groups.yaml + memberships.csv 手动改 + PR review。
AVAILABLE_GROUPS = ["data-analysts", "algorithm-team", "viewers"]
APPROVER_GROUP = "platform-team"


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


TEMPLATE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>平台权限申请</title>
<style>
  body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; color: #222; }
  h1 { font-size: 1.4em; } h2 { font-size: 1.1em; margin-top: 2em; border-bottom: 1px solid #eee; padding-bottom: 6px; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 0.92em; }
  th, td { border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }
  th { background: #fafafa; }
  .status-pending { color: #b8860b; } .status-applied { color: #228b22; }
  .status-rejected { color: #b22222; } .status-approved_pending_apply { color: #ff8c00; }
  form.inline { display: inline; margin-right: 4px; }
  button { cursor: pointer; padding: 4px 10px; }
  .hint { color: #888; font-size: 0.85em; }
  select, input[type=text] { padding: 5px; margin-right: 6px; }
</style>
</head>
<body>
<h1>平台权限申请</h1>
<p>当前登录:<b>{{ username }}</b>{% if is_approver %} <span class="hint">(你在 platform-team,可以审批)</span>{% endif %}</p>

<h2>提交新申请</h2>
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
<h2>待审批({{ pending|length }} 条)</h2>
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
</body>
</html>
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
    conn.close()
    return render_template_string(
        TEMPLATE, username=username, available_groups=AVAILABLE_GROUPS,
        my_requests=my_requests, pending=pending, is_approver=approver,
    )


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


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
