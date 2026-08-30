#!/usr/bin/env python3
"""校验文档里出现的 `./scripts/xxx` 命令,指向的脚本真的存在、且可执行。

**为什么**:一份"照着做就能拉起来"的文档,最基础的失效方式是里面写的命令
根本不存在 —— 脚本改过名、删了、或者当初就写错了一个字。这类错误读的时候
完全看不出来,只有真去敲那一行才会发现,而那时人已经在部署途中了。

这个仓库的脚本改过名(编号复用、`xx-verify-*` 拆出去),而文档有 140 多份
—— 靠人记得同步是不可能的。

顺带检查可执行位:`./scripts/x.sh` 这种写法要求文件有 +x,没有的话报的是
`Permission denied`,和"脚本不存在"是两种不同的困惑。

用法:python3 scripts/check-doc-commands.py
"""
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# 只扫真正给人照着敲的文档;journal/ 和 archive 是历史记录,里面提到的
# 命令当时是对的,不该因为脚本后来改名就把历史记录改掉。
SKIP_DIRS = ("docs/journal", "archive")

CMD_RE = re.compile(r"(?:\./)?scripts/([A-Za-z0-9._-]+\.(?:sh|py))")

# 占位符和"还没写的脚本"。**每条都要写清楚为什么**,不然这个豁免清单会
# 变成"报错了就往里加一行",那这个检查就没用了。
IGNORE = {
    "check-xxx.py": "CLAUDE.md 里举例用的占位名,不是真命令",
    "sync-app-configmaps.py": "roadmap 里提议的脚本,还没写 —— roadmap 描述"
                              "未来要做的东西是正常的",
    "task-runner.sh": "同上,外部评审里提议的,没采纳",
}


def main() -> int:
    problems = []
    scanned = 0
    for md in REPO.rglob("*.md"):
        rel = str(md.relative_to(REPO))
        if rel.startswith(".git") or any(d in rel for d in SKIP_DIRS):
            continue
        scanned += 1
        for lineno, line in enumerate(md.read_text().splitlines(), 1):
            for name in set(CMD_RE.findall(line)):
                if name in IGNORE:
                    continue
                target = REPO / "scripts" / name
                if not target.exists():
                    problems.append(f"{rel}:{lineno} 提到 scripts/{name},但这个文件不存在")
                elif line.strip().startswith(("./scripts/", "$ ./scripts/")) \
                        and not os.access(target, os.X_OK):
                    problems.append(
                        f"{rel}:{lineno} 让人跑 ./scripts/{name},但它没有可执行位 —— "
                        f"照着敲会得到 Permission denied")

    if problems:
        print(f"文档里有 {len(problems)} 条指向不存在/不可执行的脚本:", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1
    print(f"扫了 {scanned} 份文档,提到的 scripts/ 命令全部存在且可执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
