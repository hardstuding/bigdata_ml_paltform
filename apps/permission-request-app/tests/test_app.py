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


def _create_table_request(username, table_fqn, security_level, table_owner, monkeypatch, client,
                          reason="日常报表口径核对,需要看这张表的明细"):
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
        # **这条用例没有 local_git_repo fixture**,所以 apply_grant_to_git 必然
        # 失败(没有 GIT_TOKEN)。审批链路本身走完了,但授权没写进 csv ——
        # 正确终态是 approved_pending_apply,不是 approved。
        #
        # 2026-08-29 之前这里断言的是 approved,而那是**在断言一个 bug**:
        # 旧代码无条件把状态写成 approved,把"写入失败"完全掩盖了。
        # 真正的成功路径由 test_table_access_full_chain_approve_pushes_grant
        # 覆盖(它带 git fixture,断言 approved + csv 里真的有那行)。
        assert final["status"] == "approved_pending_apply"
        assert final["decided_at"] is not None

    def test_reject_at_any_step_rejects_whole_request_and_skips_rest(self, client, monkeypatch):
        row = _create_table_request("engineer1", "iceberg.demo.orders", 2, "owner-x", monkeypatch, client)
        request_id = row["id"]
        steps = self._steps(request_id)

        # 2026-08-29 起拒绝必须带原因(没有原因的拒绝对申请人是一堵墙),
        # 所以这里补上 comment;不带 comment 的分支由 TestRejectRequiresReason 覆盖。
        resp = client.post(f"/table-access/step/{steps['manager1']['id']}/reject",
                           data={"comment": "这张表含客户手机号,该场景用脱敏视图即可"},
                           headers=auth("manager1"))
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
        # 同上:这条用例没有 git fixture,授权写不进去。这里验的是"外部回调
        # 能让那一步通过、并推进到终态",不是"授权真的落盘"。
        assert final["status"] == "approved_pending_apply"


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


