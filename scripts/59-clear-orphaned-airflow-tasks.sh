#!/usr/bin/env bash
# 清掉卡在 running/queued 的孤儿 TaskInstance —— 非正常关机之后 Airflow
# scheduler 起不来时用。
#
# **症状**:scheduler 反复 CrashLoopBackOff,堆栈固定停在这里:
#
#     scheduler_job_runner.py ... adopt_or_reset_orphaned_tasks
#     taskinstance.py ... __repr__
#     sqlalchemy.orm.exc.DetachedInstanceError: Parent instance
#     <TaskInstance ...> is not bound to a Session; deferred load
#     operation of attribute 'state' cannot proceed
#
# **成因**:机器被强制停机(或者 executor 被杀)时,正在跑的 TaskInstance
# 停在 `running`/`queued`,而执行它的 Pod 已经不存在了。scheduler 启动时
# 要"接管"这些孤儿任务,而 Airflow 3.2.2 在给它们打日志时踩到 SQLAlchemy
# 的 DetachedInstanceError,**在接管阶段就崩掉,永远起不来**。
#
# 也就是说:**一次非正常关机会让整个调度器卡死**,而且从现象上看不出和
# 关机有关 —— 报错里全是 SQLAlchemy 的词。2026-09-03 实测撞到(那天为了
# 迁移磁盘和修看门狗,机器被强制停了好几次)。
#
# 这个脚本把那些孤儿标成 failed(它们本来就已经不在跑了),然后重启
# scheduler。**不会动任何还活着的任务** —— 判据是 state 在
# running/queued/restarting 或者为空,而这些状态在 scheduler 挂着的时候
# 不可能有真在跑的任务。
#
# 用法:
#   ./scripts/59-clear-orphaned-airflow-tasks.sh
#   DRY_RUN=1 ./scripts/59-clear-orphaned-airflow-tasks.sh   # 只看会动哪些
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG="logs/clear-orphaned-airflow-tasks.log"
echo "=== clear-orphaned-airflow-tasks $(date -u +%FT%TZ) ===" >> "$LOG"

# **用 api-server 而不是 scheduler 跑** —— scheduler 正崩着,exec 不进去。
POD=$(kubectl -n airflow get pod -l component=api-server \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
[ -n "${POD:-}" ] || POD=$(kubectl -n airflow get pods --no-headers 2>/dev/null \
  | grep api-server | grep Running | awk '{print $1}' | head -1)
[ -n "${POD:-}" ] || { echo "!! 找不到 Running 的 airflow api-server pod" | tee -a "$LOG"; exit 1; }
CTR=$(kubectl -n airflow get pod "$POD" -o jsonpath='{.spec.containers[0].name}')

kubectl exec -i -n airflow "$POD" -c "$CTR" -- python - "${DRY_RUN:-0}" <<'PY' 2>&1 | tee -a "$LOG"
import sys
from collections import Counter

from airflow.models import TaskInstance
from airflow.utils.session import create_session

dry = sys.argv[1] == "1"
with create_session() as s:
    print("  状态分布:", dict(Counter(r[0] for r in s.query(TaskInstance.state).all())))
    orphans = s.query(TaskInstance).filter(
        TaskInstance.state.in_(["running", "queued", "restarting"])
        | TaskInstance.state.is_(None)).all()
    for ti in orphans:
        print(f"  孤儿: {ti.dag_id}.{ti.task_id} run={ti.run_id} state={ti.state}")
    if dry:
        print(f"  DRY_RUN=1,没有真的改({len(orphans)} 条)")
        raise SystemExit(0)
    for ti in orphans:
        ti.state = "failed"
    s.commit()
    print(f"  已把 {len(orphans)} 条标成 failed")
PY

if [ "${DRY_RUN:-0}" = "1" ]; then exit 0; fi
echo "--> 重启 scheduler" | tee -a "$LOG"
kubectl -n airflow delete pod -l component=scheduler --wait=false >> "$LOG" 2>&1 || true
echo "--> 等它起来(最多 5 分钟)" | tee -a "$LOG"
for _ in $(seq 1 30); do
  sleep 10
  READY=$(kubectl -n airflow get pods --no-headers 2>/dev/null | grep scheduler | grep -c "Running" || true)
  RESTARTS=$(kubectl -n airflow get pods --no-headers 2>/dev/null | grep scheduler | awk '{print $4}' | head -1)
  if [ "$READY" -ge 1 ] && [ "${RESTARTS:-1}" = "0" ]; then
    echo "--> scheduler 起来了,0 重启" | tee -a "$LOG"; exit 0
  fi
done
echo "!! scheduler 还没稳定,看 kubectl -n airflow logs -l component=scheduler" | tee -a "$LOG"
exit 1
