#!/usr/bin/env python3
"""代码里连了某个集群内服务,NetworkPolicy 里就得有对应的放行。

**这个检查是从一类反复发作的 bug 里长出来的。** 症状高度一致:加了一个新
的调用方(新命名空间连 MinIO、notebook 连 OpenBao、探针连审计表……),忘了
回来加白名单,于是请求**卡住直到超时** —— 不是 DNS 失败、不是明确拒绝,
所以人第一反应总是去查对端服务、查凭据、查 RBAC,很久才想到网络策略。

已知发作记录:
  ADR-035 试点时           新命名空间连 MinIO
  2026-08-19               notebook 连 Trino/MLflow(ConnectionRefused)
  2026-08-30 iceberg-backup  connection refused,查了半天是新建 pod 的时序
  2026-09-01 自查           notebook 连 OpenBao(还没上集群就抓到)

**它只查一件事**:代码/清单里出现了 `<svc>.<ns>.svc.cluster.local` 这样的
集群内地址,而那个命名空间在受管的 NetworkPolicy 里既不是放行来源、也没有
对应的出向规则 —— 就报出来让人确认。

**它必然有误报**(比如那个调用方所在的命名空间根本没有 egress 限制),所以
用一份显式的豁免清单,而不是让检查变松。加豁免时要写理由。
"""
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

# 有出向限制的调用方:命名空间 -> 它的 egress 白名单从哪读
#
# 目前只有 jupyterhub 的 singleuser 是"默认全挡、逐个放行"的
# (z2jh 的 egressAllowRules.privateIPs=false)。别的命名空间的
# NetworkPolicy 都只写了 Ingress,不限制出向。
EGRESS_LIMITED = {
    "jupyterhub": ("apps/definitions/jupyterhub.yaml",
                   ["spec", "source", "helm", "valuesObject",
                    "singleuser", "networkPolicy", "egress"]),
}

# 这些命名空间的服务不需要出现在 egress 白名单里,理由写在这里。
EXEMPT = {
    "kube-system": "CoreDNS,z2jh 的策略默认就放行 DNS",
    "default": "K8s API 的 Service 在这里,但它的 Endpoints 指向节点 IP,"
               "只能用 ipBlock 放行(见 jupyterhub.yaml 里那段考古)",
    "keycloak": "notebook 不直接连 Keycloak,身份是 spawn 时注入的",
}

ADDR = re.compile(r"([a-z0-9-]+)\.([a-z0-9-]+)\.svc\.cluster\.local")


def egress_namespaces(rel, path):
    d = yaml.safe_load((REPO / rel).read_text(encoding="utf-8"))
    for k in path:
        d = d[k]
    out = set()
    for rule in d or []:
        for to in rule.get("to") or []:
            ns = (to.get("namespaceSelector") or {}).get("matchLabels", {}) \
                 .get("kubernetes.io/metadata.name")
            if ns:
                out.add(ns)
    return out


def main() -> int:
    problems = []
    for caller_ns, (rel, path) in EGRESS_LIMITED.items():
        allowed = egress_namespaces(rel, path)

        # 这个调用方会跑哪些代码?notebook 跑的是 platform-sdk 和用户脚本,
        # 所以扫 SDK 的源码。
        referenced = {}
        for f in (REPO / "platform-sdk" / "platform_sdk").rglob("*.py"):
            for svc, ns in ADDR.findall(f.read_text(encoding="utf-8")):
                referenced.setdefault(ns, set()).add(f"{svc} ({f.name})")

        for ns, where in sorted(referenced.items()):
            if ns in allowed or ns in EXEMPT or ns == caller_ns:
                continue
            problems.append(
                f"{caller_ns} 的 egress 白名单里没有 {ns}\n"
                f"      platform-sdk 里连它:{', '.join(sorted(where))}\n"
                f"      -> 症状会是「卡住直到超时」,不是明确报错。"
                f"去 apps/components/jupyterhub.yaml 的 "
                f"singleuser.networkPolicy.egress 加一条")

    if problems:
        print("NetworkPolicy 白名单漏了调用方:\n", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("\n  确认是误报的话,在 scripts/check-netpol-covers-callers.py 的 "
              "EXEMPT 里加一条**并写明理由**。", file=sys.stderr)
        return 1

    n = sum(len(egress_namespaces(r, p)) for r, p in EGRESS_LIMITED.values())
    print(f"有出向限制的命名空间 {len(EGRESS_LIMITED)} 个,"
          f"白名单共 {n} 条,platform-sdk 里连的服务都被覆盖。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
