#!/usr/bin/env python3
"""扫出"必须由人提供、脚本生不出来"的凭据。**不需要连集群。**

**为什么要离线版**:`check-manual-credentials.sh` 要连上集群才能回答,而
这个问题恰恰是在**还没有集群**的时候最需要问的 —— 别人拿到这个仓库,第一
件事是"我得先准备什么"。要先把集群建起来才能知道要准备什么,顺序是反的。

做法:扫所有 manifest 里 `secretKeyRef`/`secretRef` 引用的 Secret 名,减去
`scripts/` 下任何脚本会创建的,剩下的就是必须人工提供的。

用法:
  python3 scripts/list-manual-credentials.py            # 列出来
  python3 scripts/list-manual-credentials.py --check    # CI:发现没登记的就红
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 已知需要人工提供的凭据。**每条都要写清楚:干什么用的、不给会怎样、怎么给。**
# 这个清单是给人看的,不是给脚本对账用的 —— 所以宁可啰嗦。
KNOWN = {
    "permission-request-app-git": {
        "用途": "权限门户批准之后,把授权行写进 git(platform/iam/table-access-grants.csv)",
        "不给会怎样": "审批本身照常走完,但状态会停在 `approved_pending_apply` —— "
                     "**授权不会真正生效**(OPA 读的是 git 里那份)。页面上会说清楚。",
        "怎么给": "GitHub fine-grained PAT,只要这个仓库的 Contents:read/write。"
                 "`kubectl -n permission-request-app create secret generic "
                 "permission-request-app-git --from-literal=GIT_TOKEN=<token>`",
        "必需吗": "**是**(想让权限审批真正生效的话)",
    },
    "permission-request-app-notify": {
        "用途": "审批/超时/到期提醒推送到企业微信群机器人",
        "不给会怎样": "不推送,其余功能不受影响(代码里是静默跳过)",
        "怎么给": "企微群机器人 webhook 地址。`kubectl -n permission-request-app "
                 "create secret generic permission-request-app-notify "
                 "--from-literal=WECOM_WEBHOOK_URL=<url>`",
        "必需吗": "否 —— 按 zhenghe 的安排,真实告警渠道等上测试/生产环境再接",
    },
}

# 这些不是 k8s Secret,但同样"必须人给",列在这里免得漏。
NON_K8S = {
    "阿里云 ACR 的 4 个 GitHub 仓库 secret": {
        "用途": "CI 把自建镜像推到 ACR;境内集群从 ACR 内网拉(GHCR 在境内拉大镜像会卡死)",
        "不给会怎样": "CI 只推 GHCR,不报错;但**境内集群拉自建镜像会超时**"
                     "(实测 3.44GB 的镜像 GHCR 25 秒 0 字节,ACR 1 分 59 秒拉完)",
        "怎么给": "在 GitHub 仓库 Settings → Secrets 里设 ACR_REGISTRY / ACR_NAMESPACE / "
                 "ACR_USERNAME / ACR_PASSWORD。**由仓库所有者自己设,任何 AI/协作者都不该经手这些值。**"
                 "详见 docs/operations/image-registry.md",
        "必需吗": "**境内部署是**;镜像都放公开仓库、或者用本地缓存的话不需要",
    },
    "云主机 / Kubernetes 集群本身": {
        "用途": "跑这一整套东西",
        "不给会怎样": "无从开始",
        "怎么给": "已有集群直接用;从裸 ECS 起见 scripts/21-bootstrap-cloud-vm.sh",
        "必需吗": "**是**",
    },
}


def referenced_secrets() -> dict:
    refs = defaultdict(set)
    pats = [re.compile(r"secretKeyRef:\s*\n\s*name:\s*([a-zA-Z0-9._-]+)"),
            re.compile(r"secretRef:\s*\n\s*name:\s*([a-zA-Z0-9._-]+)")]
    for f in list(REPO.glob("apps/**/*.yaml")) + list(REPO.glob("platform/**/*.yaml")):
        if "chart" in str(f):
            continue
        try:
            txt = f.read_text()
        except Exception:
            continue
        for pat in pats:
            for m in pat.finditer(txt):
                refs[m.group(1)].add(str(f.relative_to(REPO)))
    return refs


def secrets_created_by_scripts() -> set:
    """任何脚本里提到某个 Secret 名,就认为它会被创建/填充。

    **刻意放宽**:这个函数宁可漏报(把人工凭据当成脚本会建的),也不要误报
    ——误报的后果是让人去准备一个其实不用准备的东西,那会削弱整份清单的
    可信度。真正的清单是上面 KNOWN,这里只用来发现"新出现的、谁都不管的"。
    """
    names = set()
    for f in list(REPO.glob("scripts/*.sh")) + list(REPO.glob("scripts/*.py")):
        try:
            txt = f.read_text()
        except Exception:
            continue
        names |= set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+){1,5}", txt))
    return names


def main() -> int:
    check = "--check" in sys.argv
    refs = referenced_secrets()
    created = secrets_created_by_scripts()
    orphans = sorted(n for n in refs if n not in created and n not in KNOWN)

    if not check:
        print("=== 必须由人提供的凭据(脚本生不出来) ===\n")
        for name, info in list(KNOWN.items()) + list(NON_K8S.items()):
            print(f"■ {name}")
            for k in ("必需吗", "用途", "不给会怎样", "怎么给"):
                print(f"    {k}:{info[k]}")
            print()

    if orphans:
        print(f"!! {len(orphans)} 个 Secret 被 manifest 引用,但既没有脚本创建它、"
              f"也没登记在这份清单里:", file=sys.stderr)
        for n in orphans:
            print(f"   {n} —— 引用它的:{sorted(refs[n])[0]}", file=sys.stderr)
        print("\n   要么补一个创建它的脚本,要么把它加进 "
              "scripts/list-manual-credentials.py 的 KNOWN 里(带上"
              "用途/不给会怎样/怎么给)。**一个谁都不管的 Secret,表现是某个 Pod "
              "起不来,而报错不会告诉你是缺凭据。**", file=sys.stderr)
        return 1

    if check:
        print(f"凭据清单完整:{len(refs)} 个被引用的 Secret 都有着落。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
