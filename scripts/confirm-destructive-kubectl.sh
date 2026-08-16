#!/usr/bin/env bash
# 破坏性 kubectl 操作的轻量防护层(响应 2026-08-15 Codex review 的
# P0-3,见 ADR-055"为什么先做轻量版不是完整统一 guard 框架"的说明)。
#
# 直接动机:这个项目历史上真实发生过 `kubectl delete namespace airflow`
# 顺手多打了一行 `kubectl delete namespace data` 的事故(见
# docs/operations/incidents.md,2026-08-16 从私有 memory 补成仓库里
# 正式的事故复盘)——这次又在没有任何系统性防护的情况下批量删过
# namespace/强杀过容器。这个脚本把"执行前必须显式看到完整目标清单、
# 必须显式确认这是给哪个环境用的"这两件事,从"操作者要记得做"变成
# "不这么做就执行不了"。
#
# 2026-08-16 补的两处缺口(原始评审 5 条建议里当时没做的):
#   - namespace 允许清单:目标 namespace 必须是这个仓库里真实有
#     Application 在管的 namespace(动态从 apps/definitions、
#     platform/apps 等目录下的 ArgoCD Application 的 destination.namespace
#     现查,不是手维护一份容易漂移的清单)——挡掉"打错 namespace 名字/
#     操作到不相干命名空间"这类失误。
#   - 受保护 namespace 二次确认:即使在允许清单里,`data`/`kube-system`/
#     `kube-public`/`kube-node-lease`/`argocd` 这几个"删了代价特别大"
#     的 namespace,删除前额外要求 `--i-understand-protected-namespace`
#     这个单独的 flag(不能靠 --i-am-sure 一次带过),而且如果目标是
#     `data`(Postgres 所在地),会先查一次 MinIO 里最近一次 pg_dumpall
#     备份是不是在 26 小时内(备份 CronJob 每天 02:00 跑一次,26 小时
#     留了余量),备份过旧或查不到会强烈警告(但不会硬挡——数据本身
#     没自动恢复能力去验证"挡了就一定对",最终决定权还是留给显式的
#     protected-namespace 确认)。
#
# 用法:
#   ./scripts/confirm-destructive-kubectl.sh <环境:local-lite|cloud-full> \
#     <kubectl 子命令,比如 delete> <资源类型> <资源名...> \
#     [-n <namespace>] [--i-understand-protected-namespace] --i-am-sure
#
# 例子:
#   ./scripts/confirm-destructive-kubectl.sh local-lite delete namespace \
#     spark-operator airflow --i-am-sure
#   ./scripts/confirm-destructive-kubectl.sh cloud-full delete pvc \
#     data-postgres-0 -n data --i-understand-protected-namespace --i-am-sure
#
# 设计上刻意做的限制(不是遗漏):
#   - 只接受明确列出的资源名,不支持 --all/通配符/label selector——调用者
#     必须自己先 `kubectl get` 出准确清单,这个脚本不负责"猜你想删哪些"。
#   - 环境标识和 KUBECONFIG 实际指向的 context 做强制交叉验证,不一致
#     直接拒绝执行,不给"这次可能连错集群"这种事故任何发生空间。
#   - 没有 --i-am-sure 只做预览(打印目标当前状态),不执行真正的命令。
#   - 不是完整统一 guard 框架(ADR-055 的决定):不校验任意 kubectl 参数
#     组合,不支持这个脚本以外的破坏性命令(比如裸 `docker kill`)——只
#     覆盖这个脚本明确设计要覆盖的 delete 类场景。
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
  echo "用法: $0 <local-lite|cloud-full> <kubectl子命令> <资源类型> <资源名...> [-n <namespace>] [--i-understand-protected-namespace] [--i-am-sure]" >&2
  exit 1
}

[ $# -ge 4 ] || usage

ENV="$1"; shift
SUBCMD="$1"; shift
RESOURCE_TYPE="$1"; shift

CONFIRM=0
PROTECTED_ACK=0
TARGET_NAMESPACE=""
NAMES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --i-am-sure) CONFIRM=1 ;;
    --i-understand-protected-namespace) PROTECTED_ACK=1 ;;
    -n|--namespace) shift; TARGET_NAMESPACE="${1:-}" ;;
    *) NAMES+=("$1") ;;
  esac
  shift
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

