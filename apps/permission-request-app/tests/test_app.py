"""permission-request-app 的测试——见 docs/project/roadmap.md P1"三个自建 Flask
工具补测试"那条。这个 app 最大最复杂(1388 行),这次先覆盖最核心、最
容易出错的一块:build_approval_steps() 这套"按 ADR-040 规则算出谁要
审批"的业务逻辑,以及它依赖的 get_manager_chain()/load_employees()。

选这块优先测的原因:app.py 自己的注释里明确写着"2026-08-14 实测过第一版
实现漏了去重,L2/L3 会让 L1 的人重复批,这是修过的真实 bug"——这正是
"看代码顺眼但没有测试用例锁住行为,改动时容易复发"的典型场景,值得优先
补上回归测试,而不是泛泛地往每个路由都撒一点测试。

跑法:
  cd apps/permission-request-app && python3 -m pytest tests/ -v
"""
import csv
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# EMPLOYEES_PATH 在 import app 时就固定成环境变量的值,必须在 import 之前
# 指向测试自己的组织架构数据,不能读真实的 platform/iam/employees.csv
# (那份数据以后可能会改,测试不该依赖它的具体内容)。
_TMP_EMPLOYEES = tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", newline="")
_EMPLOYEE_ROWS = [
    # employee_id, username, name, email, department, title, manager_id
    ("E001", "ceo", "CEO", "ceo@example.com", "管理层", "CEO", ""),
    ("E002", "director", "总监", "director@example.com", "平台组", "总监", "E001"),
    ("E003", "manager1", "经理", "manager1@example.com", "数据组", "经理", "E002"),
    ("E004", "engineer1", "工程师1", "e1@example.com", "数据组", "工程师", "E003"),
    ("E005", "engineer2", "工程师2", "e2@example.com", "数据组", "工程师", "E003"),
    # engineer3 没有 manager_id,模拟"断链"情况
    ("E006", "engineer3", "工程师3", "e3@example.com", "数据组", "工程师", ""),
]
_writer = csv.writer(_TMP_EMPLOYEES)
_writer.writerow(["employee_id", "username", "name", "email", "department", "title", "manager_id"])
_writer.writerows(_EMPLOYEE_ROWS)
_TMP_EMPLOYEES.close()

os.environ["EMPLOYEES_PATH"] = _TMP_EMPLOYEES.name
os.environ["DB_PATH"] = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ["DESIGNATED_ADMIN"] = "admin"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import app as perm  # noqa: E402


class TestIsApprover:
    def test_true_when_in_approver_group(self):
        assert perm.is_approver(["platform-team"]) is True

    def test_false_when_not_in_approver_group(self):
        assert perm.is_approver(["data-analysts"]) is False

    def test_false_for_empty_groups(self):
        assert perm.is_approver([]) is False


class TestLoadEmployees:
    def test_resolves_manager_username_from_manager_id(self):
        employees = perm.load_employees()
        assert employees["engineer1"]["manager_username"] == "manager1"
        assert employees["manager1"]["manager_username"] == "director"
        assert employees["director"]["manager_username"] == "ceo"

    def test_top_of_chain_has_no_manager(self):
        employees = perm.load_employees()
        assert employees["ceo"]["manager_username"] is None

    def test_broken_manager_id_treated_as_no_manager(self):
        """engineer3 的 manager_id 是空字符串,不应该报错,应该当成没有上级。"""
        employees = perm.load_employees()
        assert employees["engineer3"]["manager_username"] is None


class TestGetManagerChain:
    def test_returns_two_levels_by_default(self):
        assert perm.get_manager_chain("engineer1") == ["manager1", "director"]

    def test_stops_early_when_chain_breaks(self):
        """engineer3 没有上级,链条长度应该是 0,不是报错或者返回 None。"""
        assert perm.get_manager_chain("engineer3") == []

    def test_stops_at_top_of_org(self):
        """director 的上级是 ceo,ceo 再往上没有人了,两级请求应该只返回一级。"""
        assert perm.get_manager_chain("director") == ["ceo"]

    def test_unknown_user_returns_empty(self):
        assert perm.get_manager_chain("someone-not-in-csv") == []

    def test_respects_levels_argument(self):
        assert perm.get_manager_chain("engineer1", levels=1) == ["manager1"]
        assert perm.get_manager_chain("engineer1", levels=3) == ["manager1", "director", "ceo"]


