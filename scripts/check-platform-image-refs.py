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
import subprocess
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


# ---------------------------------------------------------------------------
# 第二条检查:tag 指向的 commit 必须真的构建过这个镜像
# ---------------------------------------------------------------------------
#
# 2026-09-02 踩到:`platform_job_image` 的 tag 被更新成了 996dab80,而那个
# commit **只改了 platform-portal 的源码,没碰 apps/platform-image/**。
# CI 的 build-images.yml 是按 `paths:` 触发的 —— portal 的镜像建了,
# platform-runtime 的**没建**。于是配置指向一个从未存在过的镜像 tag。
#
# 后果:JupyterHub 的 singleuser 镜像和 submit_job() 用的作业镜像都指向它,
# 一旦 ArgoCD 把这份配置同步下去,**用户起不了 notebook、提交的作业也拉不到
# 镜像**。发现时活的集群上还是旧 tag(同步没跟上),纯属侥幸。
#
# 判据:tag 必须等于"最后一次改动 apps/platform-image/ 的那个 commit"。
# 手动 workflow_dispatch 构建的例外见报错里的说明。

RUNTIME_BUILD_CONTEXT = "apps/platform-image"


def check_tag_was_built(root: Path) -> list[str]:
    problems: list[str] = []
    try:
        expected = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", RUNTIME_BUILD_CONTEXT],
            cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return []          # 不在 git 工作区里(比如容器内),这条检查跳过
    if not expected:
        return []
    for env_dir in sorted((root / "environments").iterdir()):
        cfg = env_dir / "config.yaml"
        if not cfg.exists():
            continue
        lines = cfg.read_text(encoding="utf-8").split("\n")
        for idx, line in enumerate(lines):
            if not line.startswith("platform_job_image:"):
                continue
            # 显式豁免:上一行写 `# image-tag-exempt: <理由>` 就跳过这一条。
            # 存在的意义是**区分"忘了改"和"知道,而且是有意的"** —— 后者最
            # 常见的情况是某次 CI 构建失败了,于是配置有意停在上一个真的
            # 存在的 tag 上。没有豁免口的检查会被人整条注释掉,那更糟。
            # 往上扫连续的注释块,不是只看紧挨着的那一行 —— 豁免理由通常
            # 要写好几行才说得清楚,标记不一定正好落在最后一行。
            exempt = False
            j = idx - 1
            while j >= 0 and lines[j].strip().startswith("#"):
                if lines[j].strip().startswith("# image-tag-exempt:"):
                    exempt = True
                    break
                j -= 1
            if exempt:
                continue
            tag = line.split(":")[-1].strip()
            if len(tag) == 40 and tag != expected:
                problems.append(
                    f"  - {cfg.relative_to(root)} 的 platform_job_image 指向 {tag[:12]},\n"
                    f"    但最后一次改动 {RUNTIME_BUILD_CONTEXT}/ 的是 {expected[:12]}。\n"
                    f"    CI 只在 {RUNTIME_BUILD_CONTEXT}/ 有变更时才构建这个镜像,\n"
                    f"    所以 {tag[:12]} 这个 tag **很可能根本不存在**。\n"
                    f"    改成 {expected} 即可;\n"
                    f"    另外两种可能,处理方式不一样:\n"
                    f"      - 那次 CI 构建**失败了**,配置有意停在上一个真的存在的 tag:\n"
                    f"        在这一行上面加一行 `# image-tag-exempt: <理由>`\n"
                    f"      - 镜像是手动 workflow_dispatch 建的别的 commit:同上,写清理由\n"
                    f"    先确认 ACR 上那个 tag 到底在不在,再决定用哪种 —— 指向一个拉不到\n"
                    f"    的镜像,后果是用户起不了 notebook、submit_job() 也拉不到镜像。")
    return problems


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

    built_problems = check_tag_was_built(REPO)

    if problems:
        print("统一运行时镜像的引用和环境配置对不上:\n", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("\n  改法:改 environments/<env>/config.yaml 的 platform_job_image,"
              "\n  然后把上面这几处跟着改成同一个值(它们渲染不了,只能手写 + 靠这个检查)。",
              file=sys.stderr)
        return 1

    if built_problems:
        print("有环境的 platform_job_image 指向一个很可能没被构建过的 tag:\n",
              file=sys.stderr)
        for b in built_problems:
            print(b, file=sys.stderr)
        return 1

    print(f"检查了 {len(REFS)} 处硬编码引用,都和各自环境的 platform_job_image 一致;"
          f"\ntag 也对得上最后一次改动 {RUNTIME_BUILD_CONTEXT}/ 的那个 commit。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