# ---- namespace 允许清单:动态从仓库里的 ArgoCD Application 现查,不是
# 手维护的静态列表(避免和实际组件清单漂移)----
ALLOWED_NAMESPACES=$(
  grep -rl "kind: Application" apps/ platform/ environments/*/pending-definitions/ 2>/dev/null \
    | xargs -I{} sh -c 'grep -A3 "^  destination:" "{}" 2>/dev/null | grep "namespace:"' 2>/dev/null \
    | awk '{print $2}' | tr -d '"' | sort -u
)

# ---- 确定这次操作实际会碰到哪个 namespace ----
if [ "$RESOURCE_TYPE" = "namespace" ]; then
  EFFECTIVE_NAMESPACES=("${NAMES[@]}")
elif [ -n "$TARGET_NAMESPACE" ]; then
  EFFECTIVE_NAMESPACES=("$TARGET_NAMESPACE")
else
  EFFECTIVE_NAMESPACES=()
  echo "!! 提示:资源类型 '${RESOURCE_TYPE}' 通常是 namespace 级资源,但没有传 -n," >&2
  echo "   本次不做 namespace 允许清单校验(比如集群级资源本来就没有 namespace)。" >&2
fi

PROTECTED_NAMESPACES="data kube-system kube-public kube-node-lease argocd"

for ns in "${EFFECTIVE_NAMESPACES[@]:-}"; do
  [ -z "$ns" ] && continue
  if ! echo "$ALLOWED_NAMESPACES" | grep -qx "$ns"; then
    echo "!! 拒绝执行:namespace '${ns}' 不在这个仓库当前管理的 namespace 清单里" >&2
    echo "   (清单是现从 apps/definitions、platform/apps 等目录的 ArgoCD" >&2
    echo "   Application destination.namespace 查出来的)。如果这个 namespace" >&2
    echo "   确实应该存在,先确认有没有打错字;如果是刻意要操作清单外的" >&2
    echo "   namespace,这个脚本不负责,自己手动 kubectl,不要绕过这层校验。" >&2
    exit 1
  fi
  if echo "$PROTECTED_NAMESPACES" | tr ' ' '\n' | grep -qx "$ns"; then
    echo "=== namespace '${ns}' 是受保护 namespace(${PROTECTED_NAMESPACES})==="
    if [ "$PROTECTED_ACK" -ne 1 ]; then
      echo "!! 拒绝执行:操作受保护 namespace 必须额外加 --i-understand-protected-namespace" >&2
      echo "   (不能只靠 --i-am-sure 一次带过,这是当年误删 data namespace 那次事故" >&2
      echo "   的直接教训,见 docs/operations/incidents.md)" >&2
      exit 1
    fi
    if [ "$ns" = "data" ] && [ "$CONFIRM" -eq 1 ]; then
      echo "=== 目标涉及 data namespace,检查最近一次 Postgres 备份 ==="
      MINIO_USER=$(kubectl get secret -n minio minio-root -o jsonpath='{.data.rootUser}' 2>/dev/null | base64 -d || true)
      MINIO_PASSWORD=$(kubectl get secret -n minio minio-root -o jsonpath='{.data.rootPassword}' 2>/dev/null | base64 -d || true)
      if [ -z "$MINIO_USER" ]; then
        echo "!! 警告:查不到 MinIO 凭据,没法确认最近备份时间——不阻止执行," >&2
        echo "   但你应该自己先确认 data namespace 有没有可用的最近备份。" >&2
      else
        # 不用 `kubectl run --rm -i`(交互式 attach 在这台资源紧张的
        # colima 集群上实测会卡住/超时,不可靠)——改成创建一次性 Pod、
        # 轮询到 Succeeded/Failed 再读 `kubectl logs`。轮询按墙钟时间
        # 限制在 20 秒内(不是固定次数——这台本机 colima 节点内存长期
        # 90%+ 占用,单个 Pod 从创建到 Started 实测能花 45~90 秒,这层
        # 检查本来就只是"仅供参考",不该为了等一个可能很慢的调度结果
        # 拖住整个 guard 脚本,超时就降级成警告,不阻止执行)。
        # 另外 `kubectl logs` 在这台 Mac 上有个已知的、我这边没法从命令行
        # 修的问题(本机代理软件透明拦截 colima 虚拟网段的直连 kubelet
        # 流量,报 "Internal Privoxy Error",见
        # docs/operations/troubleshooting.md"kubectl logs / exec ...
        # Internal Privoxy Error"那条)——local-lite 上这层检查大概率会
        # 落到"查不到"这个降级分支,不代表真的没有备份,cloud-full 没有
        # 这个本机代理问题,不受影响。
        CHECK_POD="pg-backup-check-$$"
        kubectl delete pod "$CHECK_POD" --ignore-not-found=true >/dev/null 2>&1
        kubectl run "$CHECK_POD" --restart=Never \
          --image=minio/mc:RELEASE.2025-08-13T08-35-41Z --command -- sh -c "
          mc alias set backupminio http://minio.minio.svc.cluster.local:9000 '${MINIO_USER}' '${MINIO_PASSWORD}' >/dev/null 2>&1
          mc ls backupminio/backups/postgres/ 2>/dev/null | sort | tail -1
        " >/dev/null 2>&1 || true
        POD_PHASE=""
        POLL_START=$(date +%s)
        while [ $(( $(date +%s) - POLL_START )) -lt 20 ]; do
          POD_PHASE=$(kubectl get pod "$CHECK_POD" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
          { [ "$POD_PHASE" = "Succeeded" ] || [ "$POD_PHASE" = "Failed" ]; } && break
          sleep 2
        done
        if [ "$POD_PHASE" != "Succeeded" ]; then
          LATEST_BACKUP=""
          echo "!! 警告:20 秒内没查到备份检查结果(最终状态: ${POD_PHASE:-未知})," >&2
          echo "   不阻止执行,但你应该自己先手动确认备份状态。" >&2
        else
          LATEST_BACKUP=$(kubectl logs "$CHECK_POD" 2>/dev/null | tail -1)
        fi
        kubectl delete pod "$CHECK_POD" --ignore-not-found=true --wait=false >/dev/null 2>&1
        if [ -z "$LATEST_BACKUP" ]; then
          echo "!! 警告:没能确认 MinIO backups/postgres/ 下最近的备份文件" >&2
          echo "   (可能是真的没有备份,也可能是本机已知的 kubectl logs/Privoxy" >&2
          echo "   限制——local-lite 上大概率是后者)。如果现在删 data namespace," >&2
          echo "   自己先确认一下有没有能用的备份。" >&2
        else
          echo "最近一次备份记录: ${LATEST_BACKUP}"
          BACKUP_DATE=$(echo "$LATEST_BACKUP" | awk '{print $1}')
          BACKUP_EPOCH=$(date -j -f "%Y-%m-%d" "$BACKUP_DATE" +%s 2>/dev/null || date -d "$BACKUP_DATE" +%s 2>/dev/null || echo 0)
          NOW_EPOCH=$(date +%s)
          AGE_HOURS=$(( (NOW_EPOCH - BACKUP_EPOCH) / 3600 ))
          if [ "$BACKUP_EPOCH" -eq 0 ] || [ "$AGE_HOURS" -gt 26 ]; then
            echo "!! 警告:最近备份距今约 ${AGE_HOURS} 小时,超过每日备份 CronJob 的" >&2
            echo "   正常间隔(26小时留了余量)——备份可能过旧或解析失败,继续前自己" >&2
            echo "   确认一下这份备份是不是真的够新。" >&2
          else
            echo "备份新鲜度正常(约 ${AGE_HOURS} 小时内)。"
          fi
        fi
      fi
    fi
  fi
done

echo "=== 环境: ${ENV}(context: ${CURRENT_CONTEXT})确认匹配 ==="
echo "=== 目标预览(${RESOURCE_TYPE},共 ${#NAMES[@]} 个${TARGET_NAMESPACE:+, namespace: $TARGET_NAMESPACE})==="
# bash 3.2(macOS 默认)对声明成空的数组配合 set -u 有个已知 bug,
# `"${arr[@]}"` 会报 unbound variable——不用空数组展开,显式判断有没有
# namespace 参数,分两条路径调用,避免依赖这个数组展开行为。
for name in "${NAMES[@]}"; do
  if [ -n "$TARGET_NAMESPACE" ]; then
    kubectl get "$RESOURCE_TYPE" "$name" -n "$TARGET_NAMESPACE" 2>&1 || echo "  (${name} 当前不存在或已经不在了)"
  else
    kubectl get "$RESOURCE_TYPE" "$name" 2>&1 || echo "  (${name} 当前不存在或已经不在了)"
  fi
done

if [ "$CONFIRM" -ne 1 ]; then
  echo
  echo "=== 只是预览,没有加 --i-am-sure,不会真的执行 ${SUBCMD} ==="
  echo "确认上面这份目标清单就是你要的之后,原样重跑这条命令、末尾加 --i-am-sure"
  exit 0
fi

echo
echo "=== 确认执行:kubectl ${SUBCMD} ${RESOURCE_TYPE} ${NAMES[*]}${TARGET_NAMESPACE:+ -n $TARGET_NAMESPACE} ==="
if [ -n "$TARGET_NAMESPACE" ]; then
  kubectl "$SUBCMD" "$RESOURCE_TYPE" "${NAMES[@]}" -n "$TARGET_NAMESPACE"
else
  kubectl "$SUBCMD" "$RESOURCE_TYPE" "${NAMES[@]}"
fi