class TestBuildApprovalSteps:
    """核心回归测试:2026-08-14 真实修过的 bug 是"L2/L3 会让 L1 的人
    重复批",这里专门覆盖这个场景,不能再退回去。"""

    def test_level1_only_manager_and_owner(self):
        steps = perm.build_approval_steps("engineer1", "someone-else-owner", security_level=1)
        assert steps == [
            (1, "manager", "manager1"),
            (1, "table_owner", "someone-else-owner"),
        ]

    def test_level1_dedupes_when_manager_is_also_owner(self):
        """申请人的直属上级正好就是这张表的负责人,只应该出现一次,不是
        两行都要这个人批。"""
        steps = perm.build_approval_steps("engineer1", "manager1", security_level=1)
        assert steps == [(1, "manager", "manager1")]

    def test_level2_adds_manager_manager_without_repeating_level1(self):
        """这是当年那个真实 bug 的核心场景:L2 只应该新增"上级的上级"这一个
        人,不能让 L1 已经批过的 manager/table_owner 在 L2 再出现一次。"""
        steps = perm.build_approval_steps("engineer1", "owner-x", security_level=2)
        assert steps == [
            (1, "manager", "manager1"),
            (1, "table_owner", "owner-x"),
            (2, "manager_manager", "director"),
        ]

    def test_level2_dedupes_when_manager_manager_already_required(self):
        """"上级的上级"如果正好也是表负责人(已经在 L1 出现过),L2 就不应该
        再放一次。"""
        steps = perm.build_approval_steps("engineer1", "director", security_level=2)
        assert steps == [
            (1, "manager", "manager1"),
            (1, "table_owner", "director"),
        ]

    def test_level3_adds_designated_admin(self):
        steps = perm.build_approval_steps("engineer1", "owner-x", security_level=3)
        assert steps == [
            (1, "manager", "manager1"),
            (1, "table_owner", "owner-x"),
            (2, "manager_manager", "director"),
            (3, "designated_admin", "admin"),
        ]

    def test_level3_dedupes_designated_admin_if_already_required(self):
        steps = perm.build_approval_steps("engineer1", "admin", security_level=3)
        names_at_step1 = [s for s in steps if s[0] == 1]
        names_at_step3 = [s for s in steps if s[0] == 3]
        assert ("table_owner", "admin") in [(r, u) for _, r, u in names_at_step1]
        assert names_at_step3 == []  # admin 已经在 L1 出现过,L3 不应该再放一次

    def test_broken_chain_skips_missing_manager_manager(self):
        """director 只有一级上级(ceo),申请 L2 的话 manager_manager 这一
        级应该是空的(不是报错),因为链条断了。"""
        steps = perm.build_approval_steps("director", "owner-x", security_level=2)
        assert steps == [
            (1, "manager", "ceo"),
            (1, "table_owner", "owner-x"),
        ]

    def test_no_table_owner_provided(self):
        steps = perm.build_approval_steps("engineer1", "", security_level=1)
        assert steps == [(1, "manager", "manager1")]

    def test_applicant_with_no_manager_and_no_owner_gives_empty_level1(self):
        steps = perm.build_approval_steps("engineer3", "", security_level=1)
        assert steps == []


# ---------------------------------------------------------------------------
# 下面覆盖 /request/*、/table-access/* 这些路由的完整状态机(approve/reject/
# escalation/transfer/audit/external-callback)——docs/project/roadmap.md P1.2 里
# 明确标注过"还没测"的那部分。用 Flask test_client 走真实路由,不是直接
# 调内部函数,这样能顺带验证认证头解析/HTTP 状态码这些路由层面的行为,
# 不只是业务逻辑本身。
#
# 覆盖范围(2026-08-20 补完,见 docs/project/roadmap.md 2.4):默认的 client
# fixture 场景下 GIT_TOKEN 不设置,只测到"没配置时优雅降级,不崩溃"这
# 条路径。真正执行 git clone/commit/push 的分支(apply_to_git()/
# apply_grant_to_git()/reclaim_expired()/transfer())现在由下面
# TestGitWritePaths 这个类覆盖——用 local_git_repo fixture 起一个本地
# 裸仓库当 REPO_URL,不连真实 GitHub。外部 OA webhook 的真实 POST(成功
# 和失败两条分支)由 TestExternalOaWebhookDispatch 覆盖,mock
# perm.requests.post,不发真实网络请求。
def _reset_db():
    """每个测试前清空三张表,保证测试之间互不干扰(id 从 1 重新分配,
    不依赖上一个测试留下的行)。"""
    conn = perm.sqlite3.connect(perm.DB_PATH)
    conn.execute("DELETE FROM approval_steps")
    conn.execute("DELETE FROM table_access_requests")
    conn.execute("DELETE FROM requests")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def clean_db():
    _reset_db()
    yield
    _reset_db()


@pytest.fixture
def client():
    perm.app.config["TESTING"] = True
    with perm.app.test_client() as c:
        yield c


