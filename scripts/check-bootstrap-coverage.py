#!/usr/bin/env python3
"""校验"一键拉起"这条路径和文档说的一致。

**这个仓库的核心要求之一是"从空环境可恢复、可重复的一键部署路径"**
(见 CLAUDE.md)。它的失效方式很隐蔽:**新加了一个部署必需的步骤,却只写
进了某份文档、没有加进 `bootstrap-all.sh`**。跑一键脚本的人不会知道少了
这一步,直到某个组件因为缺一个 Secret 起不来 —— 而报错指向的是那个组件,
不是缺失的步骤。

2026-08-30 实测就有一个:`45-configure-acr-pull.sh`(给各命名空间配私有
镜像仓库的拉取凭据)在 cloud-full 上是**硬前置** —— 没有它自建镜像一个
都拉不下来 —— 却既不在 `bootstrap-all.sh` 里,也不在 `scripts/README.md`
的部署主线表里。

检查两个方向:
  A. `scripts/README.md` 的「从空集群拉起(部署主线)」里列的每个脚本,
     `bootstrap-all.sh` 都要真的调用。
  B. `bootstrap-all.sh` 调用的每个编号脚本,都要在那张表里列出来。

两边都查是有意的:只查 A 的话,一键脚本里多做了一件没人知道的事;只查 B
的话,文档写了一步而一键脚本没做。

用法:python3 scripts/check-bootstrap-coverage.py
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = REPO / "scripts" / "README.md"
BOOTSTRAP = REPO / "scripts" / "bootstrap-all.sh"
SECTION = "## 1. 从空集群拉起(部署主线)"

# 部署主线表里列了、但**有意**不进 bootstrap-all.sh 的。每条都要写原因 ——
# 这个清单没有理由就会退化成"报错了就往里加一行"。
ALLOWED_NOT_IN_BOOTSTRAP = {
    "07-fix-trino-liveness-probe.sh":
        "现在是可选的快捷方式:apps/trino-liveness-fix/ 那个 CronJob 每 5 分钟"
        "自动巡检修复。bootstrap 里其实也调了它(为了不等那 5 分钟),这条留着"
        "是防止哪天 CronJob 下线了这里被误删。",
}


def scripts_in_readme() -> list[str]:
    """部署主线那一节的表格里出现的脚本名。

    **不要限定在第一列** —— 第一版的正则要求脚本名出现在行首那一格,而那张表第一列
    是序号、脚本在第二列,于是它一个都匹配不到、然后报告"25 处不一致"。
    检查器自己看错了地方,和它要防的问题是一类。
    """
    text = README.read_text()
    start = text.index(SECTION)
    end = text.index("\n## ", start + len(SECTION))
    out = []
    for line in text[start:end].splitlines():
        if not line.startswith("|"):
            continue
        out += re.findall(r"`([0-9][0-9]-[a-z0-9-]+\.(?:sh|py))`", line)
    return out


def scripts_in_bootstrap() -> set[str]:
    """**只算真正的调用**,不算日志/注释里提到的名字。

    第一版是全文 grep 脚本名,于是 `21-bootstrap-cloud-vm.sh` 这种只在
    提示信息里出现的也被当成"一键脚本会执行它",要求文档把它列进部署主线
    —— 而它根本不是这条路径上的一步。
    """
    text = BOOTSTRAP.read_text()
    return set(re.findall(
        r'run_(?:required|optional) "(?:scripts/)?([0-9][0-9]-[a-z0-9-]+\.(?:sh|py))',
        text))


def main() -> int:
    listed = scripts_in_readme()
    called = scripts_in_bootstrap()
    problems = []

    for s in listed:
        if s in called or s in ALLOWED_NOT_IN_BOOTSTRAP:
            continue
        problems.append(
            f"{s} 列在 scripts/README.md 的部署主线里,但 bootstrap-all.sh **没有调用它** —— "
            f"跑一键脚本的人会漏掉这一步,而报错会指向某个起不来的组件,不是这一步")

    for s in sorted(called):
        if s not in listed:
            problems.append(
                f"bootstrap-all.sh 调用了 {s},但它不在 scripts/README.md 的部署主线表里 —— "
                f"想手动逐步执行、或者想知道一键脚本到底做了什么的人,在文档里找不到它")

    if problems:
        print(f"一键拉起路径和文档不一致({len(problems)} 处):", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1
    print(f"一键拉起路径和文档一致:部署主线 {len(listed)} 个脚本,"
          f"bootstrap-all.sh 调用 {len(called)} 个。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
