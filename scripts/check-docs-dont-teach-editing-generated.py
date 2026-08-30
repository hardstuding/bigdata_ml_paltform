#!/usr/bin/env python3
"""扫出"教人去改生成物"的文档。

**这类错误的后果特别隐蔽**:照着做,改完当场是对的(文件里确实是你写的
内容),下一次 `render-environment-config.py` 一跑,改动被**静默覆盖** ——
没有冲突、没有报错、没有任何提示。如果中间还提交过一次,git 历史里甚至
能看到"改了又没了"。

这个仓库自己撞过 **4 次**(记在 CLAUDE.md 里),而 2026-08-30 又在
`docs/operations/tuning.md` 里发现一处**教别人这么做**的:
「按需要去对应组件的 Application yaml 直接改」。文档比代码更糟 —— 代码
只坑写它的人一次,文档坑每一个照着做的人。

判断依据:句子里同时出现"改/编辑/修改"和一个生成物目录,而**没有**出现
"不要/别/生成物/覆盖"这类否定词。误报可以加进 IGNORE,但要写原因。

用法:python3 scripts/check-docs-dont-teach-editing-generated.py
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 生成物目录/文件(和 scripts/render-environment-config.py 的 DIR_MAP 对齐)
GENERATED = [
    "apps/definitions/",
    "platform/apps/",
    "platform/bootstrap/",
    "apps/platform-jobs/manifests/",
    "apps/platform-streams/manifests/",
    "scripts/03-configure-keycloak.sh",
    "docs/reference/service-catalog.md",
]
EDIT_WORDS = ("改", "编辑", "修改", "调整")
# 出现这些词说明句子是在**警告**不要改,不是在教人改
NEGATIONS = ("不要", "别去", "不该", "生成物", "会被覆盖", "静默覆盖", "自动生成",
             "不是手写", "改要改", "去改 templates", "源:", "源头是",
             "不用改", "没有改", "故意没有", "本身", "⚠️")

# **只扫真正会被照着做的文档。**
#
# `docs/decisions/` 整个排除:ADR 记录的是"当时为什么这么定、当时改了什么",
# 是历史记录不是操作指引 —— 没有人会把一份 ADR 当 how-to 照着敲。把它们算
# 进来只会产生大量误报(「故意没有改 X」「X 一个字都不用改」这类句子),
# 而一个误报多的检查会被学会忽略,那它就白做了。
#
# `docs/journal/` 同理(排障叙事),CLAUDE.md 里那段本身就是在讲这个坑。
SKIP = ("docs/journal", "docs/decisions", "archive", "CLAUDE.md")

# 目前为空。加条目要写清楚为什么是误报 —— 没有理由的豁免清单会退化成
# "报错了就往里加一行"。
IGNORE = {
    # 这一行在讲一个**已经修好的历史故障**("已经改成一开始就注册 https,
    # 不会再重现"),不是让读者去改那个脚本。
    ("docs/operations/troubleshooting.md",
     "`scripts/03-configure-keycloak.sh` 已经改成一"):
        "描述已修复的历史故障,不是操作指引",
}


def main() -> int:
    problems = []
    for md in REPO.rglob("*.md"):
        rel = str(md.relative_to(REPO))
        if rel.startswith(".git") or any(sk in rel for sk in SKIP):
            continue
        for lineno, line in enumerate(md.read_text().splitlines(), 1):
            for gen in GENERATED:
                if gen not in line:
                    continue
                if not any(w in line for w in EDIT_WORDS):
                    continue
                if any(n in line for n in NEGATIONS):
                    continue
                if any(rel == k[0] and k[1] in line for k in IGNORE):
                    continue
                problems.append(
                    f"{rel}:{lineno} 像是在教人改生成物({gen}):\n"
                    f"      {line.strip()[:110]}")

    if problems:
        print(f"{len(problems)} 处文档可能在教人改生成物:", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        print("\n  改生成物的后果是**下一次渲染被静默覆盖**,没有任何提示。"
              "\n  应该指向对应的源(见 scripts/render-environment-config.py 的 DIR_MAP)。"
              "\n  确实是误报的话,加进这个脚本的 IGNORE 并写清楚原因。", file=sys.stderr)
        return 1
    print("没有文档在教人改生成物。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