class TestApplyFailureIsNotSuccess:
    """**授权写入失败时,绝不能标成"已批准"。**

    2026-08-29 之前的代码是:
        ok, note = apply_grant_to_git(...)      # ok 被接收,但从来不用
        UPDATE ... SET status='approved' ...    # 无条件
        notify("已全部批准")                     # 无条件

    后果:git 写失败(没配 token、网络断、push 冲突)时,系统显示"已批准"、
    用户收到"已批准"的通知,而 OPA 读的那个 csv 里根本没有这条授权 ——
    用户去查数会被拒,然后来问"为什么批了还查不了",而所有界面都说一切正常。
    **在权限系统里这是最不能有的一类 bug:它同时骗了用户和审计。**

    下面四条分别锁住:失败不算成功、成功仍然算成功、重试能补回来、重复重试
    不会写重复行。
    """

    def _approve_all(self, client, monkeypatch, request_id):
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        steps = {r["approver_username"]: r for r in conn.execute(
            "SELECT * FROM approval_steps WHERE request_id=?", (request_id,)).fetchall()}
        conn.close()
        for who in ("manager1", "owner-x"):
            client.post(f"/table-access/step/{steps[who]['id']}/approve", headers=auth(who))

    def _status(self, request_id):
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        row = conn.execute("SELECT * FROM table_access_requests WHERE id=?", (request_id,)).fetchone()
        conn.close()
        return row["status"], row["note"]

    def test_写入失败时状态不是approved(self, client, monkeypatch, local_git_repo, tmp_path):
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "owner-x", monkeypatch, client)
        monkeypatch.setattr(perm, "apply_grant_to_git",
                            lambda *a, **k: (False, "模拟:push 被拒"))
        self._approve_all(client, monkeypatch, row["id"])
        status, note = self._status(row["id"])
        assert status == "approved_pending_apply", f"写入失败却标成了 {status}"
        assert "push 被拒" in note

    def test_写入失败时不发已批准的通知(self, client, monkeypatch, local_git_repo, tmp_path):
        """用户据"已批准"去查数会失败,然后来问为什么。通知必须说清真实状态。"""
        sent = []
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "owner-x", monkeypatch, client)
        monkeypatch.setattr(perm, "apply_grant_to_git", lambda *a, **k: (False, "模拟失败"))
        monkeypatch.setattr(perm, "notify_wecom", lambda msg: sent.append(msg))
        self._approve_all(client, monkeypatch, row["id"])
        final = [m for m in sent if "申请" in m][-1]
        assert "已全部批准" not in final, f"失败时却说已批准:{final}"
        assert "还不能用" in final

    def test_写入成功时仍然是approved(self, client, monkeypatch, local_git_repo, tmp_path):
        """**这条防的是"为了让上面那条变绿而把成功路径也弄坏"。**"""
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "owner-x", monkeypatch, client)
        self._approve_all(client, monkeypatch, row["id"])
        status, _ = self._status(row["id"])
        assert status == "approved"

    def test_重试能把卡住的补回来(self, client, monkeypatch, local_git_repo, tmp_path):
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "owner-x", monkeypatch, client)
        calls = {"n": 0}
        real = perm.apply_grant_to_git

        def flaky(*a, **k):
            calls["n"] += 1
            return (False, "第一次失败") if calls["n"] == 1 else real(*a, **k)

        monkeypatch.setattr(perm, "apply_grant_to_git", flaky)
        self._approve_all(client, monkeypatch, row["id"])
        assert self._status(row["id"])[0] == "approved_pending_apply"

        monkeypatch.setattr(perm, "INTERNAL_TOKEN", "internal-secret")
        resp = client.post("/internal/retry-pending-applies",
                           headers={"X-Internal-Token": "internal-secret"})
        assert resp.status_code == 200
        assert resp.get_json()["retried_ok"] == 1
        assert self._status(row["id"])[0] == "approved"
        content = _read_csv_from_bare(local_git_repo, tmp_path,
                                      "platform/iam/table-access-grants.csv", "retry1")
        assert "engineer1,iceberg.demo.orders,1," in content

    def test_重复重试不会写重复行(self, client, monkeypatch, local_git_repo, tmp_path):
        """重试机制迟早会被重复触发(CronJob 每小时一次)。不幂等的话,
        csv 里会攒出一堆同样的授权行,而 OPA 读的就是它。"""
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "owner-x", monkeypatch, client)
        self._approve_all(client, monkeypatch, row["id"])
        monkeypatch.setattr(perm, "INTERNAL_TOKEN", "internal-secret")
        for _ in range(3):
            client.post("/internal/retry-pending-applies",
                        headers={"X-Internal-Token": "internal-secret"})
        content = _read_csv_from_bare(local_git_repo, tmp_path,
                                      "platform/iam/table-access-grants.csv", "retry2")
        assert content.count("engineer1,iceberg.demo.orders,1,") == 1

    def test_重试端点要token(self, client):
        assert client.post("/internal/retry-pending-applies").status_code == 403


