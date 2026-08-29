#!/usr/bin/env python3
"""检查文档里的相对链接有没有指向不存在的文件。

**为什么需要**:这个仓库的文档之间交叉引用很密(ADR 互相引、README 指
QUICKSTART、CLAUDE.md 指 CURRENT_WORK、脚本注释指 ADR)。任何一次目录调整
都可能留下一堆死链,而死链不会让任何东西报错——只会让下一个人点过去发现
404,然后不再信任这些链接。

只检查**仓库内的相对链接**;外部 URL 不查(那需要联网,而且外部站点挂了
不该让 CI 红)。

跑法:python3 scripts/check-doc-links.py
"""
import re
import sys
from pathlib import Path
from urllib.parse import unquote

REPO = Path(__file__).resolve().parent.parent
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SKIP_DIRS = {".git", "node_modules", "image-cache", "image-cache-amd64", "logs",
             # vendored 的上游 chart,它们的文档指向的是上游仓库的结构,
             # 不是我们的(见 ADR-061 为什么要 vendor 进来)
             "loki-chart", "alloy-chart"}
# GitHub 网页上能解析、但磁盘上不存在的相对链接(安全公告、issue 模板等)
GITHUB_ONLY = ("/security/advisories/", "/issues/new")


def main() -> None:
    md_files = [p for p in REPO.rglob("*.md")
                if not any(s in p.parts for s in SKIP_DIRS)]
    broken = []
    checked = 0
    for md in md_files:
        for m in LINK_RE.finditer(md.read_text(errors="ignore")):
            target = m.group(1).strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if any(g in target for g in GITHUB_ONLY):
                continue
            checked += 1
            path = unquote(target.split("#", 1)[0])
            if not path:
                continue
            resolved = (md.parent / path).resolve()
            if not resolved.exists():
                broken.append(f"{md.relative_to(REPO)} -> {target}")

    print(f"扫了 {len(md_files)} 个 md,{checked} 条仓库内链接。")
    if broken:
        print(f"\n{len(broken)} 条死链:")
        for b in broken:
            print("  -", b)
        sys.exit(1)
    print("没有死链。")


if __name__ == "__main__":
    main()
