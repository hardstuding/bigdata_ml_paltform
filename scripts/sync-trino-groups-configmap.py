#!/usr/bin/env python3
"""从 platform/iam/memberships.csv 生成 Trino 的 file group provider 配置。

**为什么需要这个**(2026-08-26 实测发现):Trino 上**没有配任何 group
provider**,所以它传给 OPA 的 `input.context.identity.groups` **永远是空的**。
后果是 `apps/opa/policy/trino.rego` 里那条

    is_platform_admin if { "platform-team" in input.context.identity.groups }

**从来没有真正生效过** —— "平台管理组不受表级授权约束、方便排障"这个口子
一直是个摆设。而 [ADR-074](../docs/decisions/074-superset-impersonation.md)
打开 Superset impersonation 之后,这条更成了硬伤:Trino 看到的是真实的人,
如果它不知道这个人属于哪个组,**platform-team 的人也会被当成普通用户拦下**
——正好和平台负责人要求的"admin 应该有全权限"相反。

数据源复用 `platform/iam/memberships.csv`,不新建一套组织结构——这个仓库在
权限、审批、Keycloak 同步、Kueue 队列上用的都是这一份(ADR-064 里解释过
为什么不能各搞各的)。

用法:
  python3 scripts/sync-trino-groups-configmap.py           # 生成/更新
  python3 scripts/sync-trino-groups-configmap.py --check   # CI 用,漂移就非零退出
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CSV = REPO / "platform" / "iam" / "memberships.csv"
OUT = REPO / "apps" / "trino-groups" / "manifests" / "configmap.yaml"

# Trino 的 file group provider 格式:每行 `组名:用户1,用户2`
# (https://trino.io/docs/current/security/group-file.html)
# Trino 的 file group provider 格式:每行 `组名:用户1,用户2`
# (https://trino.io/docs/current/security/group-file.html)
#
# **这个 ConfigMap 只放 group.txt,不放 group-provider.properties。**
# 2026-08-26 实测踩到:后者必须待在 Trino 的配置目录 /etc/trino 里,而
# /etc/trino **本身就是 chart 挂上去的一个 ConfigMap 卷**——往一个 ConfigMap
# 卷里再 subPath 挂一个文件,kubelet 直接失败:
#   error mounting ... to rootfs at "/etc/trino/group.txt":
#   not a directory: Are you trying to mount a directory onto a file
# coordinator 起不来(好在滚动更新时老 Pod 还在跑,Trino 没断)。
#
# 改成两边分开:
#   group-provider.properties -> chart 的 coordinator.additionalConfigFiles
#                                (chart 会把它塞进它自己那个配置 ConfigMap)
#   group.txt(这份)          -> 单独挂到 /etc/trino-groups/ 这个独立目录

HEADER = """# **这个文件是生成的**,源头是 platform/iam/memberships.csv。
# 改组成员改那份 CSV,然后跑
#   python3 scripts/sync-trino-groups-configmap.py
# CI 会用 --check 拦住漂移(和 sync-airflow-dags-configmap.py 同一个模式)。
#
# 它解决的问题见那个脚本的文档字符串:没有 group provider 的话,Trino 传给
# OPA 的 groups 永远是空的,`is_platform_admin` 那条规则形同虚设。
apiVersion: v1
kind: ConfigMap
metadata:
  name: trino-groups
  namespace: trino
data:
  group.txt: |
"""

def build() -> str:
    groups = defaultdict(list)
    with open(CSV, newline="") as f:
        for row in csv.DictReader(f):
            u, g = row["username"].strip(), row["group"].strip()
            if u and g:
                groups[g].append(u)
    lines = [f"    {g}:{','.join(sorted(set(users)))}" for g, users in sorted(groups.items())]
    return HEADER + "\n".join(lines) + "\n"


def main() -> int:
    want = build()
    check = "--check" in sys.argv
    if OUT.exists() and OUT.read_text() == want:
        print(f"一致  {OUT.relative_to(REPO)}")
        return 0
    if check:
        print(f"!! {OUT.relative_to(REPO)} 和 {CSV.relative_to(REPO)} 不一致,"
              "跑 `python3 scripts/sync-trino-groups-configmap.py` 重新生成。", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(want)
    print(f"已生成 {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
