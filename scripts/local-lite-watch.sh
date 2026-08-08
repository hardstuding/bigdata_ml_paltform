#!/usr/bin/env bash
# 后台记录集群状态(Application 同步情况 + VM 内存/负载),写到固定位置的
# 日志文件。用途:你关掉 Claude、自己跑 local-lite-toggle-heavy.sh 测试的
# 时候,启动这个脚本在后台盯着,重新打开 Claude 后我读这个日志就知道
# 你关闭期间发生了什么,不用你口头转述。
#
# 用法(在 Terminal 里,不需要 Claude 在场):
#   nohup ./scripts/local-lite-watch.sh > /dev/null 2>&1 &
#   # 记下打印出来的 PID,想停的时候: kill <PID>
#
# 日志固定写到 logs/watch.log(不进 git),重新打开 Claude 后我会自己去读。
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/watch.log"
INTERVAL_SECONDS="${1:-30}"

echo "开始记录,每 ${INTERVAL_SECONDS} 秒一次,日志在 ${LOG_FILE},本进程 PID: $$"
echo "=== watch 启动 $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

while true; do
  {
    echo "--- $(date -u +%FT%TZ) ---"
    echo "[applications]"
    kubectl get applications -n argocd --no-headers 2>&1
    echo "[memory]"
    colima ssh -- free -h 2>&1 | tail -2
    echo "[load]"
    colima ssh -- uptime 2>&1
    echo
  } >> "$LOG_FILE" 2>&1
  sleep "$INTERVAL_SECONDS"
done
