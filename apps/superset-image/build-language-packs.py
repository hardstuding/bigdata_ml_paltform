#!/usr/bin/env python3
"""把 Superset 的 .po 翻译编译成**前端**用的 language pack(jed JSON)。

**为什么单独需要这一步**(ADR-077 第二半):Superset 的界面分两半,两半用
的是**完全不同的两套翻译产物**:

  - Flask / FAB 渲染的那部分(登录、管理页)读 gettext 的 `.mo`
    —— `pybabel compile` 产出,Dockerfile 里已经做了;
  - **React 主界面**(看板、图表、SQL Lab,也就是绝大多数人真正在看的界面)
    读 `superset/translations/<lang>/LC_MESSAGES/messages.json`,是 jed 1.x
    格式,`pybabel compile` **不会**产出它。

2026-08-27 实测:编译完 22 个 `.mo` 之后,镜像里前端翻译只有
`empty_language_pack.json`,最大的那个 JS chunk 里一个中文都没有——也就是
**React 界面仍然全英文**。只做前一半,用户感知到的就是"没汉化"。

官方是在前端构建时用 npm 脚本生成这个 JSON 的,而我们的镜像是在官方镜像上
加东西、没有前端构建链。这个脚本直接从同一份 `.po` 生成等价的 JSON,不需要
node,也不改任何翻译内容。
"""
import json
import sys
from pathlib import Path

from babel.messages.pofile import read_po

TRANSLATIONS = Path(sys.argv[1] if len(sys.argv) > 1 else "/app/superset/translations")


def build(po_path: Path, locale: str) -> dict:
    with po_path.open() as f:
        catalog = read_po(f, locale=locale)
    # jed 1.x 的结构:每个 msgid -> [msgstr, ...],空 key 放元数据。
    data = {"": {"domain": "superset", "lang": locale,
                 "plural_forms": catalog.plural_expr and
                 f"nplurals={catalog.num_plurals}; plural={catalog.plural_expr};"
                 or "nplurals=1; plural=0;"}}
    for msg in catalog:
        if not msg.id or not msg.string:
            continue
        key = msg.id if isinstance(msg.id, str) else msg.id[0]
        val = msg.string
        data[key] = [val] if isinstance(val, str) else list(val)
    return {"domain": "superset", "locale_data": {"superset": data}}


def main() -> int:
    made = 0
    for po in sorted(TRANSLATIONS.glob("*/LC_MESSAGES/messages.po")):
        locale = po.parent.parent.name
        pack = build(po, locale)
        out = po.with_name("messages.json")
        out.write_text(json.dumps(pack, ensure_ascii=False))
        n = len(pack["locale_data"]["superset"]) - 1
        print(f"  {locale}: {n} 条 -> {out}")
        made += 1
    if made == 0:
        # 不能静默成功:哪天上游改了目录结构,构建照样通过而界面回到英文,
        # 正是这个项目最忌讳的"看起来成功了"。
        print("!! 一个 .po 都没找到,翻译产物没生成", file=sys.stderr)
        return 1
    print(f"共生成 {made} 个 language pack")
    return 0


if __name__ == "__main__":
    sys.exit(main())
