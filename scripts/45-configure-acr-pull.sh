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

SECRET_NAME="acr-pull"

# **不给密码也能跑**:如果集群里已经有一份 acr-pull(之前配过),就从那里
# 复制到缺的命名空间。
#
# 加这条的直接原因(2026-08-29):第一版的命名空间清单是手写的、漏了
# superset,补的时候人已经不在跟前,而重跑脚本需要密码 —— 结果 superset
# 的镜像拉不下来卡了半天。**"补一个命名空间"是个高频操作,不该每次都要
# 人来输一次密码。**
COPY_FROM=""
if [ -z "${ACR_PASSWORD:-}" ]; then
  # **不能用 `kubectl get secret <名字> -A`** —— kubectl 明确拒绝"跨命名空间
  # 按名字取"(a resource cannot be retrieved by name across all namespaces),
  # 而且它报的是错误、不是空结果,写成 `|| true` 会静默变成"找不到"。
  # 用 field-selector 才是对的。
  COPY_FROM="$(kubectl get secrets -A --field-selector "metadata.name=${SECRET_NAME}" \
    -o jsonpath='{.items[0].metadata.namespace}' 2>/dev/null || true)"
  if [ -z "$COPY_FROM" ]; then
    echo "!! 没有设 ACR_PASSWORD,集群里也找不到现成的 ${SECRET_NAME} 可复制。"
    echo "   第一次配置必须给密码:"
    echo "     zsh:  read -s \"ACR_PASSWORD?ACR password: \" && export ACR_PASSWORD"
    echo "     bash: read -rsp \"ACR password: \" ACR_PASSWORD && export ACR_PASSWORD"
    exit 1
  fi
  echo "==> 没给密码,从 ${COPY_FROM}/${SECRET_NAME} 复制现有凭据"
else
  : "${ACR_REGISTRY:?给了密码就必须一起给 ACR_REGISTRY}"
  : "${ACR_USERNAME:?给了密码就必须一起给 ACR_USERNAME}"
  : "${ACR_NAMESPACE:?给了密码就必须一起给 ACR_NAMESPACE}"
fi

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
  if [ -n "$COPY_FROM" ]; then
    kubectl -n "$COPY_FROM" get secret "$SECRET_NAME" -o json \
      | python3 -c "import sys,json;d=json.load(sys.stdin);d['metadata']={'name':'$SECRET_NAME','namespace':'$ns'};print(json.dumps(d))" \
      | kubectl apply -f - >/dev/null
  else
    kubectl -n "$ns" create secret docker-registry "$SECRET_NAME" \
      --docker-server="$ACR_REGISTRY" \
      --docker-username="$ACR_USERNAME" \
      --docker-password="$ACR_PASSWORD" \
      --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  fi
  # 挂到默认 ServiceAccount 上,这样这个命名空间里所有 Pod 自动带上,
  # 不用逐个 Deployment 改 imagePullSecrets。
  kubectl -n "$ns" patch serviceaccount default \
    -p "{\"imagePullSecrets\":[{\"name\":\"${SECRET_NAME}\"}]}" >/dev/null
  echo "    ${ns} 就绪"
done

echo
if [ -n "${ACR_REGISTRY:-}" ]; then
  echo "完成。接下来把清单里的镜像地址切到 ACR:"
  echo "  ACR_REGISTRY=${ACR_REGISTRY} ACR_NAMESPACE=${ACR_NAMESPACE:-<命名空间>} \\"
  echo "    python3 scripts/switch-image-registry.py --to acr"
else
  # 复制模式下没有这几个变量 —— 这条路径本来就是"补一个命名空间",
  # 清单早就切过了,不用再提示一次。
  echo "完成(从现有凭据复制,没有改任何镜像地址)。"
fi
echo
echo "注意:**已经在跑的 Pod 不会自动换镜像**——它们用的还是 ghcr.io 那个地址。"
echo "切完清单 push,ArgoCD 同步后才会滚更。"
