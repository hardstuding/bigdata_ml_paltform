"""platform_sdk —— 这个数据+AI平台的薄客户端。

设计边界见 docs/decisions/058-lightweight-developer-experience.md。
**这个包只做两件事:连接封装、作业提交。** 任何"顺手加个功能"的想法
默认拒绝,先记进 docs/BACKLOG.md 单独评估——这条边界一旦破了,它就会
长成一个小型平台,ADR-058 的全部价值就没了。这不是客套话,是这个方案
唯一可能失败的方式。

常用入口:

    from platform_sdk import query, mlflow_setup, submit_job

    df = query("select * from iceberg.demo.orders limit 10")

    mlflow = mlflow_setup("my-experiment")

    wf = submit_job("my-training", "train.py")
"""

from .config import MissingCredential
from .connect import mlflow_setup, query, s3_client, trino_connection

__all__ = [
    "MissingCredential",
    "mlflow_setup",
    "query",
    "s3_client",
    "trino_connection",
    "submit_job",
    "job_status",
    "job_logs",
]

__version__ = "0.1.0"


def __getattr__(name):
    """submit 相关的三个函数延迟导入。

    它们需要 kubernetes 客户端(pyproject 里是 optional 依赖),而只想在
    notebook 里查数的人不该因为没装这个可选依赖就 `import platform_sdk`
    失败。用模块级 __getattr__ 做延迟导入,既保持了顶层的简洁用法,
    又不把重依赖变成必装。
    """
    if name in ("submit_job", "job_status", "job_logs"):
        from . import submit

        return getattr(submit, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
