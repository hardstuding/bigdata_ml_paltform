"""生成 Superset SQL Lab 的深链 —— 「从数据目录里的一张表一键跳到查询」。

背景和取舍见 docs/decisions/084-analyst-sql-workbench.md。这里只记两件
动手前必须知道的事:

1. **SQL Lab 不认 URL 参数。** Superset 6.1 的 `SqllabView.root`
   (superset/views/sqllab.py)只读 POST 的 `form_data`,不读 query string。
   拼一个 `?db=x&schema=y&table=z` 出来,SQL Lab 会**安静地开一个空编辑器**
   ——不报错、不提示,和链接坏了长得一模一样。这个项目已经被好几个
   "不报错但不生效"坑过,这条写在最前面。

2. 支持的机制是 **permalink**:先 POST 一份编辑器状态换一个 key,再把用户
   送到 `/sqllab/p/<key>/`。字段名抄自 superset/sqllab/permalink/schemas.py
   (6.1.0),**是 dbId 这种驼峰,不是 db_id**——写成下划线不会报错,
   marshmallow 直接把它当未知字段丢掉,又是一个静默失败。

**权限**:创建 permalink 用的是平台的 Superset 服务账号,但这**不构成一条
绕过权限的旁路**——permalink 里存的只是"编辑器里预填什么",不是数据。
用户打开 `/sqllab/p/<key>/` 时走的是他自己的 SSO 会话,查询由 Trino 按
他本人的身份执行(impersonation,ADR-074)。服务账号能造出一个查 X 表的
链接,不代表点链接的人查得动 X 表。
"""

from __future__ import annotations

import json
import os
import urllib.request

# 集群内直连,不绕外部 ingress。
SUPERSET_INTERNAL = os.environ.get(
    "SUPERSET_INTERNAL_URL", "http://superset.superset.svc.cluster.local:8088"
)


class SqlLabLinkUnavailable(RuntimeError):
    """没配服务账号,或者 Superset 那边没给出 permalink。

    调用方应当**降级到普通的 SQL Lab 链接**,而不是把错误抛给用户——
    少一个预填的编辑器是小事,门户上出现一个报错页是大事。
    """


def default_query(catalog: str, schema: str, table: str, limit: int = 100) -> str:
    """给一张表配一句能直接跑的 SQL。

    带 limit 是有意的:数据目录里一张表可能是几亿行,一键跳过去如果直接
    全表扫,对点的人和对集群都不友好。
    """
    return f"SELECT *\nFROM {catalog}.{schema}.{table}\nLIMIT {limit}\n"


def build_permalink_payload(db_id: int, catalog: str, schema: str, table: str,
                            sql: str | None = None, autorun: bool = False) -> dict:
    """拼 permalink 的请求体。单独拆出来是为了能不发请求就测字段名。"""
    return {
        "dbId": db_id,
        "catalog": catalog,
        "schema": schema,
        "sql": sql if sql is not None else default_query(catalog, schema, table),
        "name": f"{schema}.{table}",
        "autorun": autorun,
    }


def _login(base: str, user: str, password: str, opener) -> str:
    req = urllib.request.Request(
        f"{base}/api/v1/security/login",
        data=json.dumps({"username": user, "password": password,
                         "provider": "db", "refresh": False}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.load(opener(req, timeout=10))["access_token"]


def table_query_link(catalog: str, schema: str, table: str, db_id: int | None = None,
                     sql: str | None = None, base: str | None = None,
                     opener=urllib.request.urlopen) -> str:
    """返回 SQL Lab 里预填好这张表的**相对路径**(比如 /sqllab/p/abc123/)。

    返回相对路径而不是完整 URL,是因为对外的域名/端口只有 app.py 里那套
    环境配置知道(三档环境不一样),这个模块不该也去猜一遍。

    凭据缺失或者 Superset 不给 permalink,一律抛 SqlLabLinkUnavailable,
    由调用方降级。
    """
    user = os.environ.get("PORTAL_SUPERSET_USER", "")
    password = os.environ.get("PORTAL_SUPERSET_PASSWORD", "")
    if db_id is None:
        # 环境变量没配时是空字符串,int("") 会抛 ValueError —— 那会变成一个
        # 500,而不是我们想要的"降级成普通入口"。
        raw = os.environ.get("PORTAL_SUPERSET_TRINO_DB_ID", "").strip()
        db_id = int(raw) if raw.isdigit() else 0
    if not user or not password or not db_id:
        raise SqlLabLinkUnavailable(
            "没配 PORTAL_SUPERSET_USER / PORTAL_SUPERSET_PASSWORD / "
            "PORTAL_SUPERSET_TRINO_DB_ID,深链降级成普通 SQL Lab 入口"
        )
    base = base or SUPERSET_INTERNAL
    try:
        token = _login(base, user, password, opener)
        req = urllib.request.Request(
            f"{base}/api/v1/sqllab/permalink",
            data=json.dumps(
                build_permalink_payload(db_id, catalog, schema, table, sql)
            ).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
            method="POST",
        )
        body = json.load(opener(req, timeout=10))
    except SqlLabLinkUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 —— 上游什么错都可能,统一降级
        raise SqlLabLinkUnavailable(f"向 Superset 申请 permalink 失败:{exc}") from exc
    url = body.get("url")
    if not url:
        raise SqlLabLinkUnavailable(f"Superset 没返回 url,拿到的是:{body}")
    # Superset 返回的可能是完整 URL(带它自己以为的域名),统一成相对路径,
    # 免得把一个内部地址甩到用户浏览器上。
    if "://" in url:
        url = "/" + url.split("://", 1)[1].split("/", 1)[1]
    return url