@pytest.fixture
def local_git_repo(tmp_path, monkeypatch):
    """给 apply_to_git()/apply_grant_to_git()/reclaim_expired()/transfer()
    这几个真正执行 git clone/push 的分支搭一个本地裸仓库当 REPO_URL——不用
    真的连 GitHub。REPO_URL 是本地路径(不是 https://),所以 GIT_TOKEN
    拼接那行 `REPO_URL.replace("https://", ...)` 是 no-op,可以放心塞一个
    假 token 只用来通过 `if not GIT_TOKEN` 这个门槛,不会被真的发送到任何
    地方。返回裸仓库路径,测试断言时用它建一个新 clone 读最终内容。"""
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)

    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(bare), str(seed)], check=True, capture_output=True)
    iam_dir = seed / "platform" / "iam"
    iam_dir.mkdir(parents=True)
    (iam_dir / "memberships.csv").write_text("username,group_name\nengineer2,viewers\n")
    (iam_dir / "table-access-grants.csv").write_text("username,table_fqn,security_level,granted_at,expires_at\n")
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "seed@test.local"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "seed"], check=True)
    subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(seed), "commit", "-m", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "push"], check=True, capture_output=True)

    monkeypatch.setattr(perm, "REPO_URL", str(bare))
    monkeypatch.setattr(perm, "GIT_TOKEN", "dummy-token-not-a-real-secret")
    return bare


def _read_csv_from_bare(bare_path, tmp_path, rel_path, name):
    checkout = tmp_path / name
    subprocess.run(["git", "clone", str(bare_path), str(checkout)], check=True, capture_output=True)
    return (checkout / rel_path).read_text()


def auth(username):
    """模拟 oauth2-proxy 注入的身份头——只用 X-Forwarded-User,不带
    X-Forwarded-Access-Token(get_current_user() 在没有合法 JWT 时就是
    单纯用这个头当用户名,groups 留空;is_approver() 相关的测试单独用
    monkeypatch 覆盖 groups,见下面 TestTableAccessApprovalFlow)。"""
    return {"X-Forwarded-User": username}


class TestGroupRequestFlow:
    def test_submit_then_pending_in_index(self, client):
        resp = client.post("/request", data={"group_name": "data-analysts", "reason": "test"}, headers=auth("engineer1"))
        assert resp.status_code == 302
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        row = conn.execute("SELECT * FROM requests WHERE username='engineer1'").fetchone()
        conn.close()
        assert row["status"] == "pending"
        assert row["group_name"] == "data-analysts"

    def test_submit_invalid_group_rejected(self, client):
        resp = client.post("/request", data={"group_name": "not-a-real-group"}, headers=auth("engineer1"))
        assert resp.status_code == 400

    def test_submit_without_username_unauthorized(self, client):
        resp = client.post("/request", data={"group_name": "data-analysts"})
        assert resp.status_code == 401

    def test_approve_without_git_token_leaves_approved_pending_apply(self, client):
        client.post("/request", data={"group_name": "data-analysts"}, headers=auth("engineer1"))
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        req_id = conn.execute("SELECT id FROM requests").fetchone()["id"]
        conn.close()

        resp = client.post(f"/requests/{req_id}/approve", headers=auth("admin"), environ_overrides={})
        # get_current_user() 在没有 groups 的情况下不是 approver,应该被拦下
        assert resp.status_code == 403

    def test_approve_by_approver_group(self, client, monkeypatch):
        client.post("/request", data={"group_name": "data-analysts"}, headers=auth("engineer1"))
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        req_id = conn.execute("SELECT id FROM requests").fetchone()["id"]
        conn.close()

        monkeypatch.setattr(perm, "get_current_user", lambda: ("admin", ["platform-team"]))
        resp = client.post(f"/requests/{req_id}/approve")
        assert resp.status_code == 302
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        row = conn.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
        conn.close()
        # GIT_TOKEN 没配置,apply_to_git() 返回 False,状态该是
        # approved_pending_apply,不是 applied——这条区分本身就是要测的行为
        assert row["status"] == "approved_pending_apply"
        assert row["decided_by"] == "admin"

    def test_reject_by_approver_group(self, client, monkeypatch):
        client.post("/request", data={"group_name": "viewers"}, headers=auth("engineer1"))
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        req_id = conn.execute("SELECT id FROM requests").fetchone()["id"]
        conn.close()

        monkeypatch.setattr(perm, "get_current_user", lambda: ("admin", ["platform-team"]))
        resp = client.post(f"/requests/{req_id}/reject")
        assert resp.status_code == 302
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        row = conn.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
        conn.close()
        assert row["status"] == "rejected"

    def test_reject_by_non_approver_forbidden(self, client):
        resp = client.post("/requests/1/reject", headers=auth("engineer1"))
        assert resp.status_code == 403


def _create_table_request(username, table_fqn, security_level, table_owner, monkeypatch, client, reason=""):
    """走真实的 /table-access/request 路由建一条申请(不是直接调内部
    函数),顺带验证这条路由本身的行为。lookup_table_governance 被
    monkeypatch 掉——这个函数本来是查 OpenMetadata 的,测试环境没有真的
    OpenMetadata 可查,用假数据控制安全等级/负责人来驱动状态机。"""
    monkeypatch.setattr(perm, "lookup_table_governance", lambda fqn: (security_level, table_owner))
    resp = client.post("/table-access/request", data={"table_fqn": table_fqn, "reason": reason}, headers=auth(username))
    assert resp.status_code == 302
    conn = perm.sqlite3.connect(perm.DB_PATH)
    conn.row_factory = perm.sqlite3.Row
    row = conn.execute(
        "SELECT * FROM table_access_requests WHERE username=? AND table_fqn=? ORDER BY id DESC", (username, table_fqn)
    ).fetchone()
    conn.close()
    return row


