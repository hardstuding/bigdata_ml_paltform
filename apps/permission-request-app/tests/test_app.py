"""permission-request-app 的测试——见 docs/BACKLOG.md P1"三个自建 Flask
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
