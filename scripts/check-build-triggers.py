#!/usr/bin/env python3
"""CI 检查:build-images.yml 里每个要构建的镜像,都得有对应的 paths 触发。

**为什么需要**(2026-08-28 实测撞到):往 matrix 里加了
`apps/hive-metastore-image` 这个新镜像,却忘了同时往 `on.push.paths` 里加
对应路径。后果不是"报错",是**改了那个 Dockerfile 之后 CI 根本不会跑**
——工作流静静地不触发,而你在等一个永远不会出现的新镜像。

这一类"漏配一处导致整条路径静默失效"是这个仓库反复出现的形态(OPA 没配
--watch、Trino 没配 group provider、kserve-demo 不在 NetworkPolicy 白名单)。
能用一条检查拦住的就拦住。

用法:python3 scripts/check-build-triggers.py
"""
import sys
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "build-images.yml"


def main() -> int:
    wf = yaml.safe_load(WF.read_text())
    # PyYAML 会把 YAML 的 `on:` 解析成布尔 True(YAML 1.1 的老规矩),
    # 两种写法都兜一下,不然这个检查器自己会在某次 PyYAML 升级后静默失效。
    on = wf.get("on") or wf.get(True)
    paths = (on.get("push") or {}).get("paths") or []
    contexts = [i["context"] for i in
                wf["jobs"]["build-and-push"]["strategy"]["matrix"]["include"]]

    missing = [c for c in contexts if not any(p.startswith(c) for p in paths)]
    if missing:
        print(f"!! {len(missing)} 个镜像在 matrix 里,但没有对应的 paths 触发:",
              file=sys.stderr)
        for c in missing:
            print(f"   {c} —— 改了它的 Dockerfile,CI **不会**重新构建", file=sys.stderr)
        print("\n   在 .github/workflows/build-images.yml 的 on.push.paths 里补上。",
              file=sys.stderr)
        return 1
    print(f"{len(contexts)} 个镜像,每个都有对应的 paths 触发。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
