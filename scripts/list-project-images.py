#!/usr/bin/env python3
"""
扫描 apps/definitions/、environments/cloud-full/pending-definitions/、
platform/apps/ 下所有 ArgoCD Application,把每一个实际会用到的容器镜像
(chart 自己拉的 + 我们 valuesObject 里显式配的)列出来。

用 Python 不用 bash,是因为要解析 YAML + 跑 helm template + 正则提取
image 字段,这几件事用 bash 写会比这个脚本本身还长、还难读——这是本项目
少数几个不是纯 bash 脚本的例外,原因就是这个。

用法:
    python3 scripts/list-project-images.py [--include-pending]

默认只扫 apps/definitions/(当前实际在跑的)+ platform/apps/(平台底座,
一直在跑)。加 --include-pending 把 environments/cloud-full/pending-definitions/
里收着的组件也扫进去(这些之前都验证过,配置是对的,只是本地为了省内存
先收起来)——想要完整的"这个项目最终会用到哪些镜像"清单时用这个。

输出:每行一个镜像引用(image:tag 或 image@digest),排序去重,写到 stdout。
不直接改动集群或本地 docker 状态,只读 git 里的配置文件 + 调 helm template
(网络访问 chart repo,不落地任何东西)。
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
# 引号有的渲染成双引号,有的是单引号,不能只处理一种(踩过一次:漏了单引号
# 处理,把引号本身当成镜像名的一部分,输出里出现 'xxx' 这种带引号的脏数据)。
IMAGE_RE = re.compile(r'^\s*-?\s*image:\s*[\'"]?([^\'"\s]+)[\'"]?\s*$')

# 明确知道跟这个项目无关的镜像(这台 Mac 的 colima 上跑过其他工具留下的缓存),
# 不是本项目任何 Application 引用的,helm template 扫描本来就不会扫到它们,
# 这里列出来只是给 export 脚本比对缓存时提个醒,不是这个脚本自己要用。
KNOWN_UNRELATED = {"eipwork/etcd-host", "eipwork/kuboard"}

_repo_added = set()


def ensure_helm_repo(repo_url: str) -> str:
    """helm repo add 一个 https:// 源,返回本地仓库名;oci:// 源不需要 add,原样返回 None。"""
    if repo_url.startswith("oci://"):
        return None
    name = "scan-" + re.sub(r"[^a-zA-Z0-9]+", "-", repo_url).strip("-")[-40:]
    if name not in _repo_added:
        subprocess.run(
            ["helm", "repo", "add", name, repo_url],
            capture_output=True, check=False,
        )
        _repo_added.add(name)
    return name


def helm_template_images(app: dict) -> set[str]:
    source = app["spec"]["source"]
    repo_url = source.get("repoURL", "")
    chart = source.get("chart")
    version = source.get("targetRevision", "")
    values_obj = source.get("helm", {}).get("valuesObject")

    if not chart:
        return set()  # 不是 helm chart 源(比如纯 git 目录里的裸 manifest),下面单独处理

    values_file = None
    extra_args = []
    if values_obj:
        # 系统临时目录,不放仓库根目录——之前放过 REPO_ROOT,中途一旦被打断
        # (比如管道另一头提前退出导致 SIGPIPE)finally 里的 unlink 就跟着没
        # 机会执行,留下 tmpXXXXXX.yaml 脏文件在仓库根目录里,踩过一次。
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
            chart_ref = f"{repo_name}/{chart}"

        cmd = ["helm", "template", "scan", chart_ref]
        if version:
            cmd += ["--version", version]
        cmd += extra_args

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(
                f"  !! helm template 失败({chart_ref} @ {version}):"
                f" {result.stderr.strip().splitlines()[-1] if result.stderr else '未知错误'}",
                file=sys.stderr,
            )
            return set()

        images = set()
        for line in result.stdout.splitlines():
            m = IMAGE_RE.match(line)
            if m:
                images.add(m.group(1))
        return images
    finally:
        if values_file:
            Path(values_file).unlink(missing_ok=True)


def raw_manifest_images(app: dict) -> set[str]:
    """没有 chart 字段的 Application,是纯 git 目录里的裸 manifest,直接读文件找 image:。"""
    path = app["spec"]["source"].get("path")
    if not path:
        return set()
    target_dir = REPO_ROOT / path
    if not target_dir.is_dir():
        return set()
    images = set()
    for f in target_dir.rglob("*.yaml"):
        for line in f.read_text().splitlines():
            m = IMAGE_RE.match(line)
            if m:
                images.add(m.group(1))
    return images


