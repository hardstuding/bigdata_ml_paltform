"""平台内部共享的小工具。

**这个包本身也是发布链路的验证**:它能被 `pip install` 装上,就说明
「packages/ 下 push → 构建 wheel → 发到 MinIO → notebook 里装得到」
这条链是通的(ADR-083)。所以不要把它删掉,哪怕觉得里面的函数没什么用。
"""

__version__ = "0.1.0"


def human_bytes(n: int) -> str:
    """把字节数变成人能读的形式。写日志和做看板时反复要用。"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024.0
    return f"{n:.1f}TB"


def table_fqn(catalog: str, schema: str, table: str) -> str:
    """拼一个 Trino 的三段式表名。

    单独封一个函数是因为这个平台里"表名"有两种写法:Trino 查询用
    `catalog.schema.table`,而权限系统(table-access-grants.csv / OPA)
    用的是不带 catalog 的 `schema.table`。混用是个反复出现的错误来源。
    """
    return f"{catalog}.{schema}.{table}"


def grant_fqn(schema: str, table: str) -> str:
    """权限系统里用的两段式表名(不带 catalog),见 table_fqn 的说明。"""
    return f"{schema}.{table}"
