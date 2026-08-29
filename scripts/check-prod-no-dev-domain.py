#!/usr/bin/env python3
"""渲染 prod 之后,部署产物里不允许出现开发环境的域名。

**为什么需要**:2026-08-29 发现门户里 16 处硬编码 `local-lite.test`,
后果是 prod 部署之后门户上每一个链接都指向不存在的域名 —— 而这**不会有
任何东西报错**:ArgoCD 是绿的、Pod 是健康的、页面也打得开,只有点链接的
那个人会发现全是死链。

这个检查渲染一遍 prod,然后在产物里搜开发域名。它必须**在渲染 prod 的
状态下跑**,跑完把工作区还原回原来那一档 —— 否则会把工作区留在 prod,
下一步操作就基于错的环境了。

跑法:python3 scripts/check-prod-no-dev-domain.py
"""
import subprocess
import sys

import yaml
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RENDER = [sys.executable, str(REPO / "scripts" / "render-environment-config.py")]
# 开发环境专属、绝不该出现在 prod 产物里的东西
FORBIDDEN = ["local-lite.test", ":32460", ":32535"]
# 只看会被部署的目录。templates/ 里出现是应该的(那是源,带占位符);
# docs/ 里出现也是应该的(在讲 local-lite 这一档)。
SCAN_DIRS = ["apps/definitions", "apps/platform-portal/manifests", "platform/apps",
             "platform/bootstrap", "apps/postgres/manifests"]


def current_env() -> str:
    """工作区现在渲染的是哪一档 —— 跑完要还原回去。"""
    for env in ("cloud-full", "local-lite", "prod"):
        r = subprocess.run(RENDER + [env, "--check"], capture_output=True)
        if r.returncode == 0:
            return env
    return "cloud-full"


def _walk(node, path=""):
    """把 YAML 里所有字符串值连同它的路径吐出来。"""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def scan() -> list[str]:
    """**只看 YAML 的值,不看注释。**

    第一版直接对整个文件做字符串搜索,报了 12 处 —— 全是注释里在解释
    "local-lite 这一档是 NodePort 32460"之类的说明。那种误报会让人很快
    学会忽略这个检查,比没有检查更糟。
    """
    hits = []
    for d in SCAN_DIRS:
        root = REPO / d
        if not root.exists():
            continue
        for f in root.rglob("*.yaml"):
            try:
                docs = list(yaml.safe_load_all(f.read_text(errors="ignore")))
            except yaml.YAMLError:
                continue
            for doc in docs:
                for path, value in _walk(doc):
                    # **多行字符串值里可能嵌着注释**(helm 的 extraEnv/configFile
                    # 这类整块塞进去的 YAML/配置片段)。那些注释在解释
                    # "local-lite 这一档是 NodePort 32460",不是真实配置值。
                    # 第一版没排除,keycloak 就因为块内注释被误报。
                    lines = [l for l in value.splitlines() if not l.strip().startswith("#")]
                    body = "\n".join(lines)
                    for bad in FORBIDDEN:
                        if bad in body:
                            hits.append(f"{f.relative_to(REPO)}{path} = {body[:70]!r}(含 {bad!r})")
    return hits


def main() -> None:
    original = current_env()
    subprocess.run(RENDER + ["prod"], capture_output=True, check=True)
    try:
        hits = scan()
    finally:
        # **必须还原**,否则工作区被留在 prod,后面所有操作都基于错的环境
        subprocess.run(RENDER + [original], capture_output=True, check=True)

    print(f"渲染 prod 后扫了 {len(SCAN_DIRS)} 个部署目录(工作区已还原成 {original})。")
    if hits:
        print("\nprod 产物里出现了开发环境专属的东西:")
        for h in sorted(set(hits)):
            print("  -", h)
        print("\n这类问题不会有任何东西报错——ArgoCD 绿的、Pod 健康的、页面也打得开,"
              "\n只有点链接的那个人会发现全是死链。")
        sys.exit(1)
    print("没有发现开发环境域名/端口泄漏进 prod 产物。")


if __name__ == "__main__":
    main()