def scan_dir(d: Path) -> set[str]:
    images = set()
    if not d.is_dir():
        return images
    for f in sorted(d.glob("*.yaml")):
        try:
            app = yaml.safe_load(f.read_text())
        except yaml.YAMLError:
            continue
        if not app or app.get("kind") != "Application":
            continue
        name = app.get("metadata", {}).get("name", f.stem)
        print(f"==> {name} ({f.relative_to(REPO_ROOT)})", file=sys.stderr)
        found = helm_template_images(app) or raw_manifest_images(app)
        if not found:
            print("  (没找到 image,可能是纯 CRD/Job 之类的资源,或者 helm template 失败)", file=sys.stderr)
        images |= found
    return images


def argocd_bootstrap_images() -> set[str]:
    """ArgoCD 自己是手动 helm install 的(见 ADR-005),不在 platform/apps/ 或
    apps/definitions/ 里,单独扫 platform/bootstrap/argocd-values.yaml。
    注意 01-bootstrap-argocd.sh 没有显式 pin chart 版本(用的是当时的 latest),
    这里同样不 pin,拿到的是当前 helm repo 里 argo-cd 的最新版镜像列表,和
    集群里实际跑的版本可能有细微出入——真要 100% 准确以 `docker images` 里
    实际跑着的 tag 为准。
    """
    fake_app = {
        "spec": {
            "source": {
                "repoURL": "https://argoproj.github.io/argo-helm",
                "chart": "argo-cd",
                "targetRevision": "",
                "helm": {
                    "valuesObject": yaml.safe_load(
                        (REPO_ROOT / "platform/bootstrap/argocd-values.yaml").read_text()
                    )
                },
            }
        }
    }
    print("==> argocd(手动 bootstrap,见 ADR-005)", file=sys.stderr)
    return helm_template_images(fake_app)


def kserve_serving_runtime_images() -> set[str]:
    """KServe 的 ClusterServingRuntime(sklearn/xgboost/mlserver 等)不来自任何
    ArgoCD Application——kserve-resources chart v0.19.0 起不再打包这些,是
    scripts/10-install-kserve-serving-runtimes.sh 直接 `kubectl apply -k`
    官方 GitHub 仓库装的(见 ADR-027),完全在这个脚本正常扫描的"Application
    -> helm template"路径之外,踩过一次"以为镜像清单扫全了,其实漏了整个
    mlserver/sklearnserver 这一类"的坑,才补上这个特例,和上面 ArgoCD 自己
    是同一个道理。

    版本号不在这里硬编码第二份,直接读 apps/definitions/kserve-resources.yaml
    的 targetRevision,避免两处版本号将来改一个忘了改另一个又对不上。
    """
    kserve_app_file = REPO_ROOT / "apps" / "definitions" / "kserve-resources.yaml"
    if not kserve_app_file.exists():
        return set()
    app = yaml.safe_load(kserve_app_file.read_text())
    version = app["spec"]["source"]["targetRevision"]

    print(f"==> kserve ClusterServingRuntime(scripts/10,版本跟 kserve-resources 对齐:{version})", file=sys.stderr)
    result = subprocess.run(
        ["kubectl", "kustomize", f"https://github.com/kserve/kserve/config/runtimes?ref={version}"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"  !! kubectl kustomize 失败: {result.stderr.strip().splitlines()[-1] if result.stderr else '未知错误'}", file=sys.stderr)
        return set()

    images = set()
    for line in result.stdout.splitlines():
        m = IMAGE_RE.match(line)
        if m:
            images.add(m.group(1))
    return images


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-pending", action="store_true")
    args = parser.parse_args()

    dirs = [REPO_ROOT / "platform" / "apps", REPO_ROOT / "apps" / "definitions"]
    if args.include_pending:
        dirs.append(REPO_ROOT / "environments" / "cloud-full" / "pending-definitions")

    all_images = set()
    for d in dirs:
        all_images |= scan_dir(d)
    all_images |= argocd_bootstrap_images()
    all_images |= kserve_serving_runtime_images()

    for img in sorted(all_images):
        print(img)


if __name__ == "__main__":
    main()
