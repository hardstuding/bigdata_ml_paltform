"""table-registration-app 的测试——见 docs/project/roadmap.md P1"三个自建 Flask
工具补测试"那条。重点测两块:
1. parse_columns()/parse_table_fqn() 这两个纯校验函数——用户输入的第一道
   关卡,错了要么放过了不合法的表名/类型直接怼给 Trino 执行,要么把合法
   输入误判成不合法,两种错误都要靠真实用例覆盖,不能只看代码顺眼。
2. /submit 路由的状态机(trino_status/openmetadata_status 两个字段的
   四种组合:校验失败/建表失败/OM未配置跳过/全部成功)——这是这个工具
   唯一复杂的业务逻辑,Trino/OpenMetadata 都用 mock,不连真实服务。

跑法:
  cd apps/table-registration-app && python3 -m pytest tests/ -v
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# DB_PATH 在模块导入时就会被 init_db() 用来建表,必须在 import app 之前设好,
# 指向一个临时文件,不能碰真实的 /data/registrations.db。
_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DB_PATH"] = _TMP_DB.name

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import app as reg  # noqa: E402


@pytest.fixture
def client():
    reg.app.config["TESTING"] = True
    # 每个测试前清空表,测试之间不应该互相影响
    conn = sqlite3.connect(reg.DB_PATH)
    conn.execute("DELETE FROM registrations")
    conn.commit()
    conn.close()
    with reg.app.test_client() as c:
        yield c


class TestParseColumns:
    def test_valid_single_column(self):
        assert reg.parse_columns("order_id BIGINT") == [("order_id", "BIGINT")]

    def test_valid_multiple_columns(self):
        result = reg.parse_columns("order_id BIGINT\nname VARCHAR(100)\namount DOUBLE")
        assert result == [
            ("order_id", "BIGINT"),
            ("name", "VARCHAR(100)"),
            ("amount", "DOUBLE"),
        ]

    def test_skips_blank_lines(self):
        result = reg.parse_columns("order_id BIGINT\n\n\nname VARCHAR(50)")
        assert len(result) == 2

    def test_type_is_uppercased(self):
        """用户输小写类型也应该能过,内部统一转大写再给 Trino。"""
        result = reg.parse_columns("id bigint")
        assert result == [("id", "BIGINT")]

    def test_rejects_missing_type(self):
        with pytest.raises(ValueError, match="格式不对"):
            reg.parse_columns("order_id")

    def test_rejects_invalid_identifier(self):
        """列名不能以数字开头、不能有特殊字符——防止直接拼进 SQL 语句里
        出问题(这个工具用的是 f-string 拼 SQL,不是参数化查询,列名/类型
        这两处校验是唯一的防线,必须测严格)。"""
        with pytest.raises(ValueError, match="列名不合法"):
            reg.parse_columns("1id BIGINT")

    def test_rejects_sql_injection_attempt_in_name(self):
        with pytest.raises(ValueError, match="列名不合法"):
            reg.parse_columns("id;DROP_TABLE BIGINT")

    def test_rejects_sql_injection_attempt_in_type(self):
        with pytest.raises(ValueError, match="类型不在支持列表"):
            reg.parse_columns("id BIGINT); DROP TABLE users; --")

    def test_rejects_unsupported_type(self):
        with pytest.raises(ValueError, match="类型不在支持列表"):
            reg.parse_columns("data JSON")

    def test_rejects_empty_input(self):
        with pytest.raises(ValueError, match="至少要有一列"):
            reg.parse_columns("")

    def test_rejects_whitespace_only_input(self):
        with pytest.raises(ValueError, match="至少要有一列"):
            reg.parse_columns("   \n  \n")

    def test_accepts_type_with_precision(self):
        # 项目支持的类型白名单里没有 DECIMAL,用 VARCHAR 确认精度语法本身能过
        result = reg.parse_columns("price VARCHAR(10, 2)")
        assert result == [("price", "VARCHAR(10, 2)")]

    def test_error_message_includes_line_number(self):
        with pytest.raises(ValueError, match="第 2 行"):
            reg.parse_columns("id BIGINT\nbad_line_no_type")


class TestParseTableFqn:
    def test_two_part_uses_default_catalog(self):
        assert reg.parse_table_fqn("demo.orders") == (reg.DEFAULT_CATALOG, "demo", "orders")

    def test_three_part_explicit_catalog(self):
        assert reg.parse_table_fqn("iceberg.demo.orders") == ("iceberg", "demo", "orders")

    def test_rejects_single_part(self):
        with pytest.raises(ValueError, match="schema.table"):
            reg.parse_table_fqn("orders")

    def test_rejects_too_many_parts(self):
        with pytest.raises(ValueError, match="schema.table"):
            reg.parse_table_fqn("a.b.c.d")

    def test_rejects_invalid_characters(self):
        with pytest.raises(ValueError, match="不合法"):
            reg.parse_table_fqn("demo.orders; DROP TABLE x")

    def test_strips_whitespace(self):
        assert reg.parse_table_fqn("  demo.orders  ") == (reg.DEFAULT_CATALOG, "demo", "orders")


class TestGetCurrentUser:
    def test_prefers_forwarded_user_over_email(self, client):
        with reg.app.test_request_context(
            headers={"X-Forwarded-User": "zhenghe", "X-Forwarded-Email": "zhenghe@example.com"}
        ):
            assert reg.get_current_user() == "zhenghe"

    def test_falls_back_to_email(self, client):
        with reg.app.test_request_context(headers={"X-Forwarded-Email": "zhenghe@example.com"}):
            assert reg.get_current_user() == "zhenghe@example.com"

    def test_empty_when_no_headers(self, client):
        with reg.app.test_request_context():
            assert reg.get_current_user() == ""


class TestSubmitRoute:
    def test_rejects_anonymous(self, client):
        resp = client.post("/submit", data={"table_fqn": "demo.orders", "columns": "id BIGINT"})
        assert resp.status_code == 401

    def test_invalid_input_rejected_before_touching_trino(self, client):
        """校验失败应该直接标记 rejected,完全不应该调用 create_table_in_trino
        ——校验和执行必须是两个独立阶段,不能让非法输入碰到 Trino 连接。"""
        with patch.object(reg, "create_table_in_trino") as mock_create:
            resp = client.post(
                "/submit",
                data={"table_fqn": "not_valid", "columns": "id BIGINT"},
                headers={"X-Forwarded-User": "zhenghe"},
            )
        assert resp.status_code == 302
        mock_create.assert_not_called()
        row = sqlite3.connect(reg.DB_PATH).execute(
            "SELECT trino_status, openmetadata_status FROM registrations ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row == ("rejected", "skipped")

    def test_successful_submission_without_openmetadata_token(self, client):
        """OPENMETADATA_TOKEN 没配置时,建表本身应该照常成功,只是 OM 回写
        跳过——这是这个工具明确设计的降级行为(见模块顶部注释),不能因为
        没配 token 就连 Trino 建表都失败。"""
        with patch.object(reg, "create_table_in_trino") as mock_create, \
             patch.object(reg, "OPENMETADATA_TOKEN", ""):
            resp = client.post(
                "/submit",
                data={"table_fqn": "demo.orders", "columns": "id BIGINT\nname VARCHAR(50)"},
                headers={"X-Forwarded-User": "zhenghe"},
            )
        assert resp.status_code == 302
        mock_create.assert_called_once_with("iceberg", "demo", "orders", [("id", "BIGINT"), ("name", "VARCHAR(50)")])
        row = sqlite3.connect(reg.DB_PATH).execute(
            "SELECT trino_status, openmetadata_status FROM registrations ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row == ("ok", "skipped")

    def test_trino_failure_recorded(self, client):
        with patch.object(reg, "create_table_in_trino", side_effect=RuntimeError("connection refused")):
            resp = client.post(
                "/submit",
                data={"table_fqn": "demo.orders", "columns": "id BIGINT"},
                headers={"X-Forwarded-User": "zhenghe"},
            )
        assert resp.status_code == 302
        row = sqlite3.connect(reg.DB_PATH).execute(
            "SELECT trino_status, note FROM registrations ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row[0] == "failed"
        assert "connection refused" in row[1]

    def test_full_success_with_openmetadata(self, client):
        with patch.object(reg, "create_table_in_trino") as mock_create, \
             patch.object(reg, "OPENMETADATA_TOKEN", "fake-token"), \
             patch.object(reg, "register_table_in_openmetadata", return_value="ok") as mock_om:
            resp = client.post(
                "/submit",
                data={"table_fqn": "demo.orders", "columns": "id BIGINT", "security_level": "2", "owner": "someone"},
                headers={"X-Forwarded-User": "zhenghe"},
            )
        assert resp.status_code == 302
        mock_create.assert_called_once()
        # 2026-08-29:这条断言之前写的是 "someone" —— 也就是**在断言那个漏洞**。
        # 表单里的 owner 现在完全不看,负责人一律是登录者本人,原因见
        # app.py 里 submit() 那段注释(表负责人是审批链的第一级审批人)。
        mock_om.assert_called_once_with("iceberg", "demo", "orders", [("id", "BIGINT")], "zhenghe", 2)
        row = sqlite3.connect(reg.DB_PATH).execute(
            "SELECT trino_status, openmetadata_status, security_level, owner FROM registrations ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row == ("ok", "ok", 2, "zhenghe")   # 同上:落库的负责人也是登录者

    def test_invalid_security_level_defaults_to_1(self, client):
        with patch.object(reg, "create_table_in_trino"), patch.object(reg, "OPENMETADATA_TOKEN", ""):
            client.post(
                "/submit",
                data={"table_fqn": "demo.orders", "columns": "id BIGINT", "security_level": "99"},
                headers={"X-Forwarded-User": "zhenghe"},
            )
        row = sqlite3.connect(reg.DB_PATH).execute(
            "SELECT security_level FROM registrations ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row[0] == 1


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


class TestOwnerCannotBeSpoofed:
    """负责人只能是登录者本人(2026-08-29 堵的一条真实提权路径)。

    表负责人在 permission-request-app 里是**第一级审批人**。owner 能随便填
    的话,就能给自己安排一个好说话的审批人,或者干脆填自己然后批自己 ——
    组织架构里查不到上级的人,那条审批链上甚至只有他一个人。
    """

    def _submit(self, client, form_owner, login_as="zhenghe"):
        with patch.object(reg, "create_table_in_trino"), \
             patch.object(reg, "OPENMETADATA_TOKEN", "fake-token"), \
             patch.object(reg, "register_table_in_openmetadata", return_value="ok") as om:
            client.post("/submit",
                        data={"table_fqn": "demo.t1", "columns": "id BIGINT",
                              "security_level": "1", "owner": form_owner},
                        headers={"X-Forwarded-User": login_as})
        return om.call_args[0][4]      # owner 参数(catalog, schema, table, columns, owner, level)

    def test_填别人的名字无效(self, client):
        assert self._submit(client, "victim001") == "zhenghe"

    def test_不填也是登录者本人(self, client):
        assert self._submit(client, "") == "zhenghe"

    def test_落库记录的负责人也是登录者(self, client):
        with patch.object(reg, "create_table_in_trino"), \
             patch.object(reg, "OPENMETADATA_TOKEN", ""), \
             patch.object(reg, "register_table_in_openmetadata", return_value="ok"):
            client.post("/submit",
                        data={"table_fqn": "demo.t2", "columns": "id BIGINT",
                              "security_level": "1", "owner": "victim001"},
                        headers={"X-Forwarded-User": "zhenghe"})
        conn = reg.sqlite3.connect(reg.DB_PATH)
        row = conn.execute("SELECT owner FROM registrations WHERE table_fqn='demo.t2'").fetchone()
        conn.close()
        assert row[0] == "zhenghe"

    def test_页面上那个输入框是禁用的(self, client):
        html = client.get("/", headers={"X-Forwarded-User": "zhenghe"}).get_data(as_text=True)
        assert "disabled" in html
        assert 'name="owner"' not in html      # 干脆不提交这个字段


class TestReconcileOpenMetadata:
    """对账 + 重试。

    "Trino 里表建好了、OpenMetadata 里没有"这个半成功状态特别糟:表在目录里
    查不到、也查不到安全等级,而 permission-request-app 查不到安全等级时会
    **直接拒绝**这张表的所有权限申请 —— 表存在,但没人能通过正常流程拿到
    它的权限,而且谁也不会想到去建表工具的历史记录里翻那一行。
    """
    TOKEN = "tok"

    @pytest.fixture(autouse=True)
    def _token(self, monkeypatch):
        monkeypatch.setattr(reg, "INTERNAL_TOKEN", self.TOKEN)

    def _hdr(self):
        return {"X-Internal-Token": self.TOKEN}

    def _seed(self, om_status, table="demo.halfdone", trino_status="ok"):
        conn = reg.sqlite3.connect(reg.DB_PATH)
        conn.execute(
            "INSERT INTO registrations (requested_by, table_fqn, owner, security_level,"
            " columns_raw, trino_status, openmetadata_status, note, created_at)"
            " VALUES ('zhenghe',?,'zhenghe',1,'id BIGINT',?,?,'','2026-08-29T00:00:00+00:00')",
            (table, trino_status, om_status))
        conn.commit(); conn.close()

    def _status_of(self, table):
        conn = reg.sqlite3.connect(reg.DB_PATH)
        row = conn.execute(
            "SELECT openmetadata_status, note FROM registrations WHERE table_fqn=?",
            (table,)).fetchone()
        conn.close()
        return row

    def test_没有_token_403(self, client):
        assert client.post("/internal/reconcile-openmetadata").status_code == 403
        assert client.get("/internal/reconcile-status").status_code == 403

    def test_补写成功后状态变成_ok(self, client):
        self._seed("failed")
        with patch.object(reg, "OPENMETADATA_TOKEN", "fake"), \
             patch.object(reg, "register_table_in_openmetadata", return_value="ok"):
            body = client.post("/internal/reconcile-openmetadata",
                               headers=self._hdr()).get_json()
        assert body["fixed"] == ["demo.halfdone"]
        assert self._status_of("demo.halfdone")[0] == "ok"

    def test_skipped_的也会被补写(self, client):
        # OPENMETADATA_TOKEN 当时没配导致的 skipped,和 failed 一样是"目录里
        # 没有这张表",不能因为它当时不算错误就不管。
        self._seed("skipped", table="demo.skipped")
        with patch.object(reg, "OPENMETADATA_TOKEN", "fake"), \
             patch.object(reg, "register_table_in_openmetadata", return_value="ok"):
            body = client.post("/internal/reconcile-openmetadata",
                               headers=self._hdr()).get_json()
        assert "demo.skipped" in body["fixed"]

    def test_仍然失败的留着等下一轮_并记最新原因(self, client):
        self._seed("failed")
        with patch.object(reg, "OPENMETADATA_TOKEN", "fake"), \
             patch.object(reg, "register_table_in_openmetadata",
                          side_effect=RuntimeError("connection refused")):
            body = client.post("/internal/reconcile-openmetadata",
                               headers=self._hdr()).get_json()
        assert body["still_failing"] == ["demo.halfdone"]
        status, note = self._status_of("demo.halfdone")
        assert status == "failed"
        assert "connection refused" in note

    def test_一条失败不影响其余的(self, client):
        self._seed("failed", table="demo.a")
        self._seed("failed", table="demo.b")
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return "ok"

        with patch.object(reg, "OPENMETADATA_TOKEN", "fake"), \
             patch.object(reg, "register_table_in_openmetadata", flaky):
            body = client.post("/internal/reconcile-openmetadata",
                               headers=self._hdr()).get_json()
        assert body["still_failing"] == ["demo.a"] and body["fixed"] == ["demo.b"]

    def test_已经_ok_的不会被重复处理(self, client):
        self._seed("ok", table="demo.done")
        with patch.object(reg, "OPENMETADATA_TOKEN", "fake"), \
             patch.object(reg, "register_table_in_openmetadata") as om:
            client.post("/internal/reconcile-openmetadata", headers=self._hdr())
            om.assert_not_called()

    def test_trino_都没建成的不算半成功(self, client):
        # Trino 建表本身失败的,表压根不存在,不属于"表有但目录里没有"。
        self._seed("skipped", table="demo.nottino", trino_status="failed")
        with patch.object(reg, "OPENMETADATA_TOKEN", "fake"), \
             patch.object(reg, "register_table_in_openmetadata") as om:
            client.post("/internal/reconcile-openmetadata", headers=self._hdr())
            om.assert_not_called()

    def test_状态端点报出还有几张卡着(self, client):
        self._seed("failed", table="demo.x")
        self._seed("skipped", table="demo.y")
        body = client.get("/internal/reconcile-status", headers=self._hdr()).get_json()
        assert body["pending"] == 2
        assert {t["table"] for t in body["tables"]} == {"demo.x", "demo.y"}