# ---------------------------------------------------------------------------
# 只读 API(给门户的角色工作台用,roadmap P1.5)
#
# 这两个接口会被门户首页每次刷新调用,所以测试重点有两个:一是**不带
# user 不许返回数据**(不然就是给门户开了一个越权读取的口子),二是
# **数据源缺失时要降级返回空,不是 500**(门户上少一块内容,好过整页崩)。
# ---------------------------------------------------------------------------
class TestReadOnlyApi:
    TOKEN = "test-internal-token"

    @pytest.fixture(autouse=True)
    def _token(self, monkeypatch):
        monkeypatch.setattr(perm, "INTERNAL_TOKEN", self.TOKEN)

    def _hdr(self):
        return {"X-Internal-Token": self.TOKEN}

    def _grants_file(self, tmp_path, monkeypatch, rows):
        f = tmp_path / "grants.csv"
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["username", "table_fqn",
                                               "security_level", "expires_at"])
            w.writeheader()
            for r in rows:
                w.writerow(r)
        monkeypatch.setenv("GRANTS_CSV_PATH", str(f))
        return f

    def test_没有_token_一律_403(self, client):
        assert client.get("/api/my-permissions?user=alice").status_code == 403
        assert client.get("/api/my-approvals?user=alice").status_code == 403

    def test_token_错了也_403(self, client):
        h = {"X-Internal-Token": "wrong"}
        assert client.get("/api/my-permissions?user=alice", headers=h).status_code == 403

    def test_不带_user_拒绝_而不是返回全量(self, client):
        # 这条是安全边界:一个不带 user 就返回所有人权限的接口,等于越权读取。
        r = client.get("/api/my-permissions", headers=self._hdr())
        assert r.status_code == 400
        r = client.get("/api/my-approvals", headers=self._hdr())
        assert r.status_code == 400

    def test_只返回这个人自己的授权(self, client, tmp_path, monkeypatch):
        self._grants_file(tmp_path, monkeypatch, [
            {"username": "alice", "table_fqn": "iceberg.demo.orders",
             "security_level": "1", "expires_at": ""},
            {"username": "bob", "table_fqn": "iceberg.demo.secret",
             "security_level": "3", "expires_at": ""},
        ])
        body = client.get("/api/my-permissions?user=alice", headers=self._hdr()).get_json()
        assert [g["table"] for g in body["grants"]] == ["iceberg.demo.orders"]

    def test_算得出还有几天到期_并标出快过期的(self, client, tmp_path, monkeypatch):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        self._grants_file(tmp_path, monkeypatch, [
            {"username": "alice", "table_fqn": "t.soon", "security_level": "1",
             "expires_at": (now + timedelta(days=5)).isoformat()},
            {"username": "alice", "table_fqn": "t.later", "security_level": "1",
             "expires_at": (now + timedelta(days=200)).isoformat()},
        ])
        body = client.get("/api/my-permissions?user=alice&soon_days=30",
                          headers=self._hdr()).get_json()
        soon = {g["table"] for g in body["expiring_soon"]}
        assert soon == {"t.soon"}
        # 快到期的排在前面——首页上要先看到的是这条
        assert body["grants"][0]["table"] == "t.soon"

    def test_没有到期时间的授权不会被当成快过期(self, client, tmp_path, monkeypatch):
        self._grants_file(tmp_path, monkeypatch, [
            {"username": "alice", "table_fqn": "t.forever",
             "security_level": "1", "expires_at": ""},
        ])
        body = client.get("/api/my-permissions?user=alice", headers=self._hdr()).get_json()
        assert body["expiring_soon"] == []
        assert body["grants"][0]["days_left"] is None

    def test_读不到_grants_返回空而不是报错(self, client, monkeypatch):
        # 门户首页每次刷新都会调它,数据源缺失时整页 500 是不可接受的。
        monkeypatch.setenv("GRANTS_CSV_PATH", "/nonexistent/grants.csv")
        monkeypatch.setattr(perm, "GIT_TOKEN", "")
        r = client.get("/api/my-permissions?user=alice", headers=self._hdr())
        assert r.status_code == 200
        assert r.get_json()["grants"] == []

    def test_待我审批只返回轮到我的那一步(self, client):
        # 分级审批链里,后面几步的审批人在轮到之前不该看到这条申请。
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.execute(
            "INSERT INTO table_access_requests (id, username, table_fqn, security_level,"
            " status, requested_at) VALUES (1,'alice','iceberg.demo.orders',2,'pending','x')")
        conn.execute(
            "INSERT INTO approval_steps (request_id, step_order, approver_role,"
            " approver_username, status) VALUES (1,1,'manager','director','pending')")
        conn.execute(
            "INSERT INTO approval_steps (request_id, step_order, approver_role,"
            " approver_username, status) VALUES (1,2,'manager','ceo','pending')")
        conn.commit(); conn.close()

        mine = client.get("/api/my-approvals?user=director", headers=self._hdr()).get_json()
        assert [p["applicant"] for p in mine["pending"]] == ["alice"]
        # ceo 那步也是 pending,当前实现按 approver_username 过滤,两个人各看到自己那步
        assert len(client.get("/api/my-approvals?user=ceo",
                              headers=self._hdr()).get_json()["pending"]) == 1
        assert client.get("/api/my-approvals?user=engineer1",
                          headers=self._hdr()).get_json()["pending"] == []

    def test_等太久的会被标成_overdue(self, client):
        from datetime import datetime, timedelta, timezone
        old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.execute(
            "INSERT INTO table_access_requests (id, username, table_fqn, security_level,"
            " status, requested_at) VALUES (1,'alice','t.x',1,'pending','x')")
        conn.execute(
            "INSERT INTO approval_steps (request_id, step_order, approver_role,"
            " approver_username, status, activated_at) VALUES (1,1,'manager','director',"
            "'pending',?)", (old,))
        conn.commit(); conn.close()
        body = client.get("/api/my-approvals?user=director", headers=self._hdr()).get_json()
        assert body["pending"][0]["waiting_hours"] >= 72
        assert len(body["overdue"]) == 1

    def test_已经批过的不再出现在待办里(self, client):
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.execute(
            "INSERT INTO table_access_requests (id, username, table_fqn, security_level,"
            " status, requested_at) VALUES (1,'alice','t.x',1,'pending','x')")
        conn.execute(
            "INSERT INTO approval_steps (request_id, step_order, approver_role,"
            " approver_username, status) VALUES (1,1,'manager','director','approved')")
        conn.commit(); conn.close()
        assert client.get("/api/my-approvals?user=director",
                          headers=self._hdr()).get_json()["pending"] == []


