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


def _token(groups):
    """伪造一个 oauth2-proxy 会传下来的 access token(不验签,见
    shared/flask_identity.py 里说明为什么不验)。"""
    import base64 as b64, json as js
    payload = b64.urlsafe_b64encode(
        js.dumps({"preferred_username": "zhenghe", "groups": groups}).encode()
    ).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{payload}."


class TestParseColumns:
    def test_valid_single_column(self):
        assert reg.parse_columns("order_id BIGINT") == [("order_id", "BIGINT", None)]

    def test_valid_multiple_columns(self):
        result = reg.parse_columns("order_id BIGINT\nname VARCHAR(100)\namount DOUBLE")
        assert result == [
            ("order_id", "BIGINT", None),
            ("name", "VARCHAR(100)", None),
            ("amount", "DOUBLE", None),
        ]

    def test_skips_blank_lines(self):
        result = reg.parse_columns("order_id BIGINT\n\n\nname VARCHAR(50)")
        assert len(result) == 2

    def test_type_is_uppercased(self):
        """用户输小写类型也应该能过,内部统一转大写再给 Trino。"""
        result = reg.parse_columns("id bigint")
        assert result == [("id", "BIGINT", None)]

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
        # 2026-08-29 起 DECIMAL 在白名单里了(此前不在,而表单示例给的正是
        # `amount DECIMAL(10,2)` —— 照着示例填的人第一次提交就会被打回)。
        assert reg.parse_columns("price DECIMAL(10,2)") == [("price", "DECIMAL(10,2)", None)]
        assert reg.parse_columns("name VARCHAR(100)") == [("name", "VARCHAR(100)", None)]

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
        mock_create.assert_called_once_with("iceberg", "demo", "orders", [("id", "BIGINT", None), ("name", "VARCHAR(50)", None)], [])
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
                # 2026-08-29 起 2 级表要先审批,平台组除外 —— 这条测的是
                # "建成之后会发生什么",所以用平台组身份跳过审批那一层。
                headers={"X-Forwarded-User": "zhenghe",
                         "X-Forwarded-Access-Token": _token(["platform-team"])},
            )
        assert resp.status_code == 302
        mock_create.assert_called_once()
        # 这里用的是 platform-team 身份(见上面 headers),所以表单里的
        # owner 生效 —— 代他人建表是平台组才有的能力。非平台组填了无效那条
        # 由 TestOwnerOverrideNeedsPlatformTeam 覆盖。
        mock_om.assert_called_once_with("iceberg", "demo", "orders", [("id", "BIGINT", None)], "someone", 2)
        row = sqlite3.connect(reg.DB_PATH).execute(
            "SELECT trino_status, openmetadata_status, security_level, owner FROM registrations ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row == ("ok", "ok", 2, "someone")   # 同上:平台组代建,负责人是表单里那个

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


class TestOwnerOverrideNeedsPlatformTeam:
    """代他人建表要在 platform-team,其他人不行。

    表负责人是权限审批链的第一级审批人,所以"能指定别人当负责人"等于
    "能安排审批人",不该是所有人都有的能力。
    """

    def _submit(self, client, form_owner, headers):
        with patch.object(reg, "create_table_in_trino"), \
             patch.object(reg, "OPENMETADATA_TOKEN", "fake-token"), \
             patch.object(reg, "register_table_in_openmetadata", return_value="ok") as om:
            client.post("/submit",
                        data={"table_fqn": "demo.t9", "columns": "id BIGINT",
                              "security_level": "1", "owner": form_owner},
                        headers=headers)
        return om.call_args[0][4]

    def test_平台组可以指定别人(self, client):
        owner = self._submit(client, "analyst001", {
            "X-Forwarded-User": "zhenghe",
            "X-Forwarded-Access-Token": _token(["platform-team"])})
        assert owner == "analyst001"

    def test_平台组不填就是自己(self, client):
        owner = self._submit(client, "", {
            "X-Forwarded-User": "zhenghe",
            "X-Forwarded-Access-Token": _token(["platform-team"])})
        assert owner == "zhenghe"

    def test_不在平台组的人填了也无效(self, client):
        owner = self._submit(client, "victim001", {
            "X-Forwarded-User": "zhenghe",
            "X-Forwarded-Access-Token": _token(["data-analysts"])})
        assert owner == "zhenghe"

    def test_拿不到组信息时按不能处理(self, client):
        # 和门户那边"拿不到就显示全部"相反 —— 那边多显示几个进不去的入口
        # 没有代价,这边放过去就是一个越权写入。同一个不确定状态,依据是
        # "错的那一边代价多大"。
        owner = self._submit(client, "victim001", {"X-Forwarded-User": "zhenghe"})
        assert owner == "zhenghe"

    def test_页面按角色决定那个框能不能编辑(self, client):
        plat = client.get("/", headers={"X-Forwarded-User": "zhenghe",
                                        "X-Forwarded-Access-Token": _token(["platform-team"])}
                          ).get_data(as_text=True)
        other = client.get("/", headers={"X-Forwarded-User": "zhenghe",
                                         "X-Forwarded-Access-Token": _token(["data-analysts"])}
                           ).get_data(as_text=True)
        assert 'name="owner"' in plat and "disabled" not in plat.split("负责人")[1][:200]
        assert "disabled" in other


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


class TestColumnComments:
    def test_三段式带说明(self):
        assert reg.parse_columns("order_id BIGINT # 订单号") == [("order_id", "BIGINT", "订单号")]

    def test_旧的两段式仍然有效(self):
        # 不写 # 就是没有说明 —— 这个格式在仓库里已经有人用了,不能破坏。
        assert reg.parse_columns("order_id BIGINT") == [("order_id", "BIGINT", None)]

    def test_空说明当成没有(self):
        assert reg.parse_columns("id BIGINT #   ") == [("id", "BIGINT", None)]

    def test_说明里的井号只吃第一个(self):
        assert reg.parse_columns("id BIGINT # a # b") == [("id", "BIGINT", "a # b")]

    def test_列名重复被挡住(self):
        # Trino 也会报错,但报错里不说是哪一行,而且那时表单内容已经丢了。
        with pytest.raises(ValueError, match="列名重复"):
            reg.parse_columns("a BIGINT\nA INT")


class TestPartitioning:
    def _cols(self):
        return reg.parse_columns("ts TIMESTAMP\nregion VARCHAR\namount DECIMAL(10,2)")

    def test_空的就是不分区(self):
        assert reg.parse_partitioning("", self._cols()) == []

    def test_列名和时间函数都支持(self):
        assert reg.parse_partitioning("day(ts), region", self._cols()) == ["day(ts)", "region"]

    def test_bucket_和_truncate_带参数(self):
        # 这条抓到过一个真 bug:先按逗号切的话,`bucket(region, 8)` 里面
        # 本身就有逗号,会被切成 `bucket(region` 和 `8)`。
        assert reg.parse_partitioning("bucket(region, 8)", self._cols()) == ["bucket(region, 8)"]

    def test_多个表达式里混着带参数的(self):
        assert reg.parse_partitioning("day(ts), bucket(region, 8), amount",
                                      self._cols()) == ["day(ts)", "bucket(region, 8)", "amount"]

    def test_任意表达式被挡住(self):
        # 这个字段最后要拼进 DDL,白名单比转义可靠。
        for bad in ("drop table x", "ts); DROP TABLE y --", "nonexistent(ts)"):
            with pytest.raises(ValueError):
                reg.parse_partitioning(bad, self._cols())

    def test_引用不存在的列被挡住(self):
        with pytest.raises(ValueError, match="不在字段列表里"):
            reg.parse_partitioning("day(no_such_col)", self._cols())


class TestBuildDdl:
    def test_带说明和分区(self):
        cols = reg.parse_columns("id BIGINT # 主键\nts TIMESTAMP")
        ddl = reg.build_ddl("iceberg", "demo", "orders", cols, ["day(ts)"])
        assert "COMMENT '主键'" in ddl
        assert "partitioning = ARRAY['day(ts)']" in ddl
        assert ddl.startswith("CREATE TABLE IF NOT EXISTS iceberg.demo.orders")

    def test_不分区就不带_with(self):
        cols = reg.parse_columns("id BIGINT")
        assert "partitioning" not in reg.build_ddl("iceberg", "demo", "t", cols, [])

    def test_说明里的单引号被转义(self):
        cols = reg.parse_columns("id BIGINT # it's fine")
        assert "COMMENT 'it''s fine'" in reg.build_ddl("iceberg", "demo", "t", cols)


class TestPreview:
    def test_预览和真正建表用同一份_ddl(self, client):
        # 预览显示一段 SQL、实际跑另一段,比没有预览更糟。
        data = {"table_fqn": "demo.orders", "columns": "id BIGINT # 主键\nts TIMESTAMP",
                "partitioning": "day(ts)"}
        shown = client.post("/preview", data=data,
                            headers={"X-Forwarded-User": "zhenghe"}).get_json()["ddl"]

        with patch.object(reg, "trino") as t, \
             patch.object(reg, "TRINO_PASSWORD", "pw"):
            cols = reg.parse_columns(data["columns"])
            reg.create_table_in_trino("iceberg", "demo", "orders", cols,
                                      reg.parse_partitioning("day(ts)", cols))
            executed = [c.args[0] for c in t.dbapi.connect.return_value.cursor.return_value.execute.call_args_list]
        assert shown in executed

    def test_格式错误返回_200_加_error_不是_400(self, client):
        # 边填边看,填到一半格式不对是常态,不该在控制台留一串红色。
        r = client.post("/preview", data={"table_fqn": "demo.t", "columns": "坏行"},
                        headers={"X-Forwarded-User": "zhenghe"})
        assert r.status_code == 200 and "error" in r.get_json()

    def test_没登录不给预览(self, client):
        assert client.post("/preview", data={}).status_code == 401


class TestQualityRules:
    def test_只保留真实存在的列(self):
        cols = reg.parse_columns("order_id BIGINT\nname VARCHAR")
        # 写错列名的话,OpenMetadata 会建出一条指向不存在列的断言 —— 它不会
        # 报错,只会永远失败,而一条永远红的检查比没有检查更糟。
        assert reg._column_list("order_id, nope, NAME, order_id", cols) == ["order_id", "name"]

    def test_没勾任何规则就不调_openmetadata(self):
        with patch.object(reg, "requests") as rq:
            assert reg.create_quality_tests("iceberg", "demo", "t", set(), [], []) == ""
            rq.post.assert_not_called()

    def test_建断言失败不抛出去(self):
        # 表已经建好、目录也登记了,不该因为断言没挂上就显示"失败" ——
        # 那会让人以为要重新建表。
        with patch.object(reg, "requests") as rq:
            rq.get.side_effect = RuntimeError("boom")
            msg = reg.create_quality_tests("iceberg", "demo", "t",
                                           {"row_count_not_empty"}, [], [])
        assert "没建成" in msg

    def test_已存在的断言算成功(self):
        # 409 = 幂等重跑,不是错误。
        with patch.object(reg, "requests") as rq, \
             patch.object(reg, "om_request"):
            rq.get.return_value.status_code = 200
            rq.post.return_value.status_code = 409
            msg = reg.create_quality_tests("iceberg", "demo", "t",
                                           {"row_count_not_empty"}, [], [])
        assert "已挂 1 条" in msg and "没建成" not in msg

    def test_entity_link_指向具体的列(self):
        sent = []
        with patch.object(reg, "requests") as rq, patch.object(reg, "om_request"):
            rq.get.return_value.status_code = 200
            rq.post.side_effect = lambda *a, **k: (sent.append(k["json"]),
                                                   MagicMock(status_code=201))[1]
            reg.create_quality_tests("iceberg", "demo", "orders", {"unique"},
                                     ["order_id"], [])
        assert sent[0]["entityLink"] == "<#E::table::trino.iceberg.demo.orders::columns::order_id>"
        assert sent[0]["testDefinition"] == "columnValuesToBeUnique"
        # body 里不能带 testSuite —— 带上 1.13.3 一律 400,套件从 entityLink 推断
        assert "testSuite" not in sent[0]


class TestApprovalGate:
    """哪些角色可直接建表、哪些要审批(roadmap P1.5 验收项)。

    **规则按安全等级切,不按人切**:1 级表谁都能建 —— 建表是日常工作,卡在
    审批上只会逼人绕过平台直接连 Trino 写 DDL,那样建出来的表在数据目录里
    是隐形的,比"没有审批"糟得多。
    """

    def _post(self, client, level, groups=None):
        headers = {"X-Forwarded-User": "zhenghe"}
        if groups is not None:
            headers["X-Forwarded-Access-Token"] = _token(groups)
        with patch.object(reg, "create_table_in_trino") as create, \
             patch.object(reg, "OPENMETADATA_TOKEN", ""):
            client.post("/submit",
                        data={"table_fqn": f"demo.t{level}", "columns": "id BIGINT",
                              "security_level": str(level)},
                        headers=headers)
        return create

    def _row(self, table):
        conn = reg.sqlite3.connect(reg.DB_PATH)
        r = conn.execute("SELECT trino_status, note FROM registrations WHERE table_fqn=?",
                         (table,)).fetchone()
        conn.close()
        return r

    def test_一级表谁都能直接建(self, client):
        create = self._post(client, 1, ["data-analysts"])
        create.assert_called_once()

    def test_二级表非平台组建不了_而且没真的碰_trino(self, client):
        create = self._post(client, 2, ["data-analysts"])
        create.assert_not_called()
        status, note = self._row("demo.t2")
        assert status == "rejected" and "先审批" in note

    def test_被挡住时留下的记录说清楚该去哪(self, client):
        # 静默拒绝或者假装建成了都更糟 —— 人得知道下一步做什么。
        self._post(client, 3, ["data-analysts"])
        _, note = self._row("demo.t3")
        assert "权限申请门户" in note and "platform-team" in note

    def test_平台组不受这条限制(self, client):
        # 他们本来就能直连 Trino,在这里拦只是让他们绕路。
        create = self._post(client, 3, ["platform-team"])
        create.assert_called_once()

    def test_拿不到组信息时按需要审批处理(self, client):
        # 和门户"拿不到就显示全部"相反,和 owner 覆盖那条一致:错的那一边
        # 代价大的时候,选保守。
        create = self._post(client, 2, None)
        create.assert_not_called()

    def test_不是平台组的人看得到这条规则(self, client):
        html = client.get("/", headers={"X-Forwarded-User": "zhenghe",
                                        "X-Forwarded-Access-Token": _token(["data-analysts"])}
                          ).get_data(as_text=True)
        assert "要先在权限申请门户" in html

    def test_平台组不显示那句提示(self, client):
        html = client.get("/", headers={"X-Forwarded-User": "zhenghe",
                                        "X-Forwarded-Access-Token": _token(["platform-team"])}
                          ).get_data(as_text=True)
        assert "要先在权限申请门户" not in html
