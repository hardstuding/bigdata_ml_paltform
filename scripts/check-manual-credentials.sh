#!/usr/bin/env bash
# 一次性扫描:这个平台目前所有组件的 Deployment/CronJob 引用了哪些
# Secret(secretKeyRef),这些 Secret 现在存不存在、哪个 key 缺了。
#
# 背景(见和内部讨论,2026-08 系列会话):理想的"一键部署"应该是
# 两阶段——第一次部署后,先把所有需要人工填的凭据一次性列全给用户去准备,
# 用户填完再跑第二个脚本把东西串起来。这个脚本就是第一阶段的"列全"部分:
# 只读、不改任何集群状态,扫出一份清单,不是自动生成凭据(凭据本身该是
# 什么值是人的判断,这个项目已经明确不允许 AI 代填密码类的东西)。
#
# 用法: ./scripts/check-manual-credentials.sh
# 日志: logs/check-manual-credentials.log (追加写入,不覆盖历史记录)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$REPO_ROOT/logs/check-manual-credentials.log"
mkdir -p "$REPO_ROOT/logs"

log() { echo "$1" | tee -a "$LOG_FILE"; }

log ""
log "===== check-manual-credentials.sh 运行于 $(date -u +%Y-%m-%dT%H:%M:%SZ) ====="

if ! command -v kubectl >/dev/null 2>&1; then
  log "错误: 找不到 kubectl,退出"
  exit 1
fi

MISSING_COUNT=0
PRESENT_COUNT=0

# 扫描所有命名空间里 Deployment/CronJob/Job 引用的 secretKeyRef,
# 汇总成 "namespace secretName optional" 三元组(去重)。
REFS=$(kubectl get deployments,cronjobs,jobs --all-namespaces -o json 2>/dev/null | \
  python3 -c '
import json, sys
data = json.load(sys.stdin)
seen = set()
for item in data.get("items", []):
    ns = item["metadata"]["namespace"]
    kind = item["kind"]
    name = item["metadata"]["name"]
    if kind == "CronJob":
        containers = item["spec"]["jobTemplate"]["spec"]["template"]["spec"].get("containers", [])
    else:
        containers = item["spec"]["template"]["spec"].get("containers", [])
    for c in containers:
        for env in c.get("env", []) or []:
            vf = env.get("valueFrom", {})
            skr = vf.get("secretKeyRef")
            if skr:
                # 去重按 (namespace, secret 名, key) 算,不按具体哪个对象引用——
                # 同一个 CronJob 触发出来的历史 Job(名字带随机后缀)会反复
                # 引用同一个 Secret,不去重会把清单刷屏成大量近似重复行。
                dedup_key = (ns, skr["name"], skr["key"])
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    sname = skr["name"]
                    sopt = skr.get("optional", False)
                    skey = skr["key"]
                    print(f"{ns}|{sname}|{sopt}|{skey}|{kind}/{name}")
' 2>/dev/null || true)

if [ -z "$REFS" ]; then
  log "没有扫到任何 Deployment/CronJob 引用 secretKeyRef(可能是集群没起,或者确实没有)"
  exit 0
fi

log ""
log "组件引用的凭据一览(命名空间 / Secret 名 / 是否 optional / key / 引用它的对象):"
log "----------------------------------------------------------------------"

while IFS='|' read -r ns secret_name optional key ref; do
  [ -z "$ns" ] && continue
  if kubectl get secret "$secret_name" -n "$ns" >/dev/null 2>&1; then
    val=$(kubectl get secret "$secret_name" -n "$ns" -o jsonpath="{.data.$key}" 2>/dev/null || echo "")
    if [ -z "$val" ]; then
      log "[缺 key]   $ns/$secret_name 存在,但没有 key='$key' (optional=$optional, 引用方: $ref)"
      MISSING_COUNT=$((MISSING_COUNT + 1))
    else
      log "[已就位]   $ns/$secret_name#$key (引用方: $ref)"
      PRESENT_COUNT=$((PRESENT_COUNT + 1))
    fi
  else
    if [ "$optional" = "True" ]; then
      log "[缺·可选]  $ns/$secret_name#$key 不存在(optional=true,组件会静默降级,不阻塞启动,引用方: $ref)"
    else
      log "[缺·必需]  $ns/$secret_name#$key 不存在(optional=false,组件可能启动失败,引用方: $ref)"
    fi
    MISSING_COUNT=$((MISSING_COUNT + 1))
  fi
done <<< "$REFS"

log "----------------------------------------------------------------------"
log "汇总: 已就位 $PRESENT_COUNT 项,缺失/待填 $MISSING_COUNT 项"
log "完整清单见 $LOG_FILE"
log ""
log "怎么填: 每个 [缺·必需]/[缺·可选]/[缺 key] 条目,对应
  scripts/00-generate-secrets.sh 里同名 Secret 的说明块,去那边找占位符
  规则(哪些是自动生成的随机值、哪些必须人工提供真实凭据)。"
