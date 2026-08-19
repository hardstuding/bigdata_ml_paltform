"""`job.yaml` 驱动的提交入口——对应脚手架模板里那份 10 行配置文件。

`submit_job()` 本身是 Python API,直接调用即可;这个文件只是给不想写
Python 调用代码的场景加一层:

    platform-submit job.yaml

job.yaml 的字段就是 submit_job() 的参数,没有另外发明一套 schema。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import yaml

from .submit import submit_job

# job.yaml 里允许出现的字段——和 submit_job() 的参数一一对应,不接受
# 未知字段就直接报错,不是静默忽略拼写错误(这类"配置项打错字但没人发现"
# 是这个项目已经踩过多次的坑,见 apps/definitions/airflow.yaml 里
# webserver/apiServer 那次教训)。
_ALLOWED_KEYS = {
    "name",
    "script",
    "image",
    "env",
    "cpu",
    "memory",
    "namespace",
    "service_account",
}


def _load_job_yaml(path: pathlib.Path) -> dict:
    with path.open(encoding="utf-8") as f:
        spec = yaml.safe_load(f)

    if not isinstance(spec, dict):
        raise ValueError(f"{path} 的内容不是一个 YAML 字典,检查一下格式")

    unknown = set(spec) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(
            f"{path} 里有不认识的字段: {sorted(unknown)}。"
            f"支持的字段是: {sorted(_ALLOWED_KEYS)}"
        )
    for required in ("name", "script"):
        if required not in spec:
            raise ValueError(f"{path} 缺少必填字段 {required!r}")

    # script 路径按 job.yaml 所在目录解析,不是按当前工作目录——这样
    # `platform-submit path/to/job.yaml` 不管在哪个目录下执行都行为一致。
    spec["script"] = path.parent / spec["script"]
    return spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="platform-submit", description="按 job.yaml 提交一个作业到 Argo Workflows"
    )
    parser.add_argument("job_yaml", type=pathlib.Path, help="job.yaml 文件路径")
    args = parser.parse_args(argv)

    spec = _load_job_yaml(args.job_yaml)
    workflow_name = submit_job(**spec)
    print(f"已提交: {workflow_name}")
    print(f"查状态: python3 -c \"from platform_sdk import job_status; "
          f"print(job_status('{workflow_name}'))\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