class TestTableAccessSubmit:
    def test_governance_lookup_fails_auto_rejected(self, client, monkeypatch):
        """OpenMetadata 查不到这张表(没登记过/token 没配)时,申请应该
        直接被拒绝,不是卡在某个中间状态。"""
        row = _create_table_request("engineer1", "iceberg.demo.unknown_table", None, None, monkeypatch, client)
        assert row["status"] == "rejected"
        assert "查不到" in row["note"]

    def test_level1_creates_two_pending_steps_for_manager_and_owner(self, client, monkeypatch):
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "manager1", monkeypatch, client)
        assert row["status"] == "pending"
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        steps = conn.execute("SELECT * FROM approval_steps WHERE request_id=? ORDER BY step_order", (row["id"],)).fetchall()
        conn.close()
        # table_owner 正好等于 manager1(申请人的直属上级),应该去重成一步
        assert [(s["step_order"], s["approver_username"]) for s in steps] == [(1, "manager1")]
        assert steps[0]["activated_at"] is not None  # 第一级提交时就该被激活

    def test_no_approvers_computable_auto_rejected(self, client, monkeypatch):
        """申请人在组织架构里查不到上级、这张表也没有 owner——不该卡死在
        没有任何审批人的状态,直接拒绝。"""
        row = _create_table_request("engineer3", "iceberg.demo.orphan", 1, "", monkeypatch, client)
        assert row["status"] == "rejected"
        assert "算不出任何审批人" in row["note"]


class TestTableAccessApprovalFlow:
    """完整走一遍 security_level=2 的多级审批链:L1(manager+owner)全部批完
    才解锁 L2(manager_manager),L2 批完才终态 approved。"""

    def _steps(self, request_id):
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM approval_steps WHERE request_id=? ORDER BY step_order, id", (request_id,)
        ).fetchall()
        conn.close()
        return {r["approver_username"]: r for r in rows}

    def _request_status(self, request_id):
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        row = conn.execute("SELECT * FROM table_access_requests WHERE id=?", (request_id,)).fetchone()
        conn.close()
        return row

    def test_full_chain_l2_approve(self, client, monkeypatch):
        row = _create_table_request("engineer1", "iceberg.demo.orders", 2, "owner-x", monkeypatch, client)
        request_id = row["id"]
        steps = self._steps(request_id)
        # L1: manager1(经理)+ owner-x;L2: director(manager_manager),这一步
        # 提交时还不该被激活(activated_at 为空)。
        assert steps["manager1"]["step_order"] == 1
        assert steps["owner-x"]["step_order"] == 1
        assert steps["director"]["step_order"] == 2
        assert steps["director"]["activated_at"] is None

        # L2 的人在 L1 还没批完之前想批,应该被 409 拦下(状态机不能插队)。
        resp = client.post(f"/table-access/step/{steps['director']['id']}/approve", headers=auth("director"))
        assert resp.status_code == 409

        resp = client.post(f"/table-access/step/{steps['manager1']['id']}/approve", headers=auth("manager1"))
        assert resp.status_code == 302
        assert self._request_status(request_id)["status"] == "pending"  # owner-x 还没批,L1 没完

        resp = client.post(f"/table-access/step/{steps['owner-x']['id']}/approve", headers=auth("owner-x"))
        assert resp.status_code == 302
        # L1 全部批完,L2(director)现在应该被激活了
        assert self._steps(request_id)["director"]["activated_at"] is not None
        assert self._request_status(request_id)["status"] == "pending"

        resp = client.post(f"/table-access/step/{steps['director']['id']}/approve", headers=auth("director"))
        assert resp.status_code == 302
        final = self._request_status(request_id)
        assert final["status"] == "approved"
        assert final["decided_at"] is not None

    def test_reject_at_any_step_rejects_whole_request_and_skips_rest(self, client, monkeypatch):
        row = _create_table_request("engineer1", "iceberg.demo.orders", 2, "owner-x", monkeypatch, client)
        request_id = row["id"]
        steps = self._steps(request_id)

        resp = client.post(f"/table-access/step/{steps['manager1']['id']}/reject", headers=auth("manager1"))
        assert resp.status_code == 302
        final = self._request_status(request_id)
        assert final["status"] == "rejected"

        remaining = self._steps(request_id)
        assert remaining["manager1"]["status"] == "rejected"
        # owner-x 还没审到就因为 manager1 拒绝而整体作废,应该标成 skipped,
        # 不能一直挂在"待审批"列表里
        assert remaining["owner-x"]["status"] == "skipped"

    def test_wrong_user_cannot_approve_someone_elses_step(self, client, monkeypatch):
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "owner-x", monkeypatch, client)
        request_id = row["id"]
        step_id = self._steps(request_id)["manager1"]["id"]
        resp = client.post(f"/table-access/step/{step_id}/approve", headers=auth("someone-else"))
        assert resp.status_code == 403

    def test_already_decided_step_cannot_be_approved_again(self, client, monkeypatch):
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "owner-x", monkeypatch, client)
        request_id = row["id"]
        step_id = self._steps(request_id)["manager1"]["id"]
        client.post(f"/table-access/step/{step_id}/approve", headers=auth("manager1"))
        resp = client.post(f"/table-access/step/{step_id}/approve", headers=auth("manager1"))
        assert resp.status_code == 403