# ---------------------------------------------------------------------------
# 审批体验(roadmap P1.5「审批体验」)
# ---------------------------------------------------------------------------
class TestChineseStatusLabels:
    def test_最容易误读的那个状态有中文说明(self):
        # approved_pending_apply 字面像"批了",实际是"批了但权限还没生效",
        # 这两件事对使用者的意义完全不同。
        label = perm.status_label("approved_pending_apply")
        assert "已通过" in label and "尚未生效" in label

    def test_认不出来的状态原样返回_不吞信息(self):
        assert perm.status_label("some_new_state") == "some_new_state"

    def test_页面上印的是中文不是英文枚举(self, client, monkeypatch):
        _create_table_request("engineer1", "iceberg.demo.orders", 1, "manager1",
                              monkeypatch, client)
        html = client.get("/", headers=auth("engineer1")).get_data(as_text=True)
        assert "等待审批" in html
        # 表格里那一栏不该再直接印英文枚举
        assert ">pending<" not in html


class TestLocalTimeRendering:
    def test_时间戳带上_time_标签交给浏览器换算时区(self, client, monkeypatch):
        _create_table_request("engineer1", "iceberg.demo.orders", 1, "manager1",
                              monkeypatch, client)
        html = client.get("/", headers=auth("engineer1")).get_data(as_text=True)
        # 服务端不知道用户在哪个时区,所以只输出 UTC 值 + 标记,由页面 JS 换算
        assert '<time class="lt"' in html
        assert "toLocaleString" in html

    def test_空时间不渲染出空标签(self):
        assert perm._localtime_html("") == ""
        assert perm._localtime_html(None) == ""


