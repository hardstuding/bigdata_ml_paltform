#!/usr/bin/env bash
# 一键把 Kafka/Spark Operator/Airflow 在"本地跑"和"挪去 cloud-full 待启用区"
# 之间切换,方便你自己(不需要 Claude 在场)独立测试。
#
# 背景:2026-08-08/09 验证过,这几个组件同时跑会把本地 6GB 的 colima VM
# 内存打满。已经加了 4GB swap(scripts/local-lite-enable-swap.sh)做安全垫,
# 关掉 Claude 桌面客户端能再腾出 1-2GB,值得自己试试整体是否跑得动。
#
# 用法:
#   ./scripts/local-lite-toggle-heavy.sh on    # 挪回 apps/definitions,本地拉起
#   ./scripts/local-lite-toggle-heavy.sh off   # 挪回 environments/cloud-full/pending-definitions,释放资源
#
# 每次操作后需要自己 git push,然后:
#   kubectl -n argocd patch application apps-root --type merge \
#     -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'
# 触发同步。观察状态:
#   kubectl get applications -n argocd
#   colima ssh -- free -h        # 看内存
#   colima ssh -- uptime         # 看 load average,持续大幅高于核数就该收手
set -euo pipefail

PENDING_DIR="environments/cloud-full/pending-definitions"
ACTIVE_DIR="apps/definitions"
FILES="kafka-operator.yaml kafka-cluster.yaml spark-operator.yaml airflow.yaml airflow-db-init.yaml"

ACTION="${1:-}"
if [ "$ACTION" != "on" ] && [ "$ACTION" != "off" ]; then
  echo "用法: $0 on|off" >&2
  exit 1
fi

for f in $FILES; do
  if [ "$ACTION" = "on" ] && [ -f "${PENDING_DIR}/${f}" ]; then
    git mv "${PENDING_DIR}/${f}" "${ACTIVE_DIR}/${f}"
    echo "启用: ${f}"
  elif [ "$ACTION" = "off" ] && [ -f "${ACTIVE_DIR}/${f}" ]; then
    git mv "${ACTIVE_DIR}/${f}" "${PENDING_DIR}/${f}"
    echo "收回: ${f}"
  fi
done

echo
echo "改完了,还没提交。检查一下 git status,确认没问题再:"
echo "  git commit -m 'toggle heavy components: ${ACTION}'"
echo "  git push"
echo "然后照上面注释里的命令触发同步、看状态。"
