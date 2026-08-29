#!/usr/bin/env python3
"""检查 YAML 里内嵌的 Python 代码块语法正确。

**为什么需要**:这个平台有好几处把 Python 当配置塞进 YAML —— Superset 的
`configOverrides`(整个 superset_config.py)、JupyterHub 的 `extraConfig`
(hub 的启动钩子)。它们**只在组件启动时才被执行**,写错一个括号的表现是
pod CrashLoopBackOff,而 `git push` / ArgoCD Synced 全都是绿的。

2026-08-29 给 Superset 加按组分配角色时,往 configOverrides 里塞了 40 多行
Python(自定义 SecurityManager)。这种改动靠人眼看括号不现实。

只做语法检查(`ast.parse`),不做类型/运行检查 —— 那需要真的装 superset。
语法错误是这类改动最常见也最致命的一类,先把它挡住。

跑法:python3 scripts/check-embedded-python-syntax.py
"""
import ast
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
# 已知会内嵌 Python 的位置:(文件 glob, 到那个 dict 的路径)
# **只检查渲染产物(apps/definitions/),不检查源(apps/components/)。**
# 源文件里带 {{DOMAIN_SUFFIX}} 这类占位符,YAML 和 Python 都解析不了;而
# "源改了没重新渲染"这件事由 render-environment-config.py --check 兜着,
# 所以查产物就等于查了源。
TARGETS = [
    ("apps/definitions/superset.yaml", ["spec", "source", "helm", "valuesObject", "configOverrides"]),
    ("apps/definitions/jupyterhub.yaml", ["spec", "source", "helm", "valuesObject", "hub", "extraConfig"]),
]


def dig(d, path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d if isinstance(d, dict) else None


def main() -> None:
    problems, checked = [], 0
    for rel, path in TARGETS:
        f = REPO / rel
        if not f.exists():
            continue
        text = f.read_text()
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            problems.append(f"{rel}: YAML 都解析不了 —— {str(exc)[:120]}")
            continue
        blocks = dig(doc, path)
        if not blocks:
            problems.append(f"{rel}: 找不到 {'.'.join(path)},这个检查对它失效了"
                            f"(结构改过?那要同步改这个脚本,别让它静默跳过)")
            continue
        for name, code in blocks.items():
            if not isinstance(code, str):
                continue
            checked += 1
            try:
                ast.parse(code)
            except SyntaxError as exc:
                problems.append(f"{rel} 的 {name}:第 {exc.lineno} 行 {exc.msg}")

    print(f"检查了 {checked} 段内嵌 Python。")
    if problems:
        print("\n有问题:")
        for p in problems:
            print("  -", p)
        print("\n这类错误只在组件启动时才暴露(CrashLoopBackOff),"
              "\n而 git push 和 ArgoCD 都会是绿的。")
        sys.exit(1)
    print("内嵌 Python 语法都正确。")


if __name__ == "__main__":
    main()
