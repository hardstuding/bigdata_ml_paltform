"""table-registration-app 的测试——见 docs/BACKLOG.md P1"三个自建 Flask
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
        mock_om.assert_called_once_with("iceberg", "demo", "orders", [("id", "BIGINT")], "someone", 2)
        row = sqlite3.connect(reg.DB_PATH).execute(
            "SELECT trino_status, openmetadata_status, security_level, owner FROM registrations ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row == ("ok", "ok", 2, "someone")

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
