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
    # 2026-08-26 补:这一对是 08-23 加 ADR-066 审计链路时新增的,当时**忘了
    # 注册进来**——也就是说它有两份拷贝、却有三天不受这个检查保护。
    # 这正是这个检查器存在的理由本身:靠人记得同步一定会漏,而漏了之后
    # 两份内容不一致**不会有任何地方报错**,直到某次部署跑的是老版本。
    ("apps/flink-audit-sink/manifests/script-configmap.yaml", "trino_audit_sink.py", "scripts/flink_trino_audit_sink.py"),
    # 2026-08-26 补:`definitions.py` **同时存在于三个地方**(scripts/ 一份 +
    # 两个 ConfigMap),而且加这条检查时实测已经漂移了——apps/feast 那份比
    # scripts/ 那份少 6 行。差的全是注释(功能一样),但丢掉的恰恰是"为什么
    # 用 query 不用 table""ttl 为什么给这么大"这类只有当时那个人知道的东西。
    ("apps/feast/manifests/feature-repo-configmap.yaml", "definitions.py", "scripts/feast_feature_repo/definitions.py"),
    ("apps/argo-workflows-training-image/manifests/feast-feature-repo-configmap.yaml", "definitions.py", "scripts/feast_feature_repo/definitions.py"),
]


# 明确声明"只有 ConfigMap 里这一份、没有 scripts/ 下的副本"的内嵌脚本。
# **每一条都要写清楚为什么**——这个清单没有理由就会退化成"报错了就往里
# 加一行",和 check-image-tags.py 的 ALLOWED 是同一个道理。
INLINE_ONLY = {
    # Airflow 的 DAG 有**另一个**专门的同步检查器
    # (`scripts/sync-airflow-dags-configmap.py --check`,早就在 CI 里),
    # 不要在这里重复登记——两个检查器管同一对文件,迟早会出现一个说一致
    # 另一个说漂移的情况。
    ("apps/airflow/manifests/dags-configmap.yaml", "seatunnel_device_events.py"):
        "由 sync-airflow-dags-configmap.py 负责",
    ("apps/airflow/manifests/dags-configmap.yaml", "feast_materialize.py"):
        "由 sync-airflow-dags-configmap.py 负责",
    ("apps/airflow/manifests/dags-configmap.yaml", "dbt_demo.py"):
        "由 sync-airflow-dags-configmap.py 负责",
    ("apps/airflow/manifests/dags-configmap.yaml", "platform_sdk_demo.py"):
        "由 sync-airflow-dags-configmap.py 负责",
    # 黄金链路探针(ADR-079)。**它只有这一份**:源文件在
    # templates/apps-golden-path-probes-manifests/,渲染产物在
    # apps/golden-path-probes/manifests/,scripts/ 下没有对应副本。
    #
    # 为什么不像 flink/kafka 那两个作业那样在 scripts/ 下也放一份"给人看的":
    # 那个模式存在的理由是那些脚本人要在本地读、要能单独跑;而这段探针只在
    # CronJob 里跑、120 行、逻辑全在注释里,**多一份副本就是多一处会漂移的
    # 地方**——这个检查器本身就是为了防那个而写的。
    ("apps/golden-path-probes/manifests/configmap.yaml", "probe.py"):
        "只有这一份(源在 templates/,scripts/ 下没有副本),见 ADR-079",
    # 告警回显接收端(ADR-081)。同上:只有这一份,55 行,只在集群里跑,
    # scripts/ 下放副本没有意义、只会多一处会漂移的地方。
    ("apps/alert-echo-sink/manifests/configmap.yaml", "sink.py"):
        "只有这一份(不在 scripts/ 下留副本),见 ADR-081",
    # 这个是统一镜像的自检 demo 脚本,只在这一处存在,没有 scripts/ 副本。
    ("apps/platform-image/manifests/demo-script-configmap.yaml", "job.py"):
        "只有 ConfigMap 这一份,scripts/ 下没有对应文件",
}


def find_unregistered() -> list[str]:
    """扫出"ConfigMap 里有 .py 内容、却没登记进 PAIRS"的组合。

    **这个函数才是这个脚本最该有的部分。** 2026-08-26 发现
    `flink-audit-sink` 那一对三天前就加了、却一直没注册进 PAIRS——
    也就是说它有两份拷贝却完全不受保护。检查器本身能被绕过的方式,
    恰恰就是"新增的那一对没人记得加进来"。

    靠人记得注册,和靠人记得同步,是同一种会失效的机制。所以这里不再
    依赖记性:直接去仓库里找所有内嵌的 .py,不在 PAIRS 里就报错。
    """
    registered = {(cm, key) for cm, key, _ in PAIRS}
    problems = []
    for f in sorted((REPO / "apps").rglob("*.yaml")):
        try:
            docs = list(yaml.safe_load_all(f.read_text()))
        except yaml.YAMLError:
            continue
        for d in docs:
            if not isinstance(d, dict) or d.get("kind") != "ConfigMap":
                continue
            for key in (d.get("data") or {}):
                if not key.endswith(".py"):
                    continue
                rel = str(f.relative_to(REPO))
                if (rel, key) in INLINE_ONLY:
                    continue
                if (rel, key) not in registered:
                    problems.append(
                        f"{rel} :: {key} —— ConfigMap 里内嵌了 python,但没有登记进 PAIRS。\n"
                        f"      要么在 PAIRS 里加一行指向 scripts/ 下对应的那份,\n"
                        f"      要么(确实只有这一份)在 INLINE_ONLY 里加一条带理由的声明。")
    return problems


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

    unregistered = find_unregistered()
    if unregistered:
        print(f"!! {len(unregistered)} 处内嵌脚本没有登记:", file=sys.stderr)
        for u in unregistered:
            print("   " + u, file=sys.stderr)
        sys.exit(1)

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
