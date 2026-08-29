#!/usr/bin/env python3
"""把仓库里自建镜像的地址在 GHCR / 阿里云 ACR 之间切换。

**为什么需要**:CI 把同一份镜像同时推到 GHCR 和 ACR,但**清单里只能写一个
地址**。境内集群该拉 ACR(GHCR 的大镜像拉不动,见
docs/project/roadmap.md「镜像拉取」),而开源使用者、以及境外环境该拉 GHCR。

只动**自建镜像**(`<registry>/<namespace>/<image>` 里 image 属于 CI 构建
清单的那些),第三方镜像(apache/spark、trino 等)一律不碰——它们不在我们的
registry 里,改了就是指向一个不存在的地方。

用法:
  ACR_REGISTRY=crpi-xxx.cn-hangzhou.personal.cr.aliyuncs.com \\
  ACR_NAMESPACE=bigdata-platform \\
    python3 scripts/switch-image-registry.py --to acr
  python3 scripts/switch-image-registry.py --to ghcr
  python3 scripts/switch-image-registry.py --show      # 只看现在指向哪
"""
import os
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
GHCR_PREFIX = "ghcr.io/hardstuding/bigdata_ml_paltform"
WORKFLOW = REPO / ".github" / "workflows" / "build-images.yml"
SKIP_DIRS = {".git", "logs", "image-cache", "image-cache-amd64", "node_modules"}


def built_images() -> set[str]:
    """CI 实际构建的镜像名单——从 workflow 的 matrix 读,不是手写一份。

    手写一份的下场是"加了新镜像但这里忘了加",而症状是切换之后那一个镜像
    的地址还指着旧 registry,集群上表现成单个组件 ImagePullBackOff。
    """
    wf = yaml.safe_load(WORKFLOW.read_text())
    inc = wf["jobs"]["build-and-push"]["strategy"]["matrix"]["include"]
    return {i["image"] for i in inc}


def targets() -> list[Path]:
    out = []
    for p in REPO.rglob("*"):
        if p.is_file() and p.suffix in {".yaml", ".yml", ".py", ".sh"} \
                and not any(s in p.parts for s in SKIP_DIRS):
            out.append(p)
    return out


def main() -> None:
    images = built_images()
    if "--show" in sys.argv:
        hits = {}
        for p in targets():
            for m in re.finditer(r"([\w.\-]+(?:\.[\w.\-]+)+/[\w.\-]+(?:/[\w.\-]+)?)/(" +
                                 "|".join(map(re.escape, images)) + r")[:@]", p.read_text(errors="ignore")):
                hits.setdefault(m.group(1), 0)
                hits[m.group(1)] += 1
        print("自建镜像当前指向:")
        for k, v in sorted(hits.items()):
            print(f"  {k}  ({v} 处)")
        return

    if "--to" not in sys.argv:
        print(__doc__)
        sys.exit(1)
    to = sys.argv[sys.argv.index("--to") + 1]

    if to == "acr":
        reg = os.environ.get("ACR_REGISTRY")
        ns = os.environ.get("ACR_NAMESPACE")
        if not reg or not ns:
            print("!! 切到 acr 需要 ACR_REGISTRY 和 ACR_NAMESPACE 两个环境变量")
            sys.exit(1)
        old, new = GHCR_PREFIX, f"{reg}/{ns}"
    elif to == "ghcr":
        reg = os.environ.get("ACR_REGISTRY")
        ns = os.environ.get("ACR_NAMESPACE")
        if not reg or not ns:
            print("!! 切回 ghcr 也需要知道当前的 ACR_REGISTRY/ACR_NAMESPACE 才能替换")
            sys.exit(1)
        old, new = f"{reg}/{ns}", GHCR_PREFIX
    else:
        print(f"!! --to 只能是 acr 或 ghcr,不是 {to}")
        sys.exit(1)

    changed = 0
    for p in targets():
        try:
            t = p.read_text()
        except Exception:  # noqa: BLE001
            continue
        if old not in t:
            continue
        # 只替换后面紧跟着已知镜像名的那些,避免误伤文档里讲 registry 本身的句子
        new_t = re.sub(rf"{re.escape(old)}/({'|'.join(map(re.escape, images))})",
                       rf"{new}/\1", t)
        if new_t != t:
            p.write_text(new_t)
            changed += 1

    print(f"已把 {changed} 个文件里的自建镜像地址切到 {new}")
    print("接下来:python3 scripts/render-environment-config.py <env> 重新渲染,然后提交推送。")


if __name__ == "__main__":
    main()