class TestExternalCallback:
    def test_missing_token_forbidden(self, client, monkeypatch):
        monkeypatch.setattr(perm, "EXTERNAL_OA_CALLBACK_TOKEN", "secret-token")
        resp = client.post("/table-access/step/1/external-callback", json={"status": "approved"})
        assert resp.status_code == 403

    def test_wrong_token_forbidden(self, client, monkeypatch):
        monkeypatch.setattr(perm, "EXTERNAL_OA_CALLBACK_TOKEN", "secret-token")
        resp = client.post("/table-access/step/1/external-callback", json={"status": "approved", "token": "wrong"})
        assert resp.status_code == 403

    def test_invalid_status_rejected(self, client, monkeypatch):
        monkeypatch.setattr(perm, "EXTERNAL_OA_CALLBACK_TOKEN", "secret-token")
        resp = client.post("/table-access/step/1/external-callback", json={"status": "maybe", "token": "secret-token"})
        assert resp.status_code == 400

    def test_step_not_pending_external_not_found(self, client, monkeypatch):
        """本地审批(status='pending')的 step 不该被外部回调直接改状态——
        这条端点只处理真的转出去给外部 OA 的 step(pending_external)。"""
        monkeypatch.setattr(perm, "EXTERNAL_OA_CALLBACK_TOKEN", "secret-token")
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "owner-x", monkeypatch, client)
        step_id = TestTableAccessApprovalFlow()._steps(row["id"])["manager1"]["id"]
        resp = client.post(f"/table-access/step/{step_id}/external-callback", json={"status": "approved", "token": "secret-token"})
        assert resp.status_code == 404

    def test_pending_external_step_approved_via_callback(self, client, monkeypatch):
        """走 APPROVAL_BACKEND=webhook 这条路,dispatch_step() 里会真的
        POST 到 EXTERNAL_OA_WEBHOOK_URL——mock 掉这次 POST(不打真实
        网络),只验证 POST 成功后 step 状态变成 pending_external,以及
        回调把它推进到终态这整条链路。"""
        monkeypatch.setattr(perm, "EXTERNAL_OA_CALLBACK_TOKEN", "secret-token")
        monkeypatch.setattr(perm, "APPROVAL_BACKEND", "webhook")
        monkeypatch.setattr(perm, "EXTERNAL_OA_WEBHOOK_URL", "https://fake-oa.example.com/webhook")
        monkeypatch.setattr(perm.requests, "post", lambda *a, **kw: type("R", (), {"raise_for_status": lambda self: None})())

        # table_owner 特意设成和 manager 一样(去重成唯一一个 L1 审批人),
        # 这样批准这一步就该直接把整条申请推进到终态,不用再批第二个人。
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "manager1", monkeypatch, client)
        step_id = TestTableAccessApprovalFlow()._steps(row["id"])["manager1"]["id"]

        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        status = conn.execute("SELECT status FROM approval_steps WHERE id=?", (step_id,)).fetchone()["status"]
        conn.close()
        assert status == "pending_external"

        resp = client.post(f"/table-access/step/{step_id}/external-callback", json={"status": "approved", "token": "secret-token"})
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}
        final = TestTableAccessApprovalFlow()._request_status(row["id"])
        assert final["status"] == "approved"