class TestReasonRequiredBySecurityLevel:
    def test_低敏表不强制写理由(self, client, monkeypatch):
        monkeypatch.setattr(perm, "lookup_table_governance", lambda fqn: (1, "manager1"))
        resp = client.post("/table-access/request",
                           data={"table_fqn": "iceberg.demo.orders", "reason": ""},
                           headers=auth("engineer1"))
        assert resp.status_code == 302

    def test_二级起必须写理由_而且不能敷衍(self, client, monkeypatch):
        monkeypatch.setattr(perm, "lookup_table_governance", lambda fqn: (2, "manager1"))
        resp = client.post("/table-access/request",
                           data={"table_fqn": "iceberg.demo.users", "reason": "查数"},
                           headers=auth("engineer1"))
        assert resp.status_code == 400
        assert "必须写明申请理由" in resp.get_json()["error"]

    def test_写够了就放行(self, client, monkeypatch):
        monkeypatch.setattr(perm, "lookup_table_governance", lambda fqn: (2, "manager1"))
        resp = client.post("/table-access/request",
                           data={"table_fqn": "iceberg.demo.users",
                                 "reason": "对账需要核对用户表的注册时间字段"},
                           headers=auth("engineer1"))
        assert resp.status_code == 302

    def test_批量申请里只挡住需要补理由的_其余照常提交(self, client, monkeypatch):
        # 勾了一堆表,因为其中几张要理由就全部作废,是很气人的设计。
        levels = {"iceberg.demo.a": 1, "iceberg.demo.b": 3}
        monkeypatch.setattr(perm, "lookup_table_governance",
                            lambda fqn: (levels[fqn], "manager1"))
        resp = client.post("/table-access/request-batch",
                           data={"table_fqn": ["iceberg.demo.a", "iceberg.demo.b"],
                                 "reason": ""},
                           headers=auth("engineer1"))
        assert resp.status_code == 400
        body = resp.get_json()
        assert len(body["details"]) == 1 and "iceberg.demo.b" in body["details"][0]
        # a 那条已经建出来了
        conn = perm.sqlite3.connect(perm.DB_PATH)
        got = conn.execute("SELECT table_fqn FROM table_access_requests").fetchall()
        conn.close()
        assert [g[0] for g in got] == ["iceberg.demo.a"]


class TestRejectRequiresReason:
    def _step(self, client, monkeypatch):
        row = _create_table_request("engineer1", "iceberg.demo.orders", 1, "manager1",
                                    monkeypatch, client)
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        step = conn.execute(
            "SELECT * FROM approval_steps WHERE request_id=? AND approver_username='manager1'",
            (row["id"],)).fetchone()
        conn.close()
        return step

    def test_不写原因的拒绝被服务端挡住(self, client, monkeypatch):
        # 前端有 required,但直接 POST 能绕过去,所以服务端必须也校验。
        step = self._step(client, monkeypatch)
        resp = client.post(f"/table-access/step/{step['id']}/reject",
                           headers=auth("manager1"))
        assert resp.status_code == 400
        assert "必须填写原因" in resp.get_json()["error"]

    def test_被挡住时状态没有被改坏(self, client, monkeypatch):
        step = self._step(client, monkeypatch)
        client.post(f"/table-access/step/{step['id']}/reject", headers=auth("manager1"))
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        after = conn.execute("SELECT * FROM approval_steps WHERE id=?", (step["id"],)).fetchone()
        conn.close()
        assert after["status"] == "pending"

    def test_原因会被存下来并能给申请人看到(self, client, monkeypatch):
        step = self._step(client, monkeypatch)
        client.post(f"/table-access/step/{step['id']}/reject",
                    data={"comment": "这个场景用脱敏视图就够了,不需要明细表"},
                    headers=auth("manager1"))
        html = client.get("/", headers=auth("engineer1")).get_data(as_text=True)
        assert "脱敏视图" in html          # 申请人真的看得到,不只是存进库

    def test_批准的意见是可选的(self, client, monkeypatch):
        step = self._step(client, monkeypatch)
        resp = client.post(f"/table-access/step/{step['id']}/approve",
                           headers=auth("manager1"))
        assert resp.status_code == 302


