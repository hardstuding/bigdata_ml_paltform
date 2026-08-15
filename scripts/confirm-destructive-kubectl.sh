#!/usr/bin/env bash
# 破坏性 kubectl 操作的轻量防护层(响应 2026-08-15 Codex review 的
# P0-3,见 ADR-055"为什么先做轻量版不是完整统一 guard 框架"的说明)。
#
# 直接动机:这个项目历史上真实发生过 `kubectl delete namespace airflow`
# 顺手多打了一行 `kubectl delete namespace data` 的事故(见 memory
# feedback_destructive_ops.md),这次又在没有任何系统性防护的情况下
# 批量删过 namespace/强杀过容器——每次都是操作者自己手动小心,不是
# 靠得住的防线。这个脚本把"执行前必须显式看到完整目标清单、必须显式
# 确认这是给哪个环境用的"这两件事,从"操作者要记得做"变成"不这么做就
# 执行不了"。
#
# 用法:
#   ./scripts/confirm-destructive-kubectl.sh <环境:local-lite|cloud-full> \
#     <kubectl 子命令,比如 delete> <资源类型> <资源名...> [-- 其余 kubectl 参数] \
#     --i-am-sure
#
# 例子:
#   ./scripts/confirm-destructive-kubectl.sh local-lite delete namespace \
#     spark-operator airflow --i-am-sure
#
# 设计上刻意做的限制(不是遗漏):
#   - 只接受明确列出的资源名,不支持 --all/通配符/label selector——调用者
#     必须自己先 `kubectl get` 出准确清单,这个脚本不负责"猜你想删哪些"。
#   - 环境标识和 KUBECONFIG 实际指向的 context 做强制交叉验证,不一致
#     直接拒绝执行,不给"这次可能连错集群"这种事故任何发生空间。
#   - 没有 --i-am-sure 只做预览(打印目标当前状态),不执行真正的命令。
set -euo pipefail

usage() {
  echo "用法: $0 <local-lite|cloud-full> <kubectl子命令> <资源类型> <资源名...> [--i-am-sure]" >&2
  exit 1
}

[ $# -ge 4 ] || usage

ENV="$1"; shift
SUBCMD="$1"; shift
RESOURCE_TYPE="$1"; shift

CONFIRM=0
NAMES=()
for arg in "$@"; do
  if [ "$arg" = "--i-am-sure" ]; then
    CONFIRM=1
  else
    NAMES+=("$arg")
  fi
done

if [ ${#NAMES[@]} -eq 0 ]; then
  echo "!! 至少要显式列出一个资源名,不接受空目标列表" >&2
  exit 1
fi

case "$ENV" in
  local-lite) EXPECTED_CONTEXT="colima" ;;
  cloud-full) EXPECTED_CONTEXT="cloud-full-aliyun" ;;
  *)
    echo "!! 环境标识只能是 local-lite 或 cloud-full,收到: $ENV" >&2
    exit 1
    ;;
esac

CURRENT_CONTEXT="$(kubectl config current-context 2>/dev/null || echo '(无)')"
if [ "$CURRENT_CONTEXT" != "$EXPECTED_CONTEXT" ]; then
  echo "!! 拒绝执行:当前 kubectl context 是 '${CURRENT_CONTEXT}',但你要操作的环境" >&2
  echo "   是 '${ENV}',期望 context 是 '${EXPECTED_CONTEXT}'。" >&2
  echo "   这正是防止'以为在改A环境,实际连到B集群'这类事故存在的原因,不要绕过。" >&2
  echo "   操作 cloud-full 前先: export KUBECONFIG=~/.kube/cloud-full-config" >&2
  exit 1
fi

echo "=== 环境: ${ENV}(context: ${CURRENT_CONTEXT})确认匹配 ==="
echo "=== 目标预览(${RESOURCE_TYPE},共 ${#NAMES[@]} 个)==="
for name in "${NAMES[@]}"; do
  kubectl get "$RESOURCE_TYPE" "$name" 2>&1 || echo "  (${name} 当前不存在或已经不在了)"
done

if [ "$CONFIRM" -ne 1 ]; then
  echo
  echo "=== 只是预览,没有加 --i-am-sure,不会真的执行 ${SUBCMD} ==="
  echo "确认上面这份目标清单就是你要的之后,原样重跑这条命令、末尾加 --i-am-sure"
  exit 0
fi

echo
echo "=== 确认执行:kubectl ${SUBCMD} ${RESOURCE_TYPE} ${NAMES[*]} ==="
kubectl "$SUBCMD" "$RESOURCE_TYPE" "${NAMES[@]}"