class TestEscalationCheck:
    def test_missing_token_forbidden(self, client):
        resp = client.post("/internal/escalation-check")
        assert resp.status_code == 403

    def test_wrong_token_forbidden(self, client, monkeypatch):
        monkeypatch.setattr(perm, "INTERNAL_TOKEN", "internal-secret")
        resp = client.post("/internal/escalation-check", headers={"X-Internal-Token": "wrong"})
        assert resp.status_code == 403

    def test_reminds_but_does_not_escalate_before_threshold(self, client, monkeypatch):
        monkeypatch.setattr(perm, "INTERNAL_TOKEN", "internal-secret")
        monkeypatch.setattr(perm, "ESCALATION_HOURS", 48.0)
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "owner-x", monkeypatch, client)
        step_id = TestTableAccessApprovalFlow()._steps(row["id"])["manager1"]["id"]
        # 手动把 activated_at 拨回 60 小时前(超过提醒线 48h,没到升级线 96h)
        past = (perm.datetime.now(perm.timezone.utc) - perm.timedelta(hours=60)).isoformat()
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.execute("UPDATE approval_steps SET activated_at=? WHERE id=?", (past, step_id))
        conn.commit()
        conn.close()

        resp = client.post("/internal/escalation-check", headers={"X-Internal-Token": "internal-secret"})
        assert resp.status_code == 200
        assert resp.get_json() == {"reminded": 1, "escalated": 0}
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        status = conn.execute("SELECT status FROM approval_steps WHERE id=?", (step_id,)).fetchone()["status"]
        conn.close()
        assert status == "pending"  # 只是提醒,状态不变

    def test_escalates_to_next_manager_after_double_threshold(self, client, monkeypatch):
        monkeypatch.setattr(perm, "INTERNAL_TOKEN", "internal-secret")
        monkeypatch.setattr(perm, "ESCALATION_HOURS", 48.0)
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "owner-x", monkeypatch, client)
        step_id = TestTableAccessApprovalFlow()._steps(row["id"])["manager1"]["id"]
        # manager1 的上级是 director——超过 2x 阈值(96h)应该真的换人审
        past = (perm.datetime.now(perm.timezone.utc) - perm.timedelta(hours=100)).isoformat()
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.execute("UPDATE approval_steps SET activated_at=? WHERE id=?", (past, step_id))
        conn.commit()
        conn.close()

        resp = client.post("/internal/escalation-check", headers={"X-Internal-Token": "internal-secret"})
        assert resp.status_code == 200
        assert resp.get_json() == {"reminded": 0, "escalated": 1}

        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        old_step = conn.execute("SELECT status FROM approval_steps WHERE id=?", (step_id,)).fetchone()
        new_step = conn.execute(
            "SELECT * FROM approval_steps WHERE request_id=? AND approver_username='director' AND step_order=1",
            (row["id"],),
        ).fetchone()
        conn.close()
        assert old_step["status"] == "escalated"
        assert new_step is not None  # 换成 manager1 的上级(director)接手同一级


class TestTransfer:
    def test_non_approver_forbidden(self, client):
        resp = client.get("/admin/transfer", headers=auth("engineer1"))
        assert resp.status_code == 403

    def test_get_form_ok_for_approver(self, client, monkeypatch):
        monkeypatch.setattr(perm, "get_current_user", lambda: ("admin", ["platform-team"]))
        resp = client.get("/admin/transfer")
        assert resp.status_code == 200

    def test_same_user_rejected(self, client, monkeypatch):
        monkeypatch.setattr(perm, "get_current_user", lambda: ("admin", ["platform-team"]))
        resp = client.post("/admin/transfer", data={"from_user": "manager1", "to_user": "manager1"})
        assert resp.status_code == 400

    def test_transfers_pending_steps_to_new_approver(self, client, monkeypatch):
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "owner-x", monkeypatch, client)
        step_id = TestTableAccessApprovalFlow()._steps(row["id"])["manager1"]["id"]

        monkeypatch.setattr(perm, "get_current_user", lambda: ("admin", ["platform-team"]))
        resp = client.post("/admin/transfer", data={"from_user": "manager1", "to_user": "manager2"})
        assert resp.status_code == 200
        assert "已把 1 条待审批表访问申请从 manager1 转给 manager2" in resp.get_data(as_text=True)

        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        step = conn.execute("SELECT * FROM approval_steps WHERE id=?", (step_id,)).fetchone()
        conn.close()
        assert step["approver_username"] == "manager2"
        # manager2 现在能批这一步了
        monkeypatch.setattr(perm, "get_current_user", lambda: ("manager2", []))
        resp2 = client.post(f"/table-access/step/{step_id}/approve")
        assert resp2.status_code == 302


class TestReclaimExpired:
    def test_missing_token_forbidden(self, client):
        resp = client.post("/internal/reclaim-expired")
        assert resp.status_code == 403

    def test_without_git_token_skips_gracefully(self, client, monkeypatch):
        monkeypatch.setattr(perm, "INTERNAL_TOKEN", "internal-secret")
        # 模块级 GIT_TOKEN 默认就是空字符串(测试环境没设过),这里不需要
        # 额外 monkeypatch——就是在验证这个默认情况下的降级行为。
        resp = client.post("/internal/reclaim-expired", headers={"X-Internal-Token": "internal-secret"})
        assert resp.status_code == 200
        assert resp.get_json() == {"reclaimed": 0, "skipped": "GIT_TOKEN 未配置,无法读写 grants.csv"}


class TestAuditAndHealthz:
    def test_healthz(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}

    def test_audit_forbidden_for_non_approver(self, client):
        resp = client.get("/audit", headers=auth("engineer1"))
        assert resp.status_code == 403

    def test_audit_ok_for_approver(self, client, monkeypatch):
        monkeypatch.setattr(perm, "get_current_user", lambda: ("admin", ["platform-team"]))
        resp = client.get("/audit")
        assert resp.status_code == 200


