#!/usr/bin/env python3
"""ADR 索引里的「状态」列,从每份 ADR 自己的 `状态:` 行生成。

**为什么**:2026-08-30 核对时,`docs/decisions/README.md` 里有 10 条写着
"未部署验证",而它们早就部署并实机验证过了(079 的探针 08-29 验过、081 的
告警送达 08-28 验过、068/069 分别在 08-25/08-26 验过……)。原因很简单:
**同一个状态被写在两个地方** —— ADR 自己开头一行、索引表里一列。改了一处
另一处不会跟着变,而且不会有任何地方报错。

这个仓库对付这类问题的既有办法就是"生成 + CI 防漂移"
(`check-service-catalog.py`、`sync-airflow-dags-configmap.py` 都是)。
这里是同一招:索引那一列**不再手写**,从 ADR 原文抽。

**注意这只解决"两处不一致",不解决"ADR 自己的状态过期"** —— 后者要人在
验证之后回去改那一行。但至少现在只有一个地方要改,而且改了之后索引会自动
跟上。哪份 ADR 的状态该改,以 `docs/project/capability-matrix.md` 为准
(那份表有验证级别和证据链接)。

用法:
  python3 scripts/sync-adr-index.py           # 重写索引里的状态列
  python3 scripts/sync-adr-index.py --check   # CI:检测漂移
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DECISIONS = REPO / "docs" / "decisions"
INDEX = DECISIONS / "README.md"

ROW_RE = re.compile(r"^\| \[(\d{3})\]\(([^)]+)\) \| ([^|]*?) \| ([^|]*?) \|\s*$")


def adr_status(filename: str) -> str | None:
    f = DECISIONS / filename
    if not f.exists():
        return None
    for line in f.read_text().splitlines()[:12]:
        m = re.match(r"^状态[::]\s*(.+?)\s*$", line)
        if m:
            return m.group(1)
    return None


def missing_from_index(index_text: str) -> list[str]:
    """有 ADR 文件但索引里没列。

    索引漏一份 ADR 的后果是**那个决策事实上不存在** —— 没人会去 ls 一遍
    84 个文件找它。2026-08-30 实测漏了 ADR-084。
    """
    listed = set(re.findall(r"^\| \[(\d{3})\]", index_text, re.M))
    have = {f.name[:3] for f in DECISIONS.glob("[0-9][0-9][0-9]-*.md")}
    return sorted(have - listed)


def main() -> int:
    check = "--check" in sys.argv
    lines = INDEX.read_text().splitlines()
    out, drift, missing = [], [], []
    for line in lines:
        m = ROW_RE.match(line)
        if not m:
            out.append(line)
            continue
        num, href, title, current = m.groups()
        want = adr_status(href)
        if want is None:
            # ADR 里没写状态行 —— 保留索引里手写的,但记下来
            missing.append(f"ADR-{num}({href})里没有 `状态:` 行,索引里那格只能手写")
            out.append(line)
            continue
        if current.strip() != want:
            drift.append(f"ADR-{num}:索引写「{current.strip()}」,原文写「{want}」")
        out.append(f"| [{num}]({href}) | {title.strip()} | {want} |")

    if missing:
        print(f"{len(missing)} 份 ADR 没有状态行(索引里保留手写):")
        for m_ in missing:
            print("  - " + m_)

    absent = missing_from_index("\n".join(lines))
    if absent:
        print(f"\n{len(absent)} 份 ADR 有文件但不在索引里:{absent}", file=sys.stderr)
        print("  索引漏一份的后果是那个决策事实上不存在 —— 没人会去 ls 一遍所有文件找它。",
              file=sys.stderr)
        print("  这个脚本**不会自动补**:补进去要写一句人能看懂的标题,那不该由脚本编。",
              file=sys.stderr)
        return 1

    if not drift:
        print(f"ADR 索引和原文状态一致(检查了 {sum(1 for l in lines if ROW_RE.match(l))} 条)。")
        return 0

    if check:
        print(f"\nADR 索引和原文的状态不一致({len(drift)} 处):", file=sys.stderr)
        for d in drift:
            print("  - " + d, file=sys.stderr)
        print("\n跑 `python3 scripts/sync-adr-index.py` 用原文覆盖索引。"
              "\n**如果是原文过期了,先改原文那一行,再跑这个** —— "
              "哪份 ADR 的真实状态,以 docs/project/capability-matrix.md 为准。",
              file=sys.stderr)
        return 1

    INDEX.write_text("\n".join(out) + "\n")
    print(f"已用 ADR 原文覆盖索引里的状态列({len(drift)} 处):")
    for d in drift:
        print("  - " + d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
