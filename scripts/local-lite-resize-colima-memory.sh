#!/usr/bin/env bash
# 调整 colima VM 的内存分配并重启生效。
#
# 背景:2026-08-08 从 6G 扩到 9G、2026-08-12 从 9G 扩到 11G,都是同一个模式
# ——本机内存紧张反复暴露资源相关的假故障(ADR-031 记录过 9G→11G 重启后
# 暴露了两个更深的坑)。2026-08-14 从 11G 扩到 13G 是用户主动要求的,原因是
# ArgoCD 控制面本身在 25+ 个 Application 规模下常驻内存已经吃紧(见
# docs/operations/troubleshooting.md 里 argocd-application-controller
# OOMKilled 那条),需要给后续验证(比如 Airflow + Feast 同时跑)留余量。
#
# 这台 Mac 总物理内存 16GB,分给 colima 太多会挤压 macOS 本身和其他 App,
# 不要无脑往上调——每次调整前应该先确认这不是"本该 park 掉暂时不用的组件"
# 就能解决的问题(见 ADR-004 profile 设计的本意)。
#
# 用法:
#   ./scripts/local-lite-resize-colima-memory.sh 13
#
# 会做的事:colima stop → 用新内存值 colima start(沿用已有 cpu/disk/
# kubernetes 配置,colima 自己的 profile 会记住)→ 等节点 Ready → 等 ArgoCD
# 所有 Application 收敛回 Synced/Healthy。全程日志写 logs/,重开 Claude
# 后能直接读日志知道发生了什么,不用重新问一遍。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/resize-colima-memory.log"

NEW_MEM_GB="${1:-}"
if [ -z "$NEW_MEM_GB" ]; then
  echo "用法: $0 <目标内存GB,比如 13>" >&2
  exit 1
fi

log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

log "=== 开始把 colima 内存调整到 ${NEW_MEM_GB}GB ==="
log "调整前状态:"
colima status 2>&1 | tee -a "$LOG_FILE" || true

log "==> colima stop"
colima stop 2>&1 | tee -a "$LOG_FILE"

log "==> colima start --memory ${NEW_MEM_GB}(沿用已有 cpu/disk/kubernetes 配置)"
colima start --memory "${NEW_MEM_GB}" 2>&1 | tee -a "$LOG_FILE"

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