class TestGitWritePaths:
    """补 docs/project/roadmap.md 2.4 里明确记录的缺口:apply_to_git()/
    apply_grant_to_git()/reclaim_expired()/transfer() 真正执行 git
    clone/commit/push 的分支,之前只测过"没配 GIT_TOKEN 时优雅降级"这一半。
    用 local_git_repo fixture 起一个本地裸仓库当 REPO_URL,不连真实
    GitHub。"""

    def test_approve_with_git_token_pushes_membership(self, client, monkeypatch, local_git_repo, tmp_path):
        client.post("/request", data={"group_name": "data-analysts"}, headers=auth("engineer1"))
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        req_id = conn.execute("SELECT id FROM requests").fetchone()["id"]
        conn.close()

        monkeypatch.setattr(perm, "get_current_user", lambda: ("admin", ["platform-team"]))
        resp = client.post(f"/requests/{req_id}/approve")
        assert resp.status_code == 302

        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        row = conn.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
        conn.close()
        assert row["status"] == "applied"

        content = _read_csv_from_bare(local_git_repo, tmp_path, "platform/iam/memberships.csv", "check1")
        assert "engineer1,data-analysts" in content
        assert "engineer2,viewers" in content  # 种子数据没被覆盖掉,是追加

    def test_approve_dedupes_when_line_already_present(self, client, monkeypatch, local_git_repo, tmp_path):
        """seed 数据里已经有 engineer2,viewers 这一行,批准同样的申请不该
        重复追加。"""
        client.post("/request", data={"group_name": "viewers"}, headers=auth("engineer2"))
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        req_id = conn.execute("SELECT id FROM requests WHERE username='engineer2'").fetchone()["id"]
        conn.close()

        monkeypatch.setattr(perm, "get_current_user", lambda: ("admin", ["platform-team"]))
        resp = client.post(f"/requests/{req_id}/approve")
        assert resp.status_code == 302

        content = _read_csv_from_bare(local_git_repo, tmp_path, "platform/iam/memberships.csv", "check2")
        assert content.count("engineer2,viewers") == 1

    def test_table_access_full_chain_approve_pushes_grant(self, client, monkeypatch, local_git_repo, tmp_path):
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "owner-x", monkeypatch, client)
        request_id = row["id"]
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        steps = {
            r["approver_username"]: r
            for r in conn.execute("SELECT * FROM approval_steps WHERE request_id=?", (request_id,)).fetchall()
        }
        conn.close()

        resp = client.post(f"/table-access/step/{steps['manager1']['id']}/approve", headers=auth("manager1"))
        assert resp.status_code == 302
        resp = client.post(f"/table-access/step/{steps['owner-x']['id']}/approve", headers=auth("owner-x"))
        assert resp.status_code == 302

        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        final = conn.execute("SELECT * FROM table_access_requests WHERE id=?", (request_id,)).fetchone()
        conn.close()
        assert final["status"] == "approved"

        content = _read_csv_from_bare(local_git_repo, tmp_path, "platform/iam/table-access-grants.csv", "check3")
        assert "engineer1,iceberg.demo.orders,1," in content

    def test_reclaim_expired_removes_expired_row_and_pushes(self, client, monkeypatch, local_git_repo, tmp_path):
        # 直接往种子仓库的 grants.csv 塞一条已过期 + 一条未过期,验证只有
        # 过期的那条被摘掉。
        checkout = tmp_path / "seed_grant"
        subprocess.run(["git", "clone", str(local_git_repo), str(checkout)], check=True, capture_output=True)
        grants_path = checkout / "platform" / "iam" / "table-access-grants.csv"
        grants_path.write_text(
            "username,table_fqn,security_level,granted_at,expires_at\n"
            "old_user,iceberg.demo.old_table,1,2020-01-01T00:00:00+00:00,2020-02-01T00:00:00+00:00\n"
            "fresh_user,iceberg.demo.fresh_table,1,2020-01-01T00:00:00+00:00,2099-01-01T00:00:00+00:00\n"
        )
        subprocess.run(["git", "-C", str(checkout), "config", "user.email", "seed@test.local"], check=True)
        subprocess.run(["git", "-C", str(checkout), "config", "user.name", "seed"], check=True)
        subprocess.run(["git", "-C", str(checkout), "commit", "-am", "add expired grant"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(checkout), "push"], check=True, capture_output=True)

        monkeypatch.setattr(perm, "INTERNAL_TOKEN", "internal-secret")
        monkeypatch.setattr(perm, "notify_wecom", lambda msg: None)  # 不真的调外部 webhook
        resp = client.post("/internal/reclaim-expired", headers={"X-Internal-Token": "internal-secret"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["reclaimed"] == 1
        assert body["tables"] == ["iceberg.demo.old_table"]

        content = _read_csv_from_bare(local_git_repo, tmp_path, "platform/iam/table-access-grants.csv", "check4")
        assert "old_user" not in content
        assert "fresh_user" in content

    def test_transfer_moves_membership_rows_in_git(self, client, monkeypatch, local_git_repo, tmp_path):
        monkeypatch.setattr(perm, "get_current_user", lambda: ("admin", ["platform-team"]))
        monkeypatch.setattr(perm, "notify_wecom", lambda msg: None)
        # seed 数据里 engineer2 已经在 viewers,transfer 到一个新用户身上。
        resp = client.post("/admin/transfer", data={"from_user": "engineer2", "to_user": "engineer5"})
        assert resp.status_code == 200
        assert b"engineer5" in resp.data

        content = _read_csv_from_bare(local_git_repo, tmp_path, "platform/iam/memberships.csv", "check5")
        assert "engineer5,viewers" in content

    def test_transfer_skips_git_write_when_target_already_has_group(self, client, monkeypatch, local_git_repo, tmp_path):
        """to_user 已经拥有 from_user 的全部组,不用改 memberships.csv,
        不该产生新的 commit(验证"不需要写就不写"这条,不是泛泛地测
        happy path)。"""
        # 先给种子仓库加一行 engineer6,viewers,让它和 engineer2 的组完全
        # 重叠,再测 transfer 不应该往 git 里多写一次。
        checkout = tmp_path / "seed_transfer"
        subprocess.run(["git", "clone", str(local_git_repo), str(checkout)], check=True, capture_output=True)
        memberships = checkout / "platform" / "iam" / "memberships.csv"
        memberships.write_text(memberships.read_text() + "engineer6,viewers\n")
        subprocess.run(["git", "-C", str(checkout), "config", "user.email", "seed@test.local"], check=True)
        subprocess.run(["git", "-C", str(checkout), "config", "user.name", "seed"], check=True)
        subprocess.run(["git", "-C", str(checkout), "commit", "-am", "seed engineer6"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(checkout), "push"], check=True, capture_output=True)
        before_rev = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()

        monkeypatch.setattr(perm, "get_current_user", lambda: ("admin", ["platform-team"]))
        monkeypatch.setattr(perm, "notify_wecom", lambda msg: None)
        resp = client.post("/admin/transfer", data={"from_user": "engineer2", "to_user": "engineer6"})
        assert resp.status_code == 200
        assert "已经拥有".encode() in resp.data

        after_checkout = tmp_path / "after_transfer"
        subprocess.run(["git", "clone", str(local_git_repo), str(after_checkout)], check=True, capture_output=True)
        after_rev = subprocess.run(
            ["git", "-C", str(after_checkout), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        assert after_rev == before_rev


class TestExternalOaWebhookDispatch:
    """补 docs/project/roadmap.md 2.4 里另一半没测的路径:dispatch_step() 在
    APPROVAL_BACKEND=webhook 时真的 POST 到 EXTERNAL_OA_WEBHOOK_URL——之前
    只测过 local 模式(留在 pending、发企微通知)。用 monkeypatch 替换
    perm.requests.post,不真的发网络请求。"""

    def test_webhook_success_marks_pending_external(self, client, monkeypatch):
        monkeypatch.setattr(perm, "APPROVAL_BACKEND", "webhook")
        monkeypatch.setattr(perm, "EXTERNAL_OA_WEBHOOK_URL", "https://oa.example.com/hook")
        monkeypatch.setattr(perm, "notify_wecom", lambda msg: None)

        calls = []

        def fake_post(url, json=None, timeout=None):
            calls.append((url, json))
            class Resp:
                status_code = 200
            return Resp()

        monkeypatch.setattr(perm.requests, "post", fake_post)

        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "owner-x", monkeypatch, client)
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        step = conn.execute(
            "SELECT * FROM approval_steps WHERE request_id=? AND approver_username='manager1'", (row["id"],)
        ).fetchone()
        conn.close()

        # L1 一次激活 manager1 + owner-x 两个人,两条都各自 POST 一次。
        assert step["status"] == "pending_external"
        assert len(calls) == 2
        manager_call = next(c for c in calls if c[1]["approver_username"] == "manager1")
        assert manager_call[0] == "https://oa.example.com/hook"

    def test_webhook_failure_falls_back_to_local_pending(self, client, monkeypatch):
        monkeypatch.setattr(perm, "APPROVAL_BACKEND", "webhook")
        monkeypatch.setattr(perm, "EXTERNAL_OA_WEBHOOK_URL", "https://oa.example.com/hook")
        wecom_calls = []
        monkeypatch.setattr(perm, "notify_wecom", lambda msg: wecom_calls.append(msg))

        def failing_post(url, json=None, timeout=None):
            raise perm.requests.RequestException("connection refused")

        monkeypatch.setattr(perm.requests, "post", failing_post)

        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "owner-x", monkeypatch, client)
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        step = conn.execute(
            "SELECT * FROM approval_steps WHERE request_id=? AND approver_username='manager1'", (row["id"],)
        ).fetchone()
        conn.close()

        # POST 失败,不该把这一步吞掉变成没人能处理——退化回本地 pending,
        # 照样发企微通知(L1 两个人各一条)。
        assert step["status"] == "pending"
        assert len(wecom_calls) == 2
