#!/usr/bin/env python3
"""把 apps/airflow/manifests/dags-configmap.yaml 里内嵌的各个 DAG 内容,从
对应的 apps/airflow/dags/*.py 重新生成——dags/*.py 是唯一的源码真相,
ConfigMap 里的副本是生成产物,不再手动维护两份。

背景见 docs/BACKLOG.md P1.2a:这个仓库已经踩过一次真实的分叉——
`dbt_demo.py` 的源文件和 ConfigMap 副本不一致过(ConfigMap 里的版本
锁定是任务#13 单独做的,源文件当时没有同步更新),当时手动改成一致,
但没有解决"以后还会分叉"这个根本问题。这个脚本当初是仿照
`scripts/sync-app-configmaps.py`(3 个自建 Flask 工具用的同一个模式)
写的,差异只是这里是"一个 ConfigMap 里多个 data key",那边是"每个 app
各自一个 ConfigMap 一个 key"——2026-08-20(BACKLOG 2.1)那边已经改成
构建期固化进镜像、`sync-app-configmaps.py` 已退役,这个脚本本身管的是
Airflow DAG(还是 ConfigMap 挂载模式,没有跟着改),继续用,只是不能再
拿"和那边同一个模式"当参照了。

用法:
  python3 scripts/sync-airflow-dags-configmap.py           # 重新生成,写回文件
  python3 scripts/sync-airflow-dags-configmap.py --check   # 只检查是否一致,
                                                              不写文件,退出码
                                                              非 0 表示有漂移
                                                              (适合接进 CI)
"""
import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DAGS_DIR = REPO_ROOT / "apps" / "airflow" / "dags"
CM_FILE = REPO_ROOT / "apps" / "airflow" / "manifests" / "dags-configmap.yaml"


def sync_one(dag_file: Path, cm_lines: list[str], check_only: bool) -> tuple[list[str], bool]:
    """返回 (可能更新过的 cm_lines, ok)。ok=False 表示 check 模式下发现漂移,
    或者压根没在 ConfigMap 里找到对应的 key(后者视为失败,不是静默跳过——
    dags/ 下每个 .py 文件都该在 ConfigMap 里有对应 key,没有大概率是真的
    漏挂载了,不是这个脚本该自己"猜"着放过的情况)。
    """
    key = f"  {dag_file.name}: |"
    src_content = dag_file.read_text()

    start = None
    for i, line in enumerate(cm_lines):
        if line.rstrip("\n") == key:
            start = i
            break
    if start is None:
        print(f"!! {dag_file.name}: dags-configmap.yaml 里没找到 '{key.strip()}' 这个 key,跳过", file=sys.stderr)
        return cm_lines, False

    # 内容块结束于:下一个缩进为 2 个空格的 data key(形如 "  xxx: |"),
    # 或者文件结尾。和 sync-app-configmaps.py 同一个判断逻辑。
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
        print(f"{dag_file.name}: 已经一致")
        return cm_lines, True

    if check_only:
        print(f"!! {dag_file.name}: ConfigMap 里的内容和 dags/{dag_file.name} 不一致,漂移了")
        return cm_lines, False

    new_lines = cm_lines[:start + 1] + [indented_src] + cm_lines[end:]
    print(f"{dag_file.name}: 已重新生成")
    return new_lines, True


AIRFLOW_COMPONENT = REPO_ROOT / "apps" / "components" / "airflow.yaml"
CHECKSUM_RE = re.compile(r'(platform/dags-checksum: ")[^"]*(")')


def sync_checksum(dag_files, check_only: bool) -> bool:
    """把 DAG 内容的哈希写进 apps/components/airflow.yaml 的 podAnnotations。

    **为什么必须有这一步**:DAG 是 subPath 挂 ConfigMap 的,而 subPath 挂载的
    ConfigMap **Kubernetes 永远不会更新**(不是有延迟,是根本不更新,官方文档
    写明了)。所以「改 DAG → push → ArgoCD Synced」这条链看起来完整,实际上
    跑着的 Airflow 一直是旧代码。2026-08-29 给三条 DAG 加定时时实测撞到:
    unpause 成功、ConfigMap 里确实是新内容、而 next_dagrun 一直是 None,
    进 pod 里 grep 才发现文件是老的。

    把内容哈希写进 pod 模板的注解,DAG 一改哈希就变,pod 模板就变,ArgoCD
    自动滚更 —— 不依赖任何人记得去重启。
    """
    h = hashlib.sha256()
    for f in dag_files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    digest = h.hexdigest()[:16]

    text = AIRFLOW_COMPONENT.read_text()
    new_text, n = CHECKSUM_RE.subn(rf"\g<1>{digest}\g<2>", text)
    if n == 0:
        print(f"!! {AIRFLOW_COMPONENT} 里找不到 platform/dags-checksum 注解 —— "
              f"这个机制被人删掉了?没有它,改 DAG 不会生效。", file=sys.stderr)
        return False
    if new_text == text:
        return True
    if check_only:
        print(f"!! DAG 内容变了但 {AIRFLOW_COMPONENT.name} 里的 dags-checksum 没更新"
              f"(应该是 {digest})。跑一次 scripts/sync-airflow-dags-configmap.py。",
              file=sys.stderr)
        return False
    AIRFLOW_COMPONENT.write_text(new_text)
    print(f"已更新 {AIRFLOW_COMPONENT.name} 里 {n} 处 dags-checksum -> {digest}")
    return True


def main():
    check_only = "--check" in sys.argv
    if not CM_FILE.exists():
        print(f"!! 找不到 {CM_FILE}", file=sys.stderr)
        sys.exit(1)

    dag_files = sorted(DAGS_DIR.glob("*.py"))
    if not dag_files:
        print(f"!! {DAGS_DIR} 下没有找到任何 .py 文件", file=sys.stderr)
        sys.exit(1)

    cm_lines = CM_FILE.read_text().splitlines(keepends=True)
    ok = True
    changed = False
    original_lines = list(cm_lines)
    for dag_file in dag_files:
        cm_lines, one_ok = sync_one(dag_file, cm_lines, check_only)
        if not one_ok:
            ok = False

    if not check_only and cm_lines != original_lines:
        CM_FILE.write_text("".join(cm_lines))
        changed = True

    if not check_only and changed:
        print(f"已写回 {CM_FILE}")

    if not sync_checksum(dag_files, check_only):
        ok = False

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
