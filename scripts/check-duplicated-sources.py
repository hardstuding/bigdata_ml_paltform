#!/usr/bin/env python3
"""校验"同一份源码被复制到多个应用里"的那几份副本没有漂移。

和 `check-embedded-scripts.py` 是**不同的场景**,不要合并:那个管的是
"scripts/ 下的脚本 ↔ 内嵌进 ConfigMap 的副本",这个管的是"权威源文件 ↔
被复制进各个应用 src/ 的副本"。两者的失败模式一样(靠人记得同步一定会漏,
而漏了之后不会有任何地方报错),但对象不同。

**为什么是复制而不是做成一个包**:三个自建 Flask 应用是三个独立镜像、各自
装自己的依赖。为了几十行代码引入一个内部包 + 发布流程,复杂度远大于收益。
ADR-083 那套内部包机制是给**用户的**包用的,不是给平台自己这几个小应用用的。
复制的代价(会漂移)由这个检查器兜住。

用法:
    python3 scripts/check-duplicated-sources.py         # 检查,漂移则非零退出
    python3 scripts/check-duplicated-sources.py --fix   # 用权威源覆盖所有副本
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# (权威源, [副本...])
GROUPS = [
    ("shared/flask_identity.py", [
        "apps/platform-portal/src/identity.py",
        "apps/permission-request-app/src/identity.py",
        "apps/table-registration-app/src/identity.py",
    ]),
]


def main() -> int:
    fix = "--fix" in sys.argv
    problems = []
    checked = 0
    for src_rel, copies in GROUPS:
        src = REPO / src_rel
        if not src.exists():
            problems.append(f"权威源不存在:{src_rel}")
            continue
        want = src.read_text()
        for copy_rel in copies:
            checked += 1
            copy = REPO / copy_rel
            if copy.exists() and copy.read_text() == want:
                continue
            if fix:
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_text(want)
                print(f"已同步:{copy_rel} <- {src_rel}")
            else:
                what = "不存在" if not copy.exists() else "和权威源不一致"
                problems.append(f"{copy_rel} {what}(权威源:{src_rel})")

    if problems:
        print(f"{len(problems)} 处副本漂移:", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        print("\n跑 `python3 scripts/check-duplicated-sources.py --fix` 用权威源覆盖。",
              file=sys.stderr)
        return 1
    print(f"检查了 {checked} 份副本,和各自的权威源一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
