#!/usr/bin/env bash
# 一键把 Kafka/Spark Operator/Airflow 这类重组件在 local-lite 上"启用"和
# "关闭"之间切换,方便你自己(不需要 Claude 在场)独立测试。
#
# 背景:2026-08-08/09 验证过,这几个组件同时跑会把本地 6GB 的 colima VM
# 内存打满。已经加了 4GB swap(scripts/local-lite-enable-swap.sh)做安全垫,
# 关掉 Claude 桌面客户端能再腾出 1-2GB,值得自己试试整体是否跑得动。
#
# 2026-08-20 改法(ADR-057 第三批):以前是 `git mv` 一批文件在
# apps/definitions/ 和 environments/cloud-full/pending-definitions/ 之间
# 来回搬,现在这套"目录位置表达启用状态"的机制退役了——改成直接编辑
# environments/local-lite/config.yaml 里的 enabled_components 列表(加一行
# 就是启用,删一行就是关闭),再跑 scripts/render-environment-config.py
# local-lite 重新生成 apps/definitions/。用 sed 做文本级别的加/删,不用
# yaml.safe_load()+dump() 那种"读进内存对象再写回去"的做法——那样会把
# config.yaml 里手写的注释全部冲掉,这个文件的注释比数据本身更重要。
#
# 用法:
#   ./scripts/local-lite-toggle-heavy.sh on    # 加进 enabled_components,本地拉起
#   ./scripts/local-lite-toggle-heavy.sh off   # 从 enabled_components 移除,释放资源
#
# 每次操作后需要自己 git push,然后:
#   kubectl -n argocd patch application apps-root --type merge \
#     -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'
# 触发同步。观察状态:
#   kubectl get applications -n argocd
#   colima ssh -- free -h        # 看内存
#   colima ssh -- uptime         # 看 load average,持续大幅高于核数就该收手
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/toggle-heavy.log"

CONFIG_FILE="environments/local-lite/config.yaml"
FILES="kafka-operator.yaml kafka-cluster.yaml spark-operator.yaml airflow.yaml airflow-db-init.yaml seatunnel.yaml trino.yaml trino-tls.yaml superset.yaml superset-db-init.yaml opensearch.yaml openmetadata.yaml openmetadata-db-init.yaml mlflow.yaml mlflow-db-init.yaml mlflow-oauth2-proxy.yaml"

ACTION="${1:-}"
if [ "$ACTION" != "on" ] && [ "$ACTION" != "off" ]; then
  echo "用法: $0 on|off" >&2
  exit 1
fi

echo "=== toggle ${ACTION} $(date -u +%FT%TZ) ===" >> "$LOG_FILE"
for f in $FILES; do
  already_enabled=false
  grep -qE "^\s*-\s+${f}\s*\$" "$CONFIG_FILE" && already_enabled=true

  if [ "$ACTION" = "on" ] && [ "$already_enabled" = false ]; then
    # 直接追加到 enabled_components 列表末尾(YAML 列表顺序无所谓,不用
    # 找精确插入位置,追加到文件最后一行就行——config.yaml 目前的写法是
    # enabled_components 就是整个文件的最后一段)。
    echo "  - ${f}" >> "$CONFIG_FILE"
    echo "启用: ${f}" | tee -a "$LOG_FILE"
  elif [ "$ACTION" = "off" ] && [ "$already_enabled" = true ]; then
    # macOS/BSD sed 和 GNU sed 的 -i 参数不兼容(一个要求 -i '' 一个不要),
    # 用临时文件绕开这个坑,不判断是哪个 sed。
    grep -vE "^\s*-\s+${f}\s*\$" "$CONFIG_FILE" > "${CONFIG_FILE}.tmp"
    mv "${CONFIG_FILE}.tmp" "$CONFIG_FILE"
    echo "收回: ${f}" | tee -a "$LOG_FILE"
  fi
done

echo
echo "重新生成 apps/definitions/..."
python3 scripts/render-environment-config.py local-lite

echo
echo "改完了,还没提交。检查一下 git status,确认没问题再:"
echo "  git commit -m 'toggle heavy components: ${ACTION}'"
echo "  git push"
echo "然后触发同步:"
echo "  kubectl -n argocd patch application apps-root --type merge -p '{\"metadata\":{\"annotations\":{\"argocd.argoproj.io/refresh\":\"hard\"}}}'"
echo
echo "建议顺手在后台起一个状态记录(重开 Claude 后我能看到这段时间发生了什么):"
echo "  nohup ./scripts/local-lite-watch.sh > /dev/null 2>&1 &"
