#!/usr/bin/env python3
"""校验"被内嵌进 ConfigMap 的脚本"和 scripts/ 下的权威源码没有漂移。

背景:这个仓库有几处"同一份脚本存两遍"——权威源在 `scripts/`(给人读、
也是手动跑时用的那份),运行态的副本内嵌在 ConfigMap 里(pod 真正挂载的
那份)。`apps/airflow/manifests/dags-configmap.yaml` 那一处早就有
`sync-airflow-dags-configmap.py --check` 接进 CI 防漂移,但
`apps/argo-workflows-training-image/manifests/script-configmap.yaml`
一直没有任何保护——2026-08-21 实测确认它**已经漂移了**(scripts/ 那份
后来补了一段 pandas 隐式依赖的踩坑说明,ConfigMap 副本没跟上;这次只是
注释不一致、代码一样,但没有任何机制保证下次漂的不是代码)。

刻意做成一个**通用**检查器而不是再写第三个 `sync-xxx-configmap.py`:
`docs/BACKLOG.md` 2.2 点名过"生成式单一源码脚本在增殖"这个症状,不该
每加一个 ConfigMap 就复制一份几乎一样的脚本。新增一对映射关系,在下面
`PAIRS` 里加一行就行。

用法:
    python3 scripts/check-embedded-scripts.py          # 只检查,漂移则非零退出(CI 用)
    python3 scripts/check-embedded-scripts.py --fix    # 用 scripts/ 的内容覆盖 ConfigMap
"""
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

# (ConfigMap 文件, data 里的 key, scripts/ 下的权威源文件)
PAIRS = [
    ("apps/argo-workflows-training-image/manifests/script-configmap.yaml", "train_demo_model.py", "scripts/train_demo_model.py"),
    ("apps/argo-workflows-training-image/manifests/script-configmap.yaml", "train_from_feast_features.py", "scripts/train_from_feast_features.py"),
    ("apps/argo-workflows-training-image/manifests/script-configmap.yaml", "materialize_features.py", "scripts/materialize_features.py"),
    ("apps/argo-workflows-training-image/manifests/script-configmap.yaml", "validate_registered_model.py", "scripts/validate_registered_model.py"),
    ("apps/spark-iceberg-demo/manifests/script-configmap.yaml", "spark_iceberg_demo.py", "scripts/spark_iceberg_demo.py"),
    ("apps/flink-streaming-demo/manifests/script-configmap.yaml", "device_events_stream.py", "scripts/flink_device_events_stream.py"),
    ("apps/kafka-producer/manifests/script-configmap.yaml", "producer.py", "scripts/kafka_device_events_producer.py"),
]


def rewrite(cm_path: Path, key: str, body: str) -> None:
    """只替换这一个 data key 底下的内容块,不动同一个文件里其它 key。"""
    lines = cm_path.read_text().splitlines(keepends=True)
    marker = f"  {key}: |\n"
    start = lines.index(marker)
    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i]
        # 遇到下一个 2 空格缩进的 key(不是 4 空格的内容行)就是本块结束
        if stripped.strip() and stripped.startswith("  ") and not stripped.startswith("    "):
            end = i
            break
    indented = "\n".join("    " + l if l.strip() else "" for l in body.splitlines()) + "\n"
    cm_path.write_text("".join(lines[: start + 1]) + indented + "".join(lines[end:]))


def main() -> None:
    fix = "--fix" in sys.argv
    drifted = 0
    for cm_rel, key, src_rel in PAIRS:
        cm_path, src_path = REPO / cm_rel, REPO / src_rel
        if not cm_path.exists() or not src_path.exists():
            print(f"!! 跳过(文件不存在): {cm_rel} :: {key}", file=sys.stderr)
            drifted += 1
            continue
        src = src_path.read_text()
        embedded = yaml.safe_load(cm_path.read_text())["data"].get(key)
        if embedded == src:
            print(f"一致  {cm_rel} :: {key}")
            continue
        drifted += 1
        if fix:
            rewrite(cm_path, key, src)
            print(f"已修复 {cm_rel} :: {key}(用 {src_rel} 覆盖)")
        else:
            print(f"!! 漂移 {cm_rel} :: {key} 和 {src_rel} 不一致", file=sys.stderr)

    if drifted and not fix:
        print(f"\n{drifted} 处漂移。跑 `python3 scripts/check-embedded-scripts.py --fix` 修复。", file=sys.stderr)
        sys.exit(1)
    print(f"\n检查 {len(PAIRS)} 对," + (f"修复 {drifted} 处。" if fix else "全部一致。"))


if __name__ == "__main__":
    main()
