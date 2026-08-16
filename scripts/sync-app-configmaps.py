#!/usr/bin/env python3
"""把 apps/*/manifests/app-configmap.yaml 里内嵌的 app.py 内容,从对应的
apps/*/src/app.py 重新生成——src/app.py 是唯一的源码真相,ConfigMap 里的
副本是生成产物,不再手动维护两份。

背景见 docs/BACKLOG.md P1"三个自建 Flask 工具补测试/锁依赖/单一源码"那条:
之前 app-configmap.yaml 顶部的注释写着"app.py 的内容和 src/app.py 保持
同步,改动时两边一起改",全靠人记得住,是真实的漂移风险,不是假设的。

只替换 `app.py: |` 这一个 data key 底下的内容,不动同一个文件里可能存在的
其它 data key(比如 permission-request-app 那份还有一个 employees.csv 是
demo 数据,不是源码,不该被这个脚本碰)。

用法:
  python3 scripts/sync-app-configmaps.py           # 重新生成,写回文件
  python3 scripts/sync-app-configmaps.py --check    # 只检查是否一致,不写
                                                       文件,退出码非0表示
                                                       有漂移(适合接进CI)
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APPS = ["permission-request-app", "platform-portal", "table-registration-app"]


def sync_one(app: str, check_only: bool) -> bool:
    """返回 True 表示无需改动(已经一致或者写成功),False 表示 check 模式下发现漂移。"""
    src_file = REPO_ROOT / "apps" / app / "src" / "app.py"
    cm_file = REPO_ROOT / "apps" / app / "manifests" / "app-configmap.yaml"
    if not src_file.exists() or not cm_file.exists():
        print(f"跳过 {app}: 找不到 {src_file} 或 {cm_file}")
        return True

    src_content = src_file.read_text()
    cm_lines = cm_file.read_text().splitlines(keepends=True)

    start = None
    for i, line in enumerate(cm_lines):
        if line.rstrip("\n") == "  app.py: |":
            start = i
            break
    if start is None:
        print(f"!! {app}: app-configmap.yaml 里没找到 'app.py: |' 这一行,跳过", file=sys.stderr)
        return False

    # app.py 内容块结束于:下一个缩进为2个空格的 data key(形如 "  xxx: |"),
    # 或者文件结尾
    end = len(cm_lines)
    for i in range(start + 1, len(cm_lines)):
        line = cm_lines[i]
        if line.rstrip("\n") and not line.startswith("    ") and not line.startswith("\t"):
            end = i
            break

    indented_src = "".join("    " + line if line.strip() else line for line in src_content.splitlines(keepends=True))
    if not indented_src.endswith("\n"):
        indented_src += "\n"

    current_block = "".join(cm_lines[start + 1:end])
    if current_block == indented_src:
        print(f"{app}: 已经一致")
        return True

    if check_only:
        print(f"!! {app}: ConfigMap 里的 app.py 和 src/app.py 不一致,漂移了")
        return False

    new_lines = cm_lines[:start + 1] + [indented_src] + cm_lines[end:]
    cm_file.write_text("".join(new_lines))
    print(f"{app}: 已重新生成 app-configmap.yaml")
    return True


def main():
    check_only = "--check" in sys.argv
    ok = True
    for app in APPS:
        if not sync_one(app, check_only):
            ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