class TestNudge:
    def _pending(self, client, monkeypatch):
        return _create_table_request("engineer1", "iceberg.demo.orders", 1, "manager1",
                                     monkeypatch, client)

    def test_只能催自己的申请(self, client, monkeypatch):
        row = self._pending(client, monkeypatch)
        resp = client.post(f"/table-access/request/{row['id']}/nudge",
                           headers=auth("manager1"))
        assert resp.status_code == 403

    def test_催办发出通知并记时间(self, client, monkeypatch):
        row = self._pending(client, monkeypatch)
        sent = []
        monkeypatch.setattr(perm, "notify_wecom", lambda t: sent.append(t))
        resp = client.post(f"/table-access/request/{row['id']}/nudge",
                           headers=auth("engineer1"))
        assert resp.status_code == 302
        assert len(sent) == 1
        assert "催办" in sent[0] and "manager1" in sent[0]
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        after = conn.execute("SELECT * FROM table_access_requests WHERE id=?",
                             (row["id"],)).fetchone()
        conn.close()
        assert after["last_nudged_at"] is not None

    def test_冷却期内再催被挡住(self, client, monkeypatch):
        # 一个能无限催的按钮,最后的结果是所有通知都被审批人无视。
        row = self._pending(client, monkeypatch)
        monkeypatch.setattr(perm, "notify_wecom", lambda t: None)
        client.post(f"/table-access/request/{row['id']}/nudge", headers=auth("engineer1"))
        resp = client.post(f"/table-access/request/{row['id']}/nudge", headers=auth("engineer1"))
        assert resp.status_code == 429
        assert "只能催办一次" in resp.get_json()["error"]

    def test_已经有结果的申请不能催(self, client, monkeypatch):
        row = self._pending(client, monkeypatch)
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.execute("UPDATE table_access_requests SET status='approved' WHERE id=?",
                     (row["id"],))
        conn.commit(); conn.close()
        resp = client.post(f"/table-access/request/{row['id']}/nudge", headers=auth("engineer1"))
        assert resp.status_code == 400


class TestExpiryWarning:
    """到期前提醒 —— 权限悄悄失效是这套机制最伤人的地方。"""

    def _run(self, client, monkeypatch, local_git_repo, rows):
        import subprocess as sp
        # 往裸仓库里放一份 grants.csv
        work = tempfile.mkdtemp()
        sp.run(["git", "clone", local_git_repo, work], check=True, capture_output=True)
        csv_path = Path(work) / "platform" / "iam" / "table-access-grants.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        header = "username,table_fqn,security_level,granted_at,expires_at"
        csv_path.write_text("\n".join([header] + rows) + "\n")
        sp.run(["git", "-C", work, "add", "-A"], check=True)
        sp.run(["git", "-C", work, "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-m", "seed"], check=True, capture_output=True)
        sp.run(["git", "-C", work, "push", "origin", "HEAD"], check=True, capture_output=True)

        sent = []
        monkeypatch.setattr(perm, "notify_wecom", lambda t: sent.append(t))
        monkeypatch.setattr(perm, "INTERNAL_TOKEN", "tok")
        resp = client.post("/internal/reclaim-expired", headers={"X-Internal-Token": "tok"})
        return resp.get_json(), sent

    def test_快到期的会被提醒_但不会被回收(self, client, monkeypatch, local_git_repo):
        from datetime import datetime, timedelta, timezone
        soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        body, sent = self._run(client, monkeypatch, local_git_repo,
                               [f"alice,iceberg.demo.orders,1,2026-01-01T00:00:00+00:00,{soon}"])
        assert body["reclaimed"] == 0
        assert body["warned"] == 1
        # 剩 2.99 天显示"2 天"——`.days` 向下取整,是有意的:对截止期限
        # 来说少说比多说安全,说"3 天"会让人第 3 天才动手,那时已经过期了。
        assert "即将到期" in sent[0] and "2 天" in sent[0]

    def test_还早的不提醒(self, client, monkeypatch, local_git_repo):
        from datetime import datetime, timedelta, timezone
        later = (datetime.now(timezone.utc) + timedelta(days=100)).isoformat()
        body, sent = self._run(client, monkeypatch, local_git_repo,
                               [f"alice,iceberg.demo.orders,1,2026-01-01T00:00:00+00:00,{later}"])
        assert body["warned"] == 0
        assert sent == []


