#!/usr/bin/env bash
# 把 OpenMetadata 自己建的采集/质量 CronJob 的 startingDeadlineSeconds
# 从 60 放大到 6 小时。
#
# **为什么需要这个脚本**(2026-08-28 实测,不是推测):OpenMetadata 的
# k8s pipeline deployer 建 CronJob 时把 startingDeadlineSeconds 写死成 60。
# 在一台常开的机器上没问题;在 cloud-full 这种抢占式实例上——大部分时间
# 关机——每个计划时刻都在关机期间过去,开机时早就超过 60 秒了,CronJob
# 控制器直接跳过,**永远不会触发**。实测结果:3 个 om-cronjob 建出来 5 天,
# lastScheduleTime 一直是空,一个 Job 都没产生过,而 OpenMetadata 界面上
# 这几条 pipeline 看着是配好的。
#
# 放大到 21600(= 6 小时,正好是调度周期),含义变成"开机时如果上一个
# 计划时刻在 6 小时内,就补跑一次"——每次开机至少能跑到一轮。一个周期内
# 最多错过 1 次,不会触发 k8s 那个 "too many missed start times (>100)"
# 的保护。
#
# **每次 deploy 之后都要重跑**:调 OpenMetadata 的
# `/services/ingestionPipelines/deploy/{id}` 会重建 CronJob,deadline 会
# 被重置回 60(2026-08-28 亲眼看到一次)。所以 scripts/29 和 scripts/34
# 末尾都调用了它,不是只在装的时候跑一次。
set -euo pipefail
OM_NS="${OM_NS:-openmetadata}"
DEADLINE="${DEADLINE:-21600}"

found=0
for c in $(kubectl -n "$OM_NS" get cronjob -o name 2>/dev/null | grep "om-cronjob-" || true); do
  found=$((found + 1))
  cur="$(kubectl -n "$OM_NS" get "$c" -o jsonpath='{.spec.startingDeadlineSeconds}' 2>/dev/null || echo "?")"
  if [ "$cur" = "$DEADLINE" ]; then
    echo "  ${c} 已经是 ${DEADLINE},跳过"
  else
    kubectl -n "$OM_NS" patch "$c" --type merge \
      -p "{\"spec\":{\"startingDeadlineSeconds\":${DEADLINE}}}" >/dev/null
    echo "  ${c} ${cur} -> ${DEADLINE}"
  fi
done
[ "$found" -gt 0 ] || echo "  ${OM_NS} 里没有 om-cronjob-*,可能还没部署过采集管道。"
