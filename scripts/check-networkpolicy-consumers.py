#!/usr/bin/env python3
"""检查:引用了共享服务的命名空间,有没有被漏加进对应的 NetworkPolicy 白名单。

**为什么要有这个检查**——这是这个仓库反复踩的同一类坑,不是假想风险:
  - 2026-08-13 Trino 查 Iceberg 报 "Failed connecting to Hive metastore"
  - 2026-08-14 Feast 物化连不上 Hive Metastore / MinIO
  - 2026-08-19 Argo Workflows 训练上传 artifact 时 MinIO EndpointConnectionError
  - 2026-08-20 train-from-feast-features 连 Hive Metastore 403/Connection refused
  - 2026-08-21 SeaTunnel 写 Iceberg 连 Hive Metastore Connection refused
每次都是"新命名空间消费了一个共享服务,但没人记得去中心化的白名单里加一行"。
而且**往往过很久才被发现**——组件 ArgoCD 显示 Synced/Healthy,只有真的
跑一次那条数据路径才会暴露(SeaTunnel 那次 DAG 长期暂停,`docs/roles.md`
就一直写着"批量数据接入 ✅",实际从来没通过)。

这个检查把"消费者列表"从人工维护变成可校验的:扫描仓库里所有引用共享
服务 DNS 的文件,推断它们部署到哪个命名空间,和 NetworkPolicy 里的白名单
对账,漏了就让 CI 失败。

**必读:它抓得到什么、抓不到什么**

抓得到 —— **直接引用**:某个命名空间里的 manifest/DAG 自己就写着共享服务的
DNS。上面列的 Feast、Argo Workflows 那几次都属于这类,这个检查能提前拦下。

**抓不到 —— 间接连接**:调用方只是往服务 A 提交一个请求,真正去连共享服务的
是 A 自己的 pod。**2026-08-21 SeaTunnel 那次恰恰就是这类**:Airflow DAG 通过
REST API 把作业提交给 `seatunnel-0`,真正连 Hive Metastore 的是 seatunnel
命名空间里的那个 pod,DAG 源码里根本没有 `namespace="seatunnel"`。也就是说
**这个检查当初并拦不住那个促成它诞生的 bug**——这点必须说清楚,不能让人
以为有了它就安全了。

所以:**这个检查通过 ≠ 网络路径通**。它只是把最常见的那类低级遗漏自动化
拦掉,真实的端到端链路验证一次都不能少。误报在下面 `IGNORE` 里加豁免。

用法:
    python3 scripts/check-networkpolicy-consumers.py


**已知盲区(2026-08-28 实测撞到)**:这个检查器只扫**仓库里的 manifest**。
`scripts/11-deploy-demo-inference-service.sh` 在运行时用 kubectl 建
`kserve-demo` 命名空间并在里面拉 MinIO 的模型产物——检查器看不到它,于是
NetworkPolicy 上线(ADR-035)之后那条推理链路其实已经断了,而没人发现,
因为从那以后没人再跑过 scripts/11。

下面的 EXTRA_CONSUMERS 就是为这类"命名空间不在 git 里"的消费者准备的:
**手工登记,并写明它由哪个脚本创建**。手工登记不优雅,但比"检查器报绿而
实际是断的"强——后者会让人更信任这个检查器,而它并不配。
"""
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

# 共享服务 DNS 片段 -> (定义白名单的 NetworkPolicy 文件, NetworkPolicy 名字)
SERVICES = {
    "hive-metastore.data.svc.cluster.local": (
        "platform/network-policies/manifests/postgres.yaml",
        "allow-consumers-to-hive-metastore",
    ),
    "minio.minio.svc.cluster.local": (
        "platform/network-policies/manifests/minio.yaml",
        "allow-consumers-to-minio",
    ),
}

# 已知豁免:命名空间 -> 理由。共享服务自己所在的命名空间不需要白名单
# (default-deny 只挡跨命名空间入站,同命名空间另有规则或本来就允许)。
IGNORE = {
    "data": "共享服务自己所在的命名空间",
    "minio": "共享服务自己所在的命名空间",
    "argocd": "只是 Application 定义文件里出现字符串,不是真的发起连接",
}

SKIP_DIRS = {".git", ".claude", "logs", "image-cache", "image-cache-amd64", "__pycache__"}


def _ns_from_text(text: str) -> set[str]:
    """从一段源码里抠 KubernetesPodOperator 的 namespace= 参数。"""
    return set(re.findall(r'namespace\s*=\s*["\']([a-z0-9-]+)["\']', text))


