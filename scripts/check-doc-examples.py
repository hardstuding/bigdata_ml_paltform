#!/usr/bin/env python3
"""检查文档里的 platform_sdk 调用示例,参数名和真实签名对不对得上。

**为什么需要**:2026-08-29 发现 `docs/usage-guide.md` 里写着
`submit_job("train.py")`,而真实签名第一个参数是 `name` —— 照着文档写
**必然报错**。这类错误不会被任何东西发现:文档不执行,而读文档的人会
以为是自己环境的问题。

做法是用 `inspect.signature` 真的去绑定一次参数(`sig.bind`),不是正则
比对参数个数 —— 后者对关键字参数、默认值、可变参数都会判错。

只检查**能静态看出参数的调用**(字面量实参)。带变量的示例跳过,那需要
真正执行,不是这个检查的范围。

跑法:python3 scripts/check-doc-examples.py
"""
import ast
import inspect
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "platform-sdk"))

DOC_DIRS = [REPO / "docs", REPO / "examples", REPO / "jobs", REPO / "streams", REPO / "platform-sdk"]
CODE_FENCE = re.compile(r"```(?:python|py)\n(.*?)```", re.S)


def sdk_callables() -> dict:
    """收集 platform_sdk 对外暴露的可调用对象和它们的签名。

    **优先用 `__all__`,不是 `dir()`** —— platform_sdk 用 `__getattr__` 做延迟
    导入(submit 那几个函数需要 kubernetes 客户端,是 optional 依赖),
    `dir()` 看不到它们。第一版用 dir() 写的,结果 `submit_job` 压根没被检查到,
    而它恰恰是当时那个错误示例用的函数 —— 检查器"通过"了,问题还在。
    """
    import platform_sdk
    names = list(getattr(platform_sdk, "__all__", None) or dir(platform_sdk))
    out = {}
    for name in names:
        if name.startswith("_"):
            continue
        try:
            obj = getattr(platform_sdk, name)
        except Exception:  # noqa: BLE001 - 延迟导入可能因为缺可选依赖而失败
            continue
        if not callable(obj):
            continue
        try:
            out[name] = inspect.signature(obj)
        except (TypeError, ValueError):
            pass
    return out


def main() -> None:
    sigs = sdk_callables()
    if not sigs:
        print("!! 导入不到 platform_sdk,这个检查没有意义,直接失败")
        sys.exit(1)

    problems, checked = [], 0
    for d in DOC_DIRS:
        if not d.exists():
            continue
        for md in list(d.rglob("*.md")):
            for block in CODE_FENCE.findall(md.read_text(errors="ignore")):
                try:
                    tree = ast.parse(block)
                except SyntaxError:
                    continue  # 片段不是完整语法,跳过
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                        continue
                    fn = node.func.id
                    if fn not in sigs:
                        continue
                    # 只处理实参全是字面量/简单名字的调用
                    args = ["x"] * len(node.args)
                    kwargs = {}
                    ok_to_check = True
                    for kw in node.keywords:
                        if kw.arg is None:      # **kwargs 展开,静态判不了
                            ok_to_check = False
                            break
                        kwargs[kw.arg] = "x"
                    if not ok_to_check:
                        continue
                    checked += 1
                    try:
                        sigs[fn].bind(*args, **kwargs)
                    except TypeError as exc:
                        problems.append(
                            f"{md.relative_to(REPO)}: `{fn}(...)` 和真实签名对不上 —— {exc}\n"
                            f"      真实签名: {fn}{sigs[fn]}")

    print(f"扫了 {len(sigs)} 个 SDK 可调用对象,检查了 {checked} 处文档示例调用。")
    if problems:
        print("\n照着写会报错的示例:")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("文档示例的参数都能和真实签名对上。")


if __name__ == "__main__":
    main()