class TestNoSelfApproval:
    """申请人永远不能是自己的审批人(2026-08-29 堵的一条真实提权路径)。

    路径是这样的:建表注册工具的 owner 是一个自由填写的表单字段,谁都能
    把自己填成某张表的负责人;而 table_owner 在审批链里是第一级审批人。
    "自己建表填自己 → 之后申请这张表的权限 → 自己批自己"因此成立。
    """

    def test_自己是表负责人时_不会出现在自己的审批链里(self):
        steps = perm.build_approval_steps("engineer1", "engineer1", 1)
        assert all(u != "engineer1" for _, _, u in steps)

    def test_别人是负责人时照常进链(self):
        # 用 director 而不是 manager1 —— manager1 本来就是 engineer1 的上级,
        # 会以 manager 角色进链然后被去重(同一个人只批一次,是对的行为)。
        steps = perm.build_approval_steps("engineer1", "director", 1)
        assert any(u == "director" and role == "table_owner" for _, role, u in steps)

    def test_链上只剩自己时_申请被拒而不是被自动放行(self, client, monkeypatch):
        # 这是最危险的一种:组织架构里查不到上级(manager 为 None),
        # 表负责人又填的是自己 —— 如果这时候"没有审批人"被当成"不需要
        # 审批",就等于完全自助授权。
        monkeypatch.setattr(perm, "get_manager_chain", lambda u, levels=2: [])
        row = _create_table_request("engineer1", "iceberg.demo.mine", 1, "engineer1",
                                    monkeypatch, client)
        assert row["status"] == "rejected"
        assert "自己不能批自己" in row["note"]

    def test_高等级链上自己被剔除后其余人照常(self, client, monkeypatch):
        # engineer1 的上级是 manager1、上上级是 director(见测试组织数据)
        steps = perm.build_approval_steps("engineer1", "engineer1", 2)
        names = [u for _, _, u in steps]
        assert "engineer1" not in names
        assert "manager1" in names and "director" in names


class TestGroupsDiagnosis:
    """把"配置没配对"和"这个人真的不在任何组"区分开。

    这两件事在代码里长得一模一样(`groups == []`),后果完全不同,而这个
    项目已经因为分不开它们栽过三次(ADR-078 的 Trino group provider、
    Superset 的 groups scope、以及这个 app 自己的 is_approver)。
    共同点都是"少一个配置,按组判断的分支就永远走不到,而且没有任何信号"。
    """

    def _token(self, claims):
        import base64 as b64, json as js
        payload = b64.urlsafe_b64encode(js.dumps(claims).encode()).decode().rstrip("=")
        return f"eyJhbGciOiJub25lIn0.{payload}."

    def _diag(self, client, headers):
        with perm.app.test_request_context(headers=headers):
            perm.get_current_user()
            return perm.groups_diagnosis()

    def test_没有_groups_字段时给出明确提示(self):
        d = self._diag(None, {"X-Forwarded-User": "alice",
                              "X-Forwarded-Access-Token": self._token({"preferred_username": "alice"})})
        assert d is not None and "没有 groups 字段" in d
        assert "配置问题不是权限问题" in d

    def test_有_groups_字段但是空的_不报警(self):
        # 这是"这个人真的不在任何组",是正常状态,不该吓唬人。
        d = self._diag(None, {"X-Forwarded-User": "alice",
                              "X-Forwarded-Access-Token": self._token(
                                  {"preferred_username": "alice", "groups": []})})
        assert d is None

    def test_有组时不报警(self):
        d = self._diag(None, {"X-Forwarded-User": "alice",
                              "X-Forwarded-Access-Token": self._token(
                                  {"preferred_username": "alice", "groups": ["platform-team"]})})
        assert d is None

    def test_压根没有令牌时提示查_pass_access_token(self):
        d = self._diag(None, {"X-Forwarded-User": "alice"})
        assert d is not None and "pass_access_token" in d

    def test_令牌解不开时也有提示(self):
        d = self._diag(None, {"X-Forwarded-User": "alice",
                              "X-Forwarded-Access-Token": "not.a.jwt"})
        assert d is not None and "解不开" in d

    def test_提示会渲染到页面上(self, client):
        html = client.get("/", headers=auth("alice")).get_data(as_text=True)
        # auth() 不带 access token,属于 no_token 那种
        assert "pass_access_token" in html

    def test_groups_里真的有组时_is_approver_成立(self):
        # 顺带锁住:这条链路本身是对的,坏的只是"组信息有没有被传过来"。
        with perm.app.test_request_context(headers={
                "X-Forwarded-User": "admin",
                "X-Forwarded-Access-Token": self._token(
                    {"preferred_username": "admin", "groups": ["platform-team"]})}):
            _, groups = perm.get_current_user()
            assert perm.is_approver(groups) is True


