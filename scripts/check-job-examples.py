#!/usr/bin/env python3
"""CI 检查:`examples/` 下的作业模板必须是能用的。

**为什么需要**(A 线,2026-08-28):模板的价值在于"照着改就能跑"。一份语法
错的、或者 `job.yaml` 里写了 SDK 不认的字段的模板,比没有模板更糟——人会先
花时间怀疑是自己改坏了。

而模板恰恰是最容易腐烂的东西:它不在任何部署路径上,没人会因为它坏了而收到
告警,可能几个月都没人跑一次。

查三条:
  1. 每个模板目录都有 `job.py` + `job.yaml`;
  2. `job.py` 语法正确(用 ast 解析,不执行——执行需要集群);
  3. `job.yaml` 里的字段都是 `submit_job()` 真的接受的参数,而且必填的都在。
     **第 3 条是这个检查器真正的价值**:字段名写错(比如 `cpus` 写成 `cpu`
     的反面)在提交那一刻才会报 TypeError,而那时人已经在集群上了。

用法:python3 scripts/check-job-examples.py
"""
import ast
import inspect
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"


def main() -> int:
    sys.path.insert(0, str(REPO / "platform-sdk"))
    try:
        from platform_sdk.submit import submit_job
    except ImportError as e:
        print(f"!! 导入 platform_sdk 失败:{e}", file=sys.stderr)
        return 1

    sig = inspect.signature(submit_job)
    accepted = set(sig.parameters)
    required = {n for n, p in sig.parameters.items() if p.default is inspect.Parameter.empty}

    problems = []
    dirs = sorted(d for d in EXAMPLES.iterdir() if d.is_dir())
    if not dirs:
        print("!! examples/ 下一个模板都没有", file=sys.stderr)
        return 1

    for d in dirs:
        job_py, job_yaml = d / "job.py", d / "job.yaml"
        if not job_py.is_file() or not job_yaml.is_file():
            problems.append(f"{d.name}: 缺 job.py 或 job.yaml")
            continue
        try:
            ast.parse(job_py.read_text())
        except SyntaxError as e:
            problems.append(f"{d.name}/job.py: 语法错误第 {e.lineno} 行 —— {e.msg}")
        try:
            spec = yaml.safe_load(job_yaml.read_text()) or {}
        except yaml.YAMLError as e:
            problems.append(f"{d.name}/job.yaml: YAML 解析失败 —— {str(e)[:80]}")
            continue
        unknown = set(spec) - accepted
        if unknown:
            problems.append(
                f"{d.name}/job.yaml: submit_job() 不认识这些字段 {sorted(unknown)}"
                f"(它接受的是 {sorted(accepted)})")
        missing = required - set(spec)
        if missing:
            problems.append(f"{d.name}/job.yaml: 缺必填字段 {sorted(missing)}")

    if problems:
        print(f"!! {len(problems)} 个模板有问题:", file=sys.stderr)
        for p in problems:
            print("   " + p, file=sys.stderr)
        return 1
    print(f"{len(dirs)} 个作业模板都能用(结构、语法、job.yaml 字段都对得上 submit_job())。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
