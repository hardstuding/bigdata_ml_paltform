#!/usr/bin/env python3
"""
扫描 platform/apps/、apps/definitions/、environments/cloud-full/
pending-definitions/ 下所有 ArgoCD Application,列出每个组件当前锁定的
chart/镜像版本和来源仓库——ADR-010 早就定了"版本要显式锁定并记录"这条
规则,这个脚本是把散在 30+ 个文件里的 targetRevision 汇总成一张表,不是
新规则,是把老规则落地成看得到的东西。

只读,不改任何文件,输出可以直接贴进 docs/operations/upgrade.md 的版本表
——是不是要贴、什么时候贴,人自己决定,这个脚本不自动写文件(和这个项目
"不用生成器焊配置"的原则一致,见 ADR-030)。

用法:
    python3 scripts/list-component-versions.py
"""
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DIRS = [
    ("platform/apps", "active"),
    ("apps/definitions", "active"),
    ("environments/cloud-full/pending-definitions", "pending"),
]


def main():
    rows = []
    for rel_dir, status in DIRS:
        d = REPO_ROOT / rel_dir
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yaml")):
            try:
                app = yaml.safe_load(f.read_text())
            except yaml.YAMLError:
                continue
            if not app or app.get("kind") != "Application":
                continue
            name = app.get("metadata", {}).get("name", f.stem)
            src = app.get("spec", {}).get("source", {})
            chart = src.get("chart")
            repo = src.get("repoURL", "")
            rev = src.get("targetRevision", "")
            kind = chart if chart else "raw manifest(这个仓库自己维护)"
            rows.append((name, kind, rev, repo, status))

    print("| 组件 | chart / 来源 | 版本 | 状态 |")
    print("|---|---|---|---|")
    for name, kind, rev, repo, status in sorted(rows):
        # oci:// 不是浏览器能直接打开的协议,不生成链接,原样显示文字
        repo_link = f"[{kind}]({repo})" if repo.startswith("http") else kind
        status_label = "启用" if status == "active" else "park(按需拉起)"
        print(f"| {name} | {repo_link} | {rev} | {status_label} |")


if __name__ == "__main__":
    main()