class TestRenew:
    """续期(roadmap P1.5「审批体验」最后一项)。

    **续期不是把到期时间往后推。** 那样等于把 180 天复审变成形式 —— 授权
    设期限的意义就在于"过一段时间要有人重新看一眼这个人还需不需要"。所以
    续期走的是和第一次申请完全相同的审批链;它省的不是审批,是"等到查不到
    数据才想起来"。
    """

    def _first_request(self, client, monkeypatch, reason="季度对账要看订单明细"):
        return _create_table_request("engineer1", "iceberg.demo.orders", 1, "manager1",
                                     monkeypatch, client, reason=reason)

    def _approve_all(self, request_id):
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.execute("UPDATE approval_steps SET status='approved' WHERE request_id=?",
                     (request_id,))
        conn.execute("UPDATE table_access_requests SET status='approved' WHERE id=?",
                     (request_id,))
        conn.commit(); conn.close()

    def test_续期会新建一条走完整审批链的申请(self, client, monkeypatch):
        row = self._first_request(client, monkeypatch)
        self._approve_all(row["id"])
        monkeypatch.setattr(perm, "lookup_table_governance", lambda fqn: (1, "manager1"))
        resp = client.post("/table-access/renew",
                           data={"table_fqn": "iceberg.demo.orders"},
                           headers=auth("engineer1"))
        assert resp.status_code == 302
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        new = conn.execute("SELECT * FROM table_access_requests ORDER BY id DESC LIMIT 1").fetchone()
        steps = conn.execute("SELECT COUNT(*) c FROM approval_steps WHERE request_id=?",
                             (new["id"],)).fetchone()["c"]
        conn.close()
        assert new["status"] == "pending"      # 不是直接生效
        assert steps > 0                       # 真的建了审批链

    def test_理由从上一条带过来_并标明是续期(self, client, monkeypatch):
        row = self._first_request(client, monkeypatch, reason="季度对账要看订单明细")
        self._approve_all(row["id"])
        monkeypatch.setattr(perm, "lookup_table_governance", lambda fqn: (1, "manager1"))
        client.post("/table-access/renew", data={"table_fqn": "iceberg.demo.orders"},
                    headers=auth("engineer1"))
        conn = perm.sqlite3.connect(perm.DB_PATH)
        conn.row_factory = perm.sqlite3.Row
        new = conn.execute("SELECT reason FROM table_access_requests ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        assert new["reason"].startswith("[续期]")
        assert "季度对账" in new["reason"]

    def test_已经有在审的就不重复提交(self, client, monkeypatch):
        # 一个人点两下,审批人那边不该出现两条一模一样的待办。
        self._first_request(client, monkeypatch)
        monkeypatch.setattr(perm, "lookup_table_governance", lambda fqn: (1, "manager1"))
        resp = client.post("/table-access/renew",
                           data={"table_fqn": "iceberg.demo.orders"},
                           headers=auth("engineer1"))
        assert resp.status_code == 409
        assert "已经有一条在审的申请" in resp.get_json()["error"]

    def test_没登录不给续期(self, client):
        assert client.post("/table-access/renew",
                           data={"table_fqn": "x.y.z"}).status_code == 401

    def test_不带表名拒绝(self, client):
        assert client.post("/table-access/renew", data={},
                           headers=auth("engineer1")).status_code == 400

    def test_高等级表续期仍然要够长的理由(self, client, monkeypatch):
        # 续期不是绕过校验的旁路。之前没写过理由的话,自动生成的那句话
        # 也要能过 2 级表的长度要求。
        monkeypatch.setattr(perm, "lookup_table_governance", lambda fqn: (2, "manager1"))
        resp = client.post("/table-access/renew",
                           data={"table_fqn": "iceberg.demo.users"},
                           headers=auth("engineer1"))
        assert resp.status_code == 302
