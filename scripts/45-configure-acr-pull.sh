#!/usr/bin/env bash
# 让集群能从阿里云 ACR 拉自建镜像。
#
# **为什么需要**:境内云主机拉 GHCR 的大镜像会卡死(3.44GB 的 spark-iceberg
# 实测 `docker pull` 25 秒 0 字节,见 docs/project/roadmap.md「镜像拉取」)。
# CI 已经把镜像同时推到 GHCR 和 ACR,这个脚本配好集群侧的拉取凭据,并把
# 清单里的镜像地址切到 ACR。
#
# **凭据从哪来**:这个脚本**不接受命令行传密码**,只从环境变量读,而且不落
# 任何文件、不进日志。密码是 ACR 控制台「访问凭证」里设的固定密码。
#
# **zsh(macOS 默认 shell)**:
#   read -s "ACR_PASSWORD?ACR password: " && export ACR_PASSWORD && echo
#
# **bash**:
#   read -rsp "ACR password: " ACR_PASSWORD && export ACR_PASSWORD && echo
#
# 然后:
#   export ACR_REGISTRY=crpi-xxxx.cn-hangzhou.personal.cr.aliyuncs.com
#   export ACR_USERNAME=<你的阿里云账号名>
#   export ACR_NAMESPACE=<命名空间>
#   ./scripts/45-configure-acr-pull.sh
#   unset ACR_PASSWORD
#
# 两种写法都不回显、也不进 shell 历史。**两边语法不通用**:zsh 的 `read -p`
# 是"从协程读",在 zsh 里写 bash 那套会报 `read: -p: no coprocess`
# ——2026-08-29 实测踩到,提示语要用英文,中文提示在某些终端里会乱码。
#
# **幂等**:Secret 已存在就更新(不是跳过——密码可能轮换过)。
set -euo pipefail
cd "$(dirname "$0")/.."

: "${ACR_REGISTRY:?必须设置 ACR_REGISTRY(例:crpi-xxx.cn-hangzhou.personal.cr.aliyuncs.com)}"
: "${ACR_USERNAME:?必须设置 ACR_USERNAME}"
: "${ACR_PASSWORD:?必须设置 ACR_PASSWORD(用 read -s 读,不要写进命令行)}"
: "${ACR_NAMESPACE:?必须设置 ACR_NAMESPACE}"

SECRET_NAME="acr-pull"

# 哪些命名空间会拉自建镜像。**从仓库自己算出来,不是手写一份。**
#
# 第一版是手写的 13 个,当场就漏了 `superset` —— 表现是 superset 一个组件
# `Init:ImagePullBackOff`,而其它全好。这正是这个仓库反复栽的那类:
# "需要人记得同步的清单"一定会漏,而漏了之后不会有任何地方报错。
#
# 算法:遍历每个 Application(apps/definitions/ 和 platform/apps/),如果它
# 自己或者它 source.path 指向的目录里出现了自建镜像的地址,就把它的
# destination.namespace 收进来。
#
# **仍然不是"所有命名空间"**:imagePullSecret 是凭据,撒得越广泄露面越大。
NAMESPACES="$(python3 - <<'PY'
import pathlib, yaml
REPO = pathlib.Path(".")
MARKS = ("ghcr.io/hardstuding/bigdata_ml_paltform", "personal.cr.aliyuncs.com")
out = set()
for app_dir in (REPO / "apps" / "definitions", REPO / "platform" / "apps"):
    if not app_dir.exists():
        continue
    for f in app_dir.glob("*.yaml"):
        try:
            d = yaml.safe_load(f.read_text())
        except Exception:
            continue
        if not isinstance(d, dict) or d.get("kind") != "Application":
            continue
        spec = d.get("spec", {})
        ns = (spec.get("destination") or {}).get("namespace")
        if not ns:
            continue
        text = f.read_text()
        src = spec.get("source") or {}
        path = src.get("path")
        if path:
            for m in (REPO / path).rglob("*.yaml") if (REPO / path).exists() else []:
                text += m.read_text(errors="ignore")
        if any(x in text for x in MARKS):
            out.add(ns)
print(" ".join(sorted(out)))
PY
)"
[ -n "$NAMESPACES" ] || { echo "!! 算不出任何命名空间,不继续(空列表会静默什么都不做)"; exit 1; }

echo "==> 在 ${NAMESPACES} 里建/更新 ${SECRET_NAME}"
for ns in $NAMESPACES; do
  kubectl get ns "$ns" >/dev/null 2>&1 || { echo "    跳过 ${ns}(命名空间不存在)"; continue; }
  kubectl -n "$ns" create secret docker-registry "$SECRET_NAME" \
    --docker-server="$ACR_REGISTRY" \
    --docker-username="$ACR_USERNAME" \
    --docker-password="$ACR_PASSWORD" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  # 挂到默认 ServiceAccount 上,这样这个命名空间里所有 Pod 自动带上,
  # 不用逐个 Deployment 改 imagePullSecrets。
  kubectl -n "$ns" patch serviceaccount default \
    -p "{\"imagePullSecrets\":[{\"name\":\"${SECRET_NAME}\"}]}" >/dev/null
  echo "    ${ns} 就绪"
done

echo
echo "完成。接下来把清单里的镜像地址切到 ACR:"
echo "  ACR_REGISTRY=${ACR_REGISTRY} ACR_NAMESPACE=${ACR_NAMESPACE} \\"
echo "    python3 scripts/switch-image-registry.py --to acr"
echo
echo "注意:**已经在跑的 Pod 不会自动换镜像**——它们用的还是 ghcr.io 那个地址。"
echo "切完清单 push,ArgoCD 同步后才会滚更。"
