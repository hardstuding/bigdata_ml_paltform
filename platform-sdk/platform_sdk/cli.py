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

# 模板目录。装成 wheel 之后 examples/ 不在包里,所以按"从仓库根找"和
# "从包旁边找"两种情况都试一遍——在 notebook 里(镜像里带着仓库)和在本机
# 仓库里都能用。找不到就明确报错,不静默返回空列表让人以为没有模板。
_TEMPLATE_DIRS = [
    pathlib.Path(__file__).resolve().parent.parent.parent / "examples",
    pathlib.Path.cwd() / "examples",
]


def _templates_root() -> pathlib.Path | None:
    for d in _TEMPLATE_DIRS:
        if d.is_dir() and any(d.glob("*/job.yaml")):
            return d
    return None


def list_templates() -> list[tuple[str, str]]:
    """返回 [(模板名, 一句话说明)]。说明取 job.py 文档字符串的第一行。"""
    root = _templates_root()
    if root is None:
        return []
    out = []
    for d in sorted(root.iterdir()):
        job_py = d / "job.py"
        if not (d / "job.yaml").is_file() or not job_py.is_file():
            continue
        first = ""
        for line in job_py.read_text().splitlines():
            line = line.strip().strip('"')
            if line:
                first = line
                break
        out.append((d.name, first))
    return out


def scaffold(template: str, target: pathlib.Path) -> int:
    """把模板整个复制到 target 目录。**不覆盖已存在的目录**——脚手架把人
    写了一半的代码盖掉是不可接受的。"""
    import shutil

    root = _templates_root()
    if root is None:
        print("!! 找不到 examples/ 目录。在仓库根目录下跑,或者直接复制"
              "examples/<模板名>/ 到你想要的位置。", file=sys.stderr)
        return 1
    src = root / template
    if not (src / "job.yaml").is_file():
        names = [n for n, _ in list_templates()]
        print(f"!! 没有名为 {template} 的模板。可选:{', '.join(names)}", file=sys.stderr)
        return 1
    if target.exists():
        print(f"!! {target} 已经存在,不覆盖。换个目录名,或者先把它挪走。", file=sys.stderr)
        return 1
    shutil.copytree(src, target)
    print(f"已生成 {target}/(来自模板 {template})")
    print(f"改完之后提交:platform-submit {target}/job.yaml")
    return 0

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
    parser.add_argument("job_yaml", type=pathlib.Path, nargs="?",
                        help="job.yaml 文件路径")
    parser.add_argument("--list-templates", action="store_true",
                        help="列出可用的作业模板")
    parser.add_argument("--new", metavar="模板名",
                        help="从模板生成一个新作业目录(配合 --into 指定目录名)")
    parser.add_argument("--into", type=pathlib.Path, metavar="目录",
                        help="--new 生成到哪个目录,默认用模板名")
    args = parser.parse_args(argv)

    if args.list_templates:
        rows = list_templates()
        if not rows:
            print("!! 找不到模板(examples/ 目录不在这里)。", file=sys.stderr)
            return 1
        print("可用的作业模板:")
        for name, desc in rows:
            print(f"  {name:22s} {desc}")
        print("\n用法:platform-submit --new <模板名> [--into <目录>]")
        return 0

    if args.new:
        target = args.into or pathlib.Path(args.new)
        return scaffold(args.new, target)

    if args.job_yaml is None:
        parser.error("要么给一个 job.yaml,要么用 --list-templates / --new")

    spec = _load_job_yaml(args.job_yaml)
    workflow_name = submit_job(**spec)
    print(f"已提交: {workflow_name}")
    print(f"查状态: python3 -c \"from platform_sdk import job_status; "
          f"print(job_status('{workflow_name}'))\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
