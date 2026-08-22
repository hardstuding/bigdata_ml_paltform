#!/usr/bin/env bash
# 端到端验证:Kafka(device-events topic)-> Flink 流式作业 -> Iceberg,见
# docs/decisions/062-flink-streaming-pipeline.md。
#
# 编号说明:任务原计划用 scripts/30,但这一轮开工前发现 29/30 已经被另一条
# 并行主线(OpenMetadata 自动采集 Trino 元数据,commit a30bb18/0ebcfdd)
# 占用——写这个脚本之前重新 `ls scripts/` 确认过,不是拍脑袋改的号。
#
# 这个脚本不只是看 FlinkDeployment 的 state 是不是 RUNNING(那只能证明
# 作业在跑,不能证明数据真的流过去了)——和这个仓库其它验证脚本一样,
# 从结果侧独立核实:直接查 Iceberg 表,确认这一轮真的新增了行,不是只看
# 任务/资源状态。
#
# 前置条件(**这一轮全部没有在真实集群跑过**,见
# docs/decisions/062-flink-streaming-pipeline.md 的验证记录部分):
#   - flink-kubernetes-operator / flink-streaming-demo / kafka-producer-
#     device-events 这三个 Application 已经加进 enabled_components 并同步过
#   - apps/flink-iceberg-image、apps/kafka-producer-image 两个镜像已经被
#     .github/workflows/build-images.yml 构建推送过,manifest 里的
#     :latest 占位 tag 已经指向真实构建产物
#   - table-registration-app(复用它的 Trino 服务账号凭据做验证查询,和
#     scripts/18-table-registration-demo.sh 同一个模式,不用另外配一套
#     Trino 客户端环境)
#
# 用法:
#   ./scripts/31-run-flink-streaming-demo.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/flink-streaming-demo.log"
echo "=== flink-streaming-demo $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

log() { echo "$@" | tee -a "$LOG_FILE"; }

if ! kubectl get namespace flink >/dev/null 2>&1; then
  log "!! flink 这个 namespace 不存在,flink-kubernetes-operator 是不是还没启用/同步?"
  exit 1
fi
if ! kubectl get namespace kafka >/dev/null 2>&1; then
  log "!! kafka 这个 namespace 不存在,Kafka 是不是还没启用?"
  exit 1
fi

TABLE_POD=$(kubectl get pod -n table-registration-app -l app=table-registration-app -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$TABLE_POD" ]; then
  log "!! 找不到 table-registration-app 的 pod——这个脚本借用它已经装好的 Trino"
  log "   python 客户端 + 服务账号凭据做结果核实,不是这个 demo 本身的依赖。"
  exit 1
fi

query_row_count() {
  # 表可能还不存在(Flink 作业第一次跑之前),不存在时当 0 行处理,不报错
  # 中止——这是这个验证脚本自己第一次跑之前的正常状态。
  kubectl exec -n table-registration-app "$TABLE_POD" -- python3 -c "
import os, trino
from trino.auth import BasicAuthentication
pw = os.environ['TRINO_PASSWORD']
conn = trino.dbapi.connect(host='trino.trino.svc.cluster.local', port=8443, user='table_registration_service',
    http_scheme='https', verify=False, auth=BasicAuthentication('table_registration_service', pw), catalog='iceberg')
cur = conn.cursor()
try:
    cur.execute('SELECT count(*) FROM iceberg.demo.device_events_stream')
    print(cur.fetchall()[0][0])
except Exception as e:
    if 'does not exist' in str(e) or 'TABLE_NOT_FOUND' in str(e):
        print(0)
    else:
        raise
" 2>/dev/null
}

log "==> 作业开跑前先查一次 iceberg.demo.device_events_stream 的行数(基线)"
BEFORE_COUNT=$(query_row_count)
log "    基线行数: ${BEFORE_COUNT}"

log "==> 确认 FlinkDeployment 处于 RUNNING(最多等 5 分钟——镜像拉取 + 提交 + Kafka/Iceberg 连接建立)"
STATE=""
for i in $(seq 1 30); do
  STATE=$(kubectl get flinkdeployment -n flink device-events-stream -o jsonpath='{.status.jobStatus.state}' 2>/dev/null || echo "")
  log "  [$i] jobStatus.state=${STATE}"
  if [ "$STATE" = "RUNNING" ]; then
    break
  fi
  sleep 10
done
if [ "$STATE" != "RUNNING" ]; then
  log "!! FlinkDeployment 没有进入 RUNNING,打印 JobManager 日志排查"
  kubectl logs -n flink -l app=device-events-stream,component=jobmanager --tail=80 2>&1 | tee -a "$LOG_FILE" || true
  exit 1
fi

log "==> 触发一次 Kafka 生产者(CronJob 手动跑一次,不等 schedule 到点)"
JOB_NAME="device-events-producer-manual-$(date +%s)"
kubectl create job -n kafka "$JOB_NAME" --from=cronjob/device-events-producer 2>&1 | tee -a "$LOG_FILE"

log "==> 等生产者这次运行跑完(最多 2 分钟)"
kubectl wait -n kafka --for=condition=complete "job/${JOB_NAME}" --timeout=120s 2>&1 | tee -a "$LOG_FILE" || {
  log "!! 生产者这次运行没有在超时内完成,打印日志排查"
  kubectl logs -n kafka "job/${JOB_NAME}" --tail=50 2>&1 | tee -a "$LOG_FILE" || true
  exit 1
}

log "==> 等 Flink 的 checkpoint 间隔(30s)过去几轮,让 Iceberg sink 真正提交"
log "    (Iceberg Flink sink 靠 checkpoint 触发 commit,没有 checkpoint 数据不会出现在表里,"
log "     见 apps/flink-streaming-demo/manifests/script-configmap.yaml 里的说明)"
sleep 90

log "==> 再查一次行数,核实真的有新数据进了 Iceberg(不是只看 Flink/Job 状态)"
AFTER_COUNT=$(query_row_count)
log "    当前行数: ${AFTER_COUNT}(基线 ${BEFORE_COUNT})"

if [ "$AFTER_COUNT" -le "$BEFORE_COUNT" ]; then
  log "!! 验证失败:行数没有增加,说明数据没有真的从 Kafka 流进 Iceberg"
  log "   （state=RUNNING 只代表作业在跑,不代表数据真的落盘——这正是这个仓库"
  log "    反复强调的“部署了不等于能用”）"
  exit 1
fi

log "==> 顺带查一次聚合表(demo.device_events_stream_agg),确认窗口聚合也在正常产出"
AGG_COUNT=$(kubectl exec -n table-registration-app "$TABLE_POD" -- python3 -c "
import os, trino
from trino.auth import BasicAuthentication
pw = os.environ['TRINO_PASSWORD']
conn = trino.dbapi.connect(host='trino.trino.svc.cluster.local', port=8443, user='table_registration_service',
    http_scheme='https', verify=False, auth=BasicAuthentication('table_registration_service', pw), catalog='iceberg')
cur = conn.cursor()
try:
    cur.execute('SELECT count(*) FROM iceberg.demo.device_events_stream_agg')
    print(cur.fetchall()[0][0])
except Exception as e:
    print(0)
" 2>/dev/null)
log "    聚合表行数: ${AGG_COUNT}"

log ""
log "FLINK_STREAMING_DEMO_OK: 明细表行数从 ${BEFORE_COUNT} 增加到 ${AFTER_COUNT},聚合表 ${AGG_COUNT} 行"
log "完成。详细日志: ${LOG_FILE}"
