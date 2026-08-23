#!/usr/bin/env python3
"""CI 检查:仓库里每个工作负载 manifest 的容器都得有 image 字段。

**为什么需要这条看起来很傻的检查**:2026-08-23 我自己用一段
`re.sub(pattern, r"\1" + sha, text)` 更新 FlinkDeployment 的镜像 tag,而
sha 以数字开头——Python 的替换模板把 `\1178c...` 里的 `\117` 当成**八进制
转义**(0o117 = 'O'),整行 `image: ghcr.io/...` 被替换成了一行孤零零的
`O8c9124a...`。

后果特别阴:
  - 文件仍然是**合法 YAML**,`yaml.safe_load` 不报错
  - `render-environment-config.py --check` 不报错(渲染产物和模板一致)
  - `check-image-tags.py` 也不报错(它检查"找到的镜像 tag 合不合规",
    镜像整个消失了它反而没话说)
  - 脚本里那句 `assert s2 != s` 也通过了——它只验证了"发生了替换",没有
    验证"替换成了什么"

真正会暴露的时机是**下一次部署,Pod 起不来**。

这条检查专门盯"镜像整个不见了"这一类:和 check-image-tags.py 是互补的,
那个管"tag 合不合规",这个管"到底有没有"。

用法:
    python3 scripts/check-workloads-have-image.py
"""
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
# **只扫渲染产物,不扫 templates/**:模板里有 `{{RES:xxx}}` 这类未加引号的
# 占位符,本来就不是合法 YAML。渲染产物覆盖了同样的内容,而且那才是真正
# 部署上去的东西。
SCAN_DIRS = [REPO / "apps", REPO / "platform"]
SKIP_PARTS = {".git", "__pycache__", "loki-chart", "alloy-chart", "kueue-chart",
              # apps/components/ 是**源文件**不是渲染产物,里面有 {{RES:xxx}}
              # 这类未加引号的占位符,本来就不是合法 YAML。对应的渲染产物
              # 是 apps/definitions/,那里才是真正部署上去的。
              "components"}

# kind -> 取到容器列表的路径。只列这个仓库真实用到的。
POD_SPEC_PATHS = {
    "Deployment": ["spec", "template", "spec"],
    "StatefulSet": ["spec", "template", "spec"],
    "DaemonSet": ["spec", "template", "spec"],
    "Job": ["spec", "template", "spec"],
    "CronJob": ["spec", "jobTemplate", "spec", "template", "spec"],
}


def dig(obj, path):
    for k in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def check(doc, where, problems):
    kind = doc.get("kind")
    if kind in POD_SPEC_PATHS:
        spec = dig(doc, POD_SPEC_PATHS[kind])
        if not isinstance(spec, dict):
            return
        for field in ("containers", "initContainers"):
            for c in spec.get(field) or []:
                if not c.get("image"):
                    problems.append(f"{where}: {kind}/{doc.get('metadata',{}).get('name','?')} "
                                    f"的 {field} 里 `{c.get('name','?')}` 没有 image")
    elif kind == "FlinkDeployment":
        # Flink operator 的 CRD:镜像在 spec.image,不是标准 pod spec。
        # **这条就是当初出事的那个 kind**,单独处理。
        if not dig(doc, ["spec", "image"]):
            problems.append(f"{where}: FlinkDeployment/"
                            f"{doc.get('metadata',{}).get('name','?')} 没有 spec.image")


def main() -> int:
    problems, checked = [], 0
    for root in SCAN_DIRS:
        if not root.exists():
            continue
        for f in sorted(root.rglob("*.yaml")):
            if SKIP_PARTS & set(f.parts):
                continue
            try:
                docs = list(yaml.safe_load_all(f.read_text()))
            except yaml.YAMLError as exc:
                # **解析失败必须报错,不能 continue。** 第一版就是 continue,
                # 结果自测时故意把 image 那行改坏之后,文件变成非法 YAML、
                # 被整个跳过,检查反而"通过"了——这个检查差点就以一个抓不到
                # 目标场景的形态上线。这已经是这一天里第四次"验证脚本自己
                # 错了"。
                problems.append(f"{f.relative_to(REPO)}: YAML 解析失败,"
                                f"多半是被改坏了 —— {str(exc).splitlines()[0][:120]}")
                continue
            for d in docs:
                if isinstance(d, dict):
                    checked += 1
                    check(d, f.relative_to(REPO), problems)

    if problems:
        print(f"!! {len(problems)} 个工作负载没有镜像:", file=sys.stderr)
        for p in problems:
            print("   " + p, file=sys.stderr)
        return 1
    print(f"扫了 {checked} 个对象,所有工作负载都有 image。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
