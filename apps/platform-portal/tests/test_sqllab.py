"""SQL Lab 深链的测试。

这里刻意不去 mock 一个"理想的 Superset",而是盯着两个**已知会静默失败**
的点(见 sqllab.py 顶部和 ADR-084):字段名必须是驼峰;凭据缺失必须降级
而不是抛给用户。
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import sqllab  # noqa: E402


class TestPayload(unittest.TestCase):
    def test_字段名是驼峰_不是下划线(self):
        # marshmallow 对未知字段是静默丢弃,写成 db_id 不会报错,只会
        # 得到一个没预填 database 的编辑器。这条就是防这个。
        p = sqllab.build_permalink_payload(3, "iceberg", "demo", "orders")
        self.assertEqual(p["dbId"], 3)
        self.assertNotIn("db_id", p)
        self.assertEqual(set(p), {"dbId", "catalog", "schema", "sql", "name", "autorun"})

    def test_默认_sql_带_limit(self):
        sql = sqllab.default_query("iceberg", "demo", "orders")
        self.assertIn("iceberg.demo.orders", sql)
        self.assertIn("LIMIT 100", sql)

    def test_默认不自动执行(self):
        # 一键跳过去就直接开跑,对一张大表是很不客气的行为。
        self.assertFalse(
            sqllab.build_permalink_payload(1, "a", "b", "c")["autorun"])


class TestDegradation(unittest.TestCase):
    """凭据没配时必须降级,不能让门户报错。"""

    def test_没配凭据_抛可降级的异常(self):
        with mock.patch.dict(os.environ, {"PORTAL_SUPERSET_USER": "",
                                          "PORTAL_SUPERSET_PASSWORD": "",
                                          "PORTAL_SUPERSET_TRINO_DB_ID": ""},
                             clear=False):
            with self.assertRaises(sqllab.SqlLabLinkUnavailable):
                sqllab.table_query_link("iceberg", "demo", "orders")

    def test_superset_报错也降级_不外泄原始异常类型(self):
        def boom(*a, **k):
            raise ConnectionRefusedError("connection refused")

        with mock.patch.dict(os.environ, {"PORTAL_SUPERSET_USER": "u",
                                          "PORTAL_SUPERSET_PASSWORD": "p",
                                          "PORTAL_SUPERSET_TRINO_DB_ID": "3"}):
            with self.assertRaises(sqllab.SqlLabLinkUnavailable):
                sqllab.table_query_link("iceberg", "demo", "orders", opener=boom)


class TestHappyPath(unittest.TestCase):
    def _fake_opener(self, permalink_body):
        calls = []

        def opener(req, timeout=None):
            calls.append(req)
            if req.full_url.endswith("/api/v1/security/login"):
                return io.BytesIO(json.dumps({"access_token": "tok"}).encode())
            return io.BytesIO(json.dumps(permalink_body).encode())

        return opener, calls

    def test_返回相对路径_并且带上_bearer(self):
        opener, calls = self._fake_opener({"key": "abc", "url": "/sqllab/p/abc/"})
        with mock.patch.dict(os.environ, {"PORTAL_SUPERSET_USER": "u",
                                          "PORTAL_SUPERSET_PASSWORD": "p",
                                          "PORTAL_SUPERSET_TRINO_DB_ID": "3"}):
            url = sqllab.table_query_link("iceberg", "demo", "orders", opener=opener)
        self.assertEqual(url, "/sqllab/p/abc/")
        self.assertEqual(calls[1].get_header("Authorization"), "Bearer tok")
        self.assertEqual(json.loads(calls[1].data)["dbId"], 3)

    def test_superset_返回完整_url_时剥成相对路径(self):
        # Superset 会用它自己配置里的域名拼 url,那个域名很可能是集群内
        # 地址,直接甩给浏览器是打不开的。
        opener, _ = self._fake_opener(
            {"key": "abc", "url": "http://superset.superset.svc:8088/sqllab/p/abc/"})
        with mock.patch.dict(os.environ, {"PORTAL_SUPERSET_USER": "u",
                                          "PORTAL_SUPERSET_PASSWORD": "p",
                                          "PORTAL_SUPERSET_TRINO_DB_ID": "3"}):
            url = sqllab.table_query_link("iceberg", "demo", "orders", opener=opener)
        self.assertEqual(url, "/sqllab/p/abc/")

    def test_没有_url_字段也降级(self):
        opener, _ = self._fake_opener({"key": "abc"})
        with mock.patch.dict(os.environ, {"PORTAL_SUPERSET_USER": "u",
                                          "PORTAL_SUPERSET_PASSWORD": "p",
                                          "PORTAL_SUPERSET_TRINO_DB_ID": "3"}):
            with self.assertRaises(sqllab.SqlLabLinkUnavailable):
                sqllab.table_query_link("iceberg", "demo", "orders", opener=opener)


if __name__ == "__main__":
    unittest.main()
