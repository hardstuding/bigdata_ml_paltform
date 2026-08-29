#!/usr/bin/env python3
"""检查组件定义里有没有**新写死**的资源规格。

**为什么需要这个检查**:"三个环境改配置切换,不是维护三套手动漂移的副本"
是这个仓库的三条底线之一(见 CLAUDE.md)。规格分档的机制(ADR-059,
`{{RES:key}}` 占位符 + environments/resource-profiles.yaml)早就有了,但
机制存在不等于被用——2026-08-29 盘点时,56 个组件里只有 9 个用了占位符,
11 个把 local-lite 的小规格写死在文件里。**照搬到生产硬件上等于用一台
笔记本的配置跑生产**。

这个检查不要求"所有资源都必须参数化"——operator 类组件的开销和数据规模
基本无关,为它们各造三档只是噪音。它要求的是:**写死的必须显式登记在
下面的豁免表里,并写清楚为什么**。这样"漏了"和"想清楚了不做"就能区分开,
不会又退回到"看起来都一样"的状态。

跑法:python3 scripts/check-resource-profiles.py
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COMPONENTS = REPO / "apps" / "components"

# 允许写死资源的组件 -> 理由。加进来之前先问一句:这个值在生产上会不会
# 真的伤到人?会的话应该参数化,不是加进这张表。
EXEMPT = {
    "argo-workflows": "workflow controller / server 是控制面,开销和数据规模无关;真要动的是副本数(HA),属于'生产高可用'那个更大的话题",
    "cloudnative-pg-operator": "operator 本身,同上",
    "kafka-operator": "operator 本身(Strimzi),同上;它管的 Kafka 集群规格已经参数化(kafka_* 那组键)",
    "spark-operator": "operator 本身,同上;它拉起的 Spark 作业规格由作业自己声明",
    "flink-kubernetes-operator": "operator 本身,同上;Flink 作业规格已参数化(flink_* 那组键)",
    "kueue": "配额数字本身就是按环境分档的(kueue_* 那组键),这里剩下的是 controller 自己的开销",
    "superset": "剩下的写死值是 gunicorn worker 数这类和硬件无关的调参,不是资源规格",
}

RES_RE = re.compile(r"\{\{RES:[a-z0-9_]+\}\}")
# 只看 helm valuesObject / manifest 里真正的资源字段,避免误伤注释和端口号
# `limit`/`guarantee` 要求值带单位后缀(1Gi / 512M / 0.5),否则会把 ArgoCD
# syncPolicy 里的 `retry.limit: 10` 当成资源规格误报。代价是纯数字的 CPU
# 值(KubeSpawner 的 `cpu.guarantee: 0.1`)会漏掉——那个字段目前已经参数化,
# 真要漏也只漏这一个,比每次都误报强。
HARD_RE = re.compile(
    r"^\s+(?:cpu|memory|replicas|replicaCount|capacity)\s*:\s*[\"']?[0-9]"
    r"|^\s+(?:guarantee|limit)\s*:\s*[\"']?[0-9.]+(?:m|M|Mi|G|Gi|Ti|K|Ki)\b",
    re.M,
)


def main() -> None:
    problems = []
    checked = 0
    for p in sorted(COMPONENTS.glob("*.yaml")):
        text = p.read_text()
        # 去掉注释行再判断,不然注释里举例写的数字会误报
        body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        hard = HARD_RE.findall(body)
        if not hard:
            continue
        checked += 1
        if p.stem in EXEMPT:
            continue
        if RES_RE.search(body):
            # 用了占位符但仍有写死的:大多是同一个组件里有的参数化了有的没有
            problems.append(
                f"{p.name}:用了 {{{{RES:}}}} 占位符,但还有 {len(hard)} 处写死的资源数字。"
                f"要么一起参数化,要么加进这个脚本的 EXEMPT 并写明理由。")
        else:
            problems.append(
                f"{p.name}:资源规格完全写死({len(hard)} 处),没有用 {{{{RES:}}}} 占位符。"
                f"照搬到生产等于用 local-lite 的规格跑生产。")

    print(f"扫了 {len(list(COMPONENTS.glob('*.yaml')))} 个组件,其中 {checked} 个声明了资源规格,"
          f"豁免 {len(EXEMPT)} 个(理由见脚本)。")
    if problems:
        print("\n发现写死的资源规格:")
        for x in problems:
            print("  -", x)
        sys.exit(1)
    print("没有发现未登记的写死规格。")


if __name__ == "__main__":
    main()
