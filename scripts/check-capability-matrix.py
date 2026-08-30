#!/usr/bin/env python3
"""校验 docs/project/capability-matrix.md 的自洽性。

**为什么需要这个检查**:这张表是"我们做到哪了"的唯一权威入口,而它历史上
被证伪过四次(表底部那节列了)。四次的共同点都是**状态和证据不匹配**——
标着 ✅ 但没人真的跑过。人工维护挡不住这个:写表的时候总觉得"应该是好的"。

所以这里只查一件机器查得动、而且正好卡在那个失效模式上的事:

    状态是 ✅ 的行,验证级别不能是「未验证」或「计划中」。

外加几条结构性的:列数对、验证级别只能用约定的那几个词、"生产验证"在
production-readiness-gaps.md 那些门禁项补齐之前一格都不许出现。
"""
import pathlib
import re
import sys

DOC = pathlib.Path(__file__).resolve().parent.parent / "docs/project/capability-matrix.md"

LEVELS = {"生产验证", "集成验证", "demo", "未验证", "计划中", "—"}
STATUSES = {"✅", "🟡", "❌"}


GAPS_DOC = pathlib.Path(__file__).resolve().parent.parent / "docs/project/production-readiness-gaps.md"
STATUS_WORDS = ("未部署", "未上集群", "未验证", "配置完成", "代码完成", "manifest 完成")


def check_gaps_doc_has_no_status() -> list[str]:
    """`production-readiness-gaps.md` 的条目标题里不许再挂状态。

    **为什么单独查这个**:那份文档 2026-08-30 之前每条标题上都挂着 ✅/🟡
    和"未部署""未上集群",结果它比现实**落后了四五天** —— 三条写着未部署
    的东西早就部署并验证过了。一份"生产就绪度"文档**低估**已完成的部分,
    后果和高估一样坏:会让人重复做已经做完的事,或者在评估"能不能上生产"
    时给出错误结论。

    职责划分是:状态只在 capability-matrix.md 里有一份;那份文档记的是
    "为什么这条是缺口""做到什么程度才算做完",那部分不会过期。这个检查
    就是把这条划分钉住 —— 不然下一次有人顺手在标题上写个 ✅,就又开始了。
    """
    if not GAPS_DOC.exists():
        return []
    problems = []
    for lineno, line in enumerate(GAPS_DOC.read_text().splitlines(), 1):
        if not re.match(r"^## \d+\. ", line):
            continue
        hit = [w for w in ("✅", "🟡", "❌", *STATUS_WORDS) if w in line]
        if hit:
            problems.append(
                f"production-readiness-gaps.md:{lineno} 条目标题里带了状态 {hit}:"
                f"「{line.strip()}」。状态只在 capability-matrix.md 里维护 —— "
                f"两份文档各记一份,迟早会打架(2026-08-30 实测落后了四五天)")
    return problems


def main() -> int:
    problems = check_gaps_doc_has_no_status()
    rows = 0
    for lineno, line in enumerate(DOC.read_text().splitlines(), 1):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # 只看能力表(5 列:环节/状态/验证级别/最后验证/证据),跳过说明性表格
        if len(cells) != 5 or cells[1] not in STATUSES:
            continue
        rows += 1
        环节, 状态, 级别, 日期, 证据 = cells

        if 级别 not in LEVELS:
            problems.append(f"{lineno}: 「{环节}」验证级别写的是「{级别}」,"
                            f"只能用 {'/'.join(sorted(LEVELS))}")
        if 状态 == "✅" and 级别 in {"未验证", "计划中"}:
            problems.append(
                f"{lineno}: 「{环节}」标成 ✅ 但验证级别是「{级别}」——"
                "没有人真的跑过的能力,状态最多只能是 🟡。这条规则是"
                "2026-08-21 一次跑出三处假 ✅ 之后定的")
        if 级别 == "生产验证":
            problems.append(
                f"{lineno}: 「{环节}」标成「生产验证」。这套东西还没上过生产,"
                "在 docs/project/production-readiness-gaps.md 的门禁项补齐前,"
                "任何一格都不许标这个")
        if 级别 not in {"计划中", "未验证", "—"} and not re.match(r"20\d\d-\d\d-\d\d$", 日期):
            problems.append(f"{lineno}: 「{环节}」验证过却没写日期(现在是「{日期}」)"
                            "——状态是有保质期的,不写日期就没法判断它有没有过期")
        if 状态 != "❌" and not 证据.strip("— "):
            problems.append(f"{lineno}: 「{环节}」没有证据链接")

    if problems:
        print(f"能力/缺口文档有 {len(problems)} 个问题:", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1
    print(f"capability-matrix.md:{rows} 条能力,状态和证据一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