def consumer_namespaces(path: Path, dns: str) -> set[str]:
    """这个文件里**真的引用了 dns** 的那部分内容,会跑在哪些命名空间。

    关键是**按分析单元拆开**,不要整个文件混着看:`dags-configmap.yaml` 把
    好几个 DAG 塞在同一个文件的不同 data key 里,如果整文件一起扫,只有
    A DAG 引用了共享服务,却会把 B/C DAG 的 namespace 也算成消费者——
    2026-08-21 第一版就是这么误报了 dbt 和 platform-sdk-demo 两个。
    """
    text = path.read_text(errors="ignore")
    found: set[str] = set()

    if path.suffix in {".yaml", ".yml"}:
        try:
            docs = list(yaml.safe_load_all(text))
        except yaml.YAMLError:
            docs = []
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            own_ns = (doc.get("metadata") or {}).get("namespace")
            if doc.get("kind") == "ConfigMap":
                # 每个 data key 单独判断,只有真的含 dns 的那一段才算
                for value in (doc.get("data") or {}).values():
                    if not isinstance(value, str) or dns not in value:
                        continue
                    found |= _ns_from_text(value) or ({own_ns} if own_ns else set())
                continue
            # 其它资源:整份 doc 作为一个单元
            if dns not in yaml.safe_dump(doc, allow_unicode=True):
                continue
            if doc.get("kind") == "Application":
                ns = doc.get("spec", {}).get("destination", {}).get("namespace")
                if ns:
                    found.add(ns)
            if own_ns:
                found.add(own_ns)
        return found

    # 独立的 .py 文件(比如 apps/airflow/dags/*.py):整个文件就是一个单元
    if dns in text:
        found |= _ns_from_text(text)
    return found


# 命名空间不在仓库 manifest 里、由脚本运行时创建的消费者。
# 格式:命名空间 -> (它要访问的服务, 由谁创建)
EXTRA_CONSUMERS = {
    "kserve-demo": ("minio.minio.svc.cluster.local",
                    "scripts/11-deploy-demo-inference-service.sh 运行时 kubectl 创建"),
    # openmetadata 这个命名空间本身在 git 里,但"它要读 MinIO"这条消费关系
    # 不在:dbt 血缘管道是 scripts/43 调 OpenMetadata API 建的,采集 Job 由
    # OpenMetadata 自己生成,仓库里没有任何一处 manifest 写着这件事。
    # 2026-08-29 实测撞到:采集 Job 报
    # `Failed to list objects in S3 bucket 'lakehouse': Could not connect to
    # the endpoint URL`,而这个检查器当时报的是"没有发现漏加白名单的消费者"。
    "openmetadata": ("minio.minio.svc.cluster.local",
                     "scripts/43-configure-openmetadata-dbt-ingestion.sh 建的 dbt 采集管道,"
                     "由 OpenMetadata 自己生成 CronJob 去读 s3://lakehouse/dbt-artifacts/"),
}


def main() -> None:
    problems = []
    for dns, (policy_rel, policy_name) in SERVICES.items():
        policy_path = REPO / policy_rel
        allowed = set()
        for doc in yaml.safe_load_all(policy_path.read_text()):
            if isinstance(doc, dict) and (doc.get("metadata") or {}).get("name") == policy_name:
                for rule in doc.get("spec", {}).get("ingress", []):
                    for src in rule.get("from", []):
                        ns = (src.get("namespaceSelector", {}).get("matchLabels", {})
                              .get("kubernetes.io/metadata.name"))
                        if ns:
                            allowed.add(ns)
        if not allowed:
            print(f"!! 没能从 {policy_rel} 解析出 {policy_name} 的白名单", file=sys.stderr)
            sys.exit(1)

        consumers = {}
        for path in REPO.rglob("*"):
            if not path.is_file() or path.suffix not in {".yaml", ".yml", ".py"}:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.resolve() == policy_path.resolve():
                continue
            if dns not in path.read_text(errors="ignore"):
                continue
            for ns in consumer_namespaces(path, dns):
                consumers.setdefault(ns, []).append(str(path.relative_to(REPO)))

        for ns, files in sorted(consumers.items()):
            if ns in allowed or ns in IGNORE:
                continue
            problems.append((dns, policy_rel, policy_name, ns, files))

        # 登记在册、但命名空间不在 git 里的消费者(见文件头部「已知盲区」)
        for ns, (service, who) in EXTRA_CONSUMERS.items():
            if service == dns and ns not in allowed:
                problems.append((dns, policy_rel, policy_name, ns,
                                 [f"(不在 git 里:{who})"]))

        extra_here = sum(1 for _, (svc, _) in EXTRA_CONSUMERS.items() if svc == dns)
        print(f"{dns}: 白名单 {len(allowed)} 个命名空间,扫到 {len(consumers)} 个引用方"
              + (f",另有 {extra_here} 个手工登记的(命名空间不在 git 里)" if extra_here else ""))

    if problems:
        print("\n!! 发现可能漏加白名单的消费者:", file=sys.stderr)
        for dns, policy_rel, policy_name, ns, files in problems:
            print(f"  - 命名空间 `{ns}` 引用了 {dns},但不在 {policy_rel} 的 "
                  f"{policy_name} 白名单里\n    引用它的文件:{', '.join(files[:3])}", file=sys.stderr)
        print("\n确认是误报的话,在脚本的 IGNORE 里加豁免并写清理由。", file=sys.stderr)
        sys.exit(1)
    print("\n没有发现漏加白名单的消费者。")


if __name__ == "__main__":
    main()
