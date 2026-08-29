#!/usr/bin/env python3
"""服务目录的覆盖检查 —— 让这份目录不会腐烂成一份过期清单。

**这个检查比目录内容本身更重要**。一份手写的服务清单,如果没有任何东西
逼它跟上现实,三个月后就会变成"看起来很全但有一半是错的",那比没有更糟
——这个仓库已经在 roles.md、BACKLOG、ADR 里各栽过一次"文档和事实反了"。

规则:`environments/*/config.yaml` 的 `enabled_components` 里每一项,要么在
`platform/service-catalog.yaml` 的 `services` 里有条目(按组件文件名去掉
`.yaml` 匹配,允许目录条目名和组件名不同时用 `component:` 字段显式指定),
要么在 `infra_only` 里登记并写明归属。两边都没有 = CI 红。

另外校验:
  - owner 必须是 platform/iam/groups.yaml 里真实存在的组(不能写个不存在的组)
  - 依赖里提到的服务必须在目录里存在(不能指向一个不存在的服务)
  - 目录里不能有已经不在任何环境 enabled_components 里的僵尸条目

跑法:python3 scripts/check-service-catalog.py
"""
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / "platform" / "service-catalog.yaml"
GROUPS = REPO / "platform" / "iam" / "groups.yaml"
ENVS = REPO / "environments"


def main() -> None:
    cat = yaml.safe_load(CATALOG.read_text())
    services = {s.get("component", s["name"] + ".yaml"): s for s in cat["services"]}
    infra = cat.get("infra_only", {}) or {}

    groups_raw = yaml.safe_load(GROUPS.read_text())
    if isinstance(groups_raw, dict) and "groups" in groups_raw:
        groups_raw = groups_raw["groups"]
    group_names = {g["name"] if isinstance(g, dict) else g for g in groups_raw}

    enabled = {}
    for cfg in sorted(ENVS.glob("*/config.yaml")):
        env = cfg.parent.name
        for c in yaml.safe_load(cfg.read_text()).get("enabled_components", []):
            enabled.setdefault(c, []).append(env)
    # platform/apps/ 是平台底座,不参与"按环境启用"(三档都有),但同样要
    # 进目录 —— 2026-08-29 第一版漏了这一层,检查器立刻把 keycloak 报成
    # "僵尸条目",反过来说明这个检查是有效的。
    for app in sorted((REPO / "platform" / "apps").glob("*.yaml")):
        enabled.setdefault(f"platform/apps/{app.name}", []).append("平台底座")

    problems = []

    for comp, envs in sorted(enabled.items()):
        if comp in services or comp in infra:
            continue
        problems.append(
            f"组件 {comp}({'/'.join(envs)} 启用)既不在服务目录里,也没登记成 infra_only。"
            f"加一个条目说明它是干什么的、归谁;确实不算独立服务的话写进 infra_only 并注明归属。")

    for comp, s in sorted(services.items()):
        if s["owner"] not in group_names:
            problems.append(f"{s['name']} 的 owner「{s['owner']}」不在 platform/iam/groups.yaml 里。")
        for dep in s.get("依赖", []) or []:
            if dep + ".yaml" not in services and dep not in {x["name"] for x in cat["services"]}:
                problems.append(f"{s['name']} 依赖的「{dep}」在目录里不存在(写错名字?还是该给它补个条目?)")
        if comp not in enabled:
            problems.append(f"目录里的 {s['name']} 已经不在任何环境的 enabled_components 里,是僵尸条目。")

    if "--write-doc" in sys.argv or "--check-doc" in sys.argv:
        _doc(cat, enabled)

    print(f"服务目录 {len(services)} 条 + infra_only {len(infra)} 条,"
          f"覆盖三个环境合计 {len(enabled)} 个启用组件。")
    if problems:
        print("\n发现问题:")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("覆盖完整,owner 和依赖都指向真实存在的东西。")


DOC = REPO / "docs" / "operations" / "service-catalog.md"


def _render(cat) -> str:
    """从 YAML 生成给人读的那份。**单一源码**:md 是生成物,不要手改。"""
    out = ["# 服务目录", "",
           "> **这份文件是生成的,不要手改。**源码是 `platform/service-catalog.yaml`,",
           "> 改完跑 `python3 scripts/check-service-catalog.py --write-doc` 重新生成。",
           "> CI 会校验两者不漂移。", "",
           "一个地方回答:这个服务是干什么的、谁负责、坏了要紧吗、出问题看哪里。",
           "排障 Runbook(`troubleshooting.md`)回答的是「这个症状怎么处理」,",
           "这份目录回答它前面那个问题:「这是什么、归谁、坏了影响谁」。", ""]

    cats = {}
    for s in cat["services"]:
        cats.setdefault(s["类别"], []).append(s)

    # 先把"坏了会直接断用户请求"的挑出来单列一节 —— 出事时最先要看的就是这些
    critical = [s for s in cat["services"] if s.get("请求路径上")]
    out += ["## 在请求路径上(它挂了,用户立刻感知)", "",
            "| 服务 | 用途 | 负责组 |", "|---|---|---|"]
    for s in sorted(critical, key=lambda x: x["name"]):
        out.append(f"| **{s['name']}** | {s['用途']} | {s['owner']} |")
    out.append("")

    for c in sorted(cats):
        out += [f"## {c}", "",
                "| 服务 | 用途 | 负责组 | 面向用户 | 依赖 | 出问题看哪里 |",
                "|---|---|---|---|---|---|"]
        for s in sorted(cats[c], key=lambda x: x["name"]):
            deps = "、".join(s.get("依赖") or []) or "—"
            note = f"<br>{s['备注']}" if s.get("备注") else ""
            out.append(f"| **{s['name']}** | {s['用途']}{note} | {s['owner']} | "
                       f"{'是' if s.get('面向用户') else '否'} | {deps} | {s.get('排障','—')} |")
        out.append("")

    out += ["## 不单独立条目的支撑资源", "",
            "写在这里是为了让「没登记」和「不需要登记」能区分开 —— 直接不写的话,",
            "下次有人加了新组件忘了登记,检查器分不出是遗漏还是有意。", "",
            "| 组件 | 归属 |", "|---|---|"]
    for k, v in sorted((cat.get("infra_only") or {}).items()):
        out.append(f"| `{k}` | {v} |")
    out.append("")
    return "\n".join(out)


def _doc(cat, enabled) -> None:
    text = _render(cat)
    if "--write-doc" in sys.argv:
        DOC.write_text(text)
        print(f"已生成 {DOC.relative_to(REPO)}")
    elif DOC.exists() and DOC.read_text() != text:
        print(f"!! {DOC.relative_to(REPO)} 和 platform/service-catalog.yaml 漂移了,"
              f"跑 python3 scripts/check-service-catalog.py --write-doc 重新生成")
        sys.exit(1)


if __name__ == "__main__":
    main()
