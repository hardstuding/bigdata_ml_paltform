#!/usr/bin/env python3
"""统一运行时镜像:所有硬编码的引用,必须和环境配置一致。

**为什么需要这个检查。** notebook(JupyterHub singleuser)、定时作业
(Argo CronWorkflow)、`platform-submit` 提交的作业,用的必须是**同一个
镜像** —— "交互开发和调度执行环境一致"(ADR-058)这条能力就靠它成立。
而"同一个"这件事,2026-08-30 之前是靠**六个地方各自写对同一个字符串**
来保证的:没有任何机制拦住其中一个被改掉。

这正是 2026-08-16 那次 SSO 连环故障的同一个形态(一个值散落硬编码在 9 个
文件里,改一处不代表其他跟着变),那次的解法是 `environments/*/config.yaml`
+ 渲染。这里能渲染的都渲染了(JupyterHub 组件、CronWorkflow),但有几处
天生渲染不了:

- `platform_sdk/config.py` 的兜底默认值 —— 一个 Python 包,不走渲染
- Airflow 的 DAG —— 静态文件,只被同步进 ConfigMap
- 文档和示例里的注释 —— 给人看的,写错了不会报错,只会误导

对这几处,仓库的既定判据是"删不掉也生成不了,才手写 + 加检查"
(见 CLAUDE.md「状态别写两遍」那节)。这个脚本就是那道检查。
"""
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

# (文件, 该文件应该和哪个环境的配置一致, 说明)
#
# 为什么不是全部对 cloud-full:`platform_sdk/config.py` 的兜底值是给
# **本机直接 import 试用**准备的(集群里轮不到它 —— singleuser pod 和
# Argo 作业都会带 PLATFORM_JOB_IMAGE 环境变量),所以它该对 local-lite。
REFS = [
    ("platform-sdk/platform_sdk/config.py", "local-lite",
     "SDK 的兜底默认值(本机 import 时才用得到)"),
    ("apps/airflow/dags/platform_sdk_demo.py", "cloud-full",
     "Airflow DAG 里的 PLATFORM_IMAGE"),
    # 文档和示例**刻意不在这张表里** —— 它们原来也写死了这个值,
    # 2026-08-30 一起改掉了:改成"值在 environments/<env>/config.yaml"。
    # 判据见 CLAUDE.md「状态别写两遍」:能删掉重复的就删掉,删不掉才加检查。
    # 而且文档里名指一个值本身就是错的 —— 镜像按环境不同,写死哪一档都会
    # 误导另一档的读者。
]

# 这些地方**允许**出现别的写法,不检查:
#   - 环境配置自己
#   - 这个脚本自己
#   - 生成物(它们由渲染保证)
#   - journal / roadmap 这类记录历史的文档,里面本来就要引用旧值
SKIP_DIRS = ("logs/", "docs/journal/", ".git/")


def image_of(env: str) -> str:
    cfg = yaml.safe_load(
        (REPO / "environments" / env / "config.yaml").read_text(encoding="utf-8"))
    img = (cfg or {}).get("platform_job_image")
    if not img:
        print(f"!! environments/{env}/config.yaml 里没有 platform_job_image", file=sys.stderr)
        sys.exit(1)
    return img


# 长得像统一运行时镜像的字符串
IMAGE_RE = re.compile(r"[A-Za-z0-9._/-]*platform-runtime:[A-Za-z0-9._-]+")


def main() -> int:
    problems = []
    for rel, env, what in REFS:
        path = REPO / rel
        if not path.exists():
            problems.append(f"{rel}:文件不存在 —— REFS 这张表过期了,去 "
                            "scripts/check-platform-image-refs.py 里更新")
            continue
        expected = image_of(env)
        found = set(IMAGE_RE.findall(path.read_text(encoding="utf-8")))
        if not found:
            problems.append(f"{rel}:找不到任何 platform-runtime 引用({what})"
                            " —— 要么被删了,要么写法变了,这张表该更新")
            continue
        wrong = sorted(f for f in found if f != expected)
        if wrong:
            problems.append(
                f"{rel}({what})\n"
                f"    应该是({env} 那档):{expected}\n"
                f"    实际写着:      {', '.join(wrong)}")

    if problems:
        print("统一运行时镜像的引用和环境配置对不上:\n", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("\n  改法:改 environments/<env>/config.yaml 的 platform_job_image,"
              "\n  然后把上面这几处跟着改成同一个值(它们渲染不了,只能手写 + 靠这个检查)。",
              file=sys.stderr)
        return 1

    print(f"检查了 {len(REFS)} 处硬编码引用,都和各自环境的 platform_job_image 一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
