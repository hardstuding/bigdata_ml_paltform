#!/usr/bin/env python3
"""
CI 用:扫描仓库里所有 ArgoCD Application(不管现在是 active 还是收在
pending-definitions/ 里,CI 要验证的是"这份配置本身对不对",不是"现在集群
里跑没跑"),对每一个 Helm chart 来源跑一次 `helm template`,渲染失败就算
CI 失败。

抓不到的问题:helm template 只验证"chart 语法 + 我们的 values 能不能正确
渲染成 YAML",抓不到"渲染出来的配置在真实集群里跑不跑得起来"这类问题
(这次踩的很多坑,比如 livenessProbe 打错端口、Secret key 名字对不上,
都是这类,helm template 阶段完全看不出来,只能靠真的部署到集群才发现)。
即便如此,能在 push 之前拦住"chart 版本写错""字段名写错""YAML 缩进错了"
这类问题,已经比现在这种"push 上去等 ArgoCD 报错才发现"要早一步。

用法:
    python3 scripts/validate-charts.py

退出码:0 = 全部渲染成功;1 = 至少一个 Application 渲染失败,详情打印到 stderr。
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# `helm template` 离线跑,默认不知道目标集群实际装了哪些 CRD——遇到
# `{{- if .Capabilities.APIVersions.Has "x" }}` 这种判断,离线渲染时永远
# 判 false,某些 chart(比如 spark-operator 的 PodMonitor 模板)对这种情况
# 是直接报错 fail,不是优雅跳过。真实集群里这些 CRD 是有的(比如
# monitoring.coreos.com/v1 是 kube-prometheus-stack 自己装的,这个仓库另一个
# Application 负责),只是"这个 chart 单独 helm template 时看不到别的
# Application 装了什么"——这里手动补一份"我们知道最终会在同一个集群里"的
# CRD 清单,让离线校验更贴近真实部署目标,不是瞎猜的容忍名单。
KNOWN_CLUSTER_API_VERSIONS = [
    "monitoring.coreos.com/v1",       # kube-prometheus-stack(Prometheus Operator CRD)
    "monitoring.coreos.com/v1alpha1",
    "serving.kserve.io/v1beta1",      # kserve-crd
    "serving.kserve.io/v1alpha1",
    # 有些 chart 的 {{- if .Capabilities.APIVersions.Has }} 判断写的是
    # "group/version/Kind" 这种带具体资源类型的完整格式,不是只写
    # "group/version"(spark-operator 的 PodMonitor 模板就是这样,实测确认
    # 过——只传 group/version 不够,helm template 照样报
    # "cluster does not support"),两种格式都得给,不能只给粗粒度那个。
    "monitoring.coreos.com/v1/PodMonitor",
    "monitoring.coreos.com/v1/ServiceMonitor",
]

_repo_added = set()


def ensure_helm_repo(repo_url: str) -> str:
    if repo_url.startswith("oci://"):
        return None
    name = "validate-" + re.sub(r"[^a-zA-Z0-9]+", "-", repo_url).strip("-")[-40:]
    if name not in _repo_added:
        subprocess.run(["helm", "repo", "add", name, repo_url], capture_output=True, check=False)
        _repo_added.add(name)
    return name


def validate_yaml_syntax(path: Path) -> tuple[bool, str]:
    try:
        list(yaml.safe_load_all(path.read_text()))
        return True, "SKIP (raw manifest, YAML 语法 OK)"
    except yaml.YAMLError as e:
        return False, f"YAML 解析失败: {e}"


def validate_app(path: Path) -> tuple[bool, str]:
    try:
        app = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return False, f"YAML 解析失败: {e}"

    if not app or app.get("kind") != "Application":
        return True, "SKIP (不是 ArgoCD Application)"

    source = app.get("spec", {}).get("source", {})
    chart = source.get("chart")
    if not chart:
        # 纯 git manifest 来源(不是 Helm chart),没有 chart 可 render,
        # 退而求其次把它引用的 manifests 目录下所有 YAML 都做语法检查。
        manifest_path = source.get("path")
        if manifest_path:
            target_dir = REPO_ROOT / manifest_path
            if target_dir.is_dir():
                for f in sorted(target_dir.rglob("*.yaml")):
                    ok, msg = validate_yaml_syntax(f)
                    if not ok:
                        return False, f"{f.relative_to(REPO_ROOT)}: {msg}"
        return True, "SKIP (纯 git manifest 来源,已检查 YAML 语法)"

    repo_url = source.get("repoURL", "")
    version = source.get("targetRevision", "")
    values_obj = source.get("helm", {}).get("valuesObject")

    values_file = None
    extra_args = []
    if values_obj:
        fd = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.safe_dump(values_obj, fd)
        fd.close()
        values_file = fd.name
        extra_args = ["-f", values_file]

    try:
        if repo_url.startswith("oci://"):
            chart_ref = repo_url
        else:
            repo_name = ensure_helm_repo(repo_url)
            if repo_name is None:
                return False, f"无法识别的 chart 来源: {repo_url}"
            chart_ref = f"{repo_name}/{chart}"

        cmd = ["helm", "template", "validate", chart_ref]
        if version:
            cmd += ["--version", version]
        for av in KNOWN_CLUSTER_API_VERSIONS:
            cmd += ["--api-versions", av]
        cmd += extra_args

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return False, result.stderr.strip()
        return True, "OK"
    finally:
        if values_file:
            Path(values_file).unlink(missing_ok=True)


def main():
    dirs = [
        REPO_ROOT / "platform" / "apps",
        REPO_ROOT / "apps" / "definitions",
        REPO_ROOT / "environments" / "cloud-full" / "pending-definitions",
    ]

    failures = []
    checked = 0
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yaml")):
            ok, msg = validate_app(f)
            checked += 1
            rel = f.relative_to(REPO_ROOT)
            if ok:
                print(f"{'OK' if msg == 'OK' else 'SKIP':5} {rel}  {'' if msg == 'OK' else msg}")
            else:
                print(f"FAIL  {rel}: {msg}", file=sys.stderr)
                failures.append((rel, msg))

    print(f"\n共检查 {checked} 个文件,{len(failures)} 个失败。")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
