#!/usr/bin/env python3
"""CI 检查:platform/iam/ 这三份数据自洽。

**为什么需要**:2026-08-27 发现 `memberships.csv` 里只有 2 行(admin /
zhenghe),而 `employees.csv` 里有 5 个人——**analyst001 / algo001 / ceo001
不属于任何组**。而 `scripts/12-sync-iam.py` 是拿 memberships.csv 往 Keycloak
同步组成员的,也就是说:

- 他们登不进 JupyterHub(`allowed_groups` 按组白名单);
- 提交作业拿不到 Kueue 队列(`platform_sdk.queue_name()` 按组推断);
- Trino 那边 group provider 也看不到他们(ADR-078)。

**表面上一切正常**——组、角色、队列、策略全都配好了,只是没人在里面。
这类"结构齐全但数据没接上"的缺口不会报错,只会在有人真的去用的时候才发现。

检查三条:
  1. employees.csv 里的每个人,在 memberships.csv 里至少属于一个组
  2. memberships.csv 里引用的组,在 groups.yaml 里存在
  3. memberships.csv 里的用户,在 employees.csv 里存在(防拼写错)

用法:python3 scripts/check-iam-consistency.py
"""
import csv
import sys
from pathlib import Path

import yaml

IAM = Path(__file__).resolve().parent.parent / "platform" / "iam"


def main() -> int:
    employees = {r["username"].strip() for r in csv.DictReader(open(IAM / "employees.csv"))
                 if r.get("username", "").strip()}
    groups = {g["name"] for g in yaml.safe_load((IAM / "groups.yaml").read_text())["groups"]}
    members = [(r["username"].strip(), r["group"].strip())
               for r in csv.DictReader(open(IAM / "memberships.csv"))
               if r.get("username", "").strip()]

    problems = []
    in_group = {u for u, _ in members}
    for u in sorted(employees - in_group):
        problems.append(f"{u} 在 employees.csv 里,但不属于任何组 —— "
                        "他登不进 JupyterHub、拿不到 Kueue 队列、Trino 也看不到他的组")
    for u, g in members:
        if g not in groups:
            problems.append(f"memberships.csv 里的组 `{g}`(用户 {u})在 groups.yaml 里不存在")
        if u not in employees:
            problems.append(f"memberships.csv 里的用户 `{u}` 在 employees.csv 里不存在(拼写错?)")

    if problems:
        print(f"!! platform/iam/ 有 {len(problems)} 处不自洽:", file=sys.stderr)
        for p in problems:
            print("   " + p, file=sys.stderr)
        return 1
    print(f"platform/iam/ 自洽:{len(employees)} 个人、{len(groups)} 个组、"
          f"{len(members)} 条成员关系,每个人都至少属于一个组。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
