#!/usr/bin/env bash
# 调整 colima VM 的内存(必填)和 CPU(可选)分配并重启生效。
#
# 背景:2026-08-08 从 6G 扩到 9G、2026-08-12 从 9G 扩到 11G,都是同一个模式
# ——本机内存紧张反复暴露资源相关的假故障(ADR-031 记录过 9G→11G 重启后
# 暴露了两个更深的坑)。2026-08-14 从 11G 扩到 13G 是用户主动要求的,原因是
# ArgoCD 控制面本身在 25+ 个 Application 规模下常驻内存已经吃紧(见
# docs/operations/troubleshooting.md 里 argocd-application-controller
# OOMKilled 那条)。同一天验证 Feast 物化任务时,CPU request 反复顶到
# 87-96%,pod 被延迟调度甚至抢占,补上 CPU 也能调的能力(之前脚本名字里
# 虽然叫 memory,这次顺手扩展,不另建一个几乎一样的脚本)。
#
# 这台 Mac 总物理内存 16GB、8 核 CPU,分给 colima 太多会挤压 macOS 本身和
# 其他 App,不要无脑往上调——每次调整前应该先确认这不是"本该 park 掉暂时
# 不用的组件"就能解决的问题(见 ADR-004 profile 设计的本意)。
#
# 用法:
#   ./scripts/local-lite-resize-colima-memory.sh 13        # 只改内存,CPU 沿用现有配置
#   ./scripts/local-lite-resize-colima-memory.sh 13 6      # 内存改 13G,CPU 改 6 核
#
# 会做的事:colima stop → 用新内存/CPU 值 colima start(沿用已有 disk/
# kubernetes 配置,colima 自己的 profile 会记住)→ 等节点 Ready → 等 ArgoCD
# 所有 Application 收敛回 Synced/Healthy。全程日志写 logs/,重开 Claude
# 后能直接读日志知道发生了什么,不用重新问一遍。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/resize-colima-memory.log"

NEW_MEM_GB="${1:-}"
NEW_CPU="${2:-}"
if [ -z "$NEW_MEM_GB" ]; then
  echo "用法: $0 <目标内存GB,比如 13> [目标CPU核数,比如 6]" >&2
  exit 1
fi

log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

log "=== 开始把 colima 内存调整到 ${NEW_MEM_GB}GB${NEW_CPU:+、CPU 调整到 ${NEW_CPU} 核} ==="
log "调整前状态:"
colima status 2>&1 | tee -a "$LOG_FILE" || true

log "==> colima stop"
colima stop 2>&1 | tee -a "$LOG_FILE"

if [ -n "$NEW_CPU" ]; then
  log "==> colima start --memory ${NEW_MEM_GB} --cpu ${NEW_CPU}(沿用已有 disk/kubernetes 配置)"
  colima start --memory "${NEW_MEM_GB}" --cpu "${NEW_CPU}" 2>&1 | tee -a "$LOG_FILE"
else
  log "==> colima start --memory ${NEW_MEM_GB}(沿用已有 cpu/disk/kubernetes 配置)"
  colima start --memory "${NEW_MEM_GB}" 2>&1 | tee -a "$LOG_FILE"
fi

log "==> 等节点 Ready(最多 5 分钟)"
kubectl wait --for=condition=Ready node --all --timeout=300s 2>&1 | tee -a "$LOG_FILE"

log "==> 等 ArgoCD apps-root 恢复 Healthy(最多 5 分钟,前提是 argocd 命名空间本来就有)"
kubectl -n argocd wait --for=jsonpath='{.status.health.status}'=Healthy application/apps-root --timeout=300s 2>&1 | tee -a "$LOG_FILE" || \
  log "警告:apps-root 5 分钟内没恢复 Healthy,手动查 kubectl get applications -n argocd"

log "==> 当前所有 Application 状态:"
kubectl get applications -n argocd 2>&1 | tee -a "$LOG_FILE"

log "==> 当前资源占用:"
kubectl top nodes 2>&1 | tee -a "$LOG_FILE" || true

log "=== 完成,详情见 ${LOG_FILE} ==="
