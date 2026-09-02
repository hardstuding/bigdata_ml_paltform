#!/usr/bin/env bash
# 端到端验证 Superset 的告警链路,**验证的是收件端真的收到了什么形状的消息**,
# 不是"告警状态显示成功"。
#
# 为什么需要这个脚本(ADR-091):告警是一条五环链 ——
# 特性开关 → beat → Redis broker → worker → 通知实现。任何一环缺失,界面上
# 的表现都是"告警不触发"或者干脆"看着触发了",而看不出缺的是哪一环。
# 2026-09-02 第一次跑这个验证,当场抓到两个问题:
#   - worker 因为 celery 默认并发=CPU核数(16)反复 OOM,加内存治不好
#   - 企微那段二开一行都没跑过(插件按注册顺序取第一个,子类永远轮不到)
# **两个都是"看状态看不出来"的**,只有比对收件端拿到的字节才发现。
#
# 做法:在 superset 命名空间起一个一次性的 HTTP sink,建一条每分钟执行的
# 告警指向它,等两个周期,然后核对收到的 payload。跑完把 sink 和告警都删掉。
#
# 用法:
#   ./scripts/55-verify-superset-alerting.sh
#   KEEP=1 ./scripts/55-verify-superset-alerting.sh   # 留下 sink 和告警便于排查
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/verify-superset-alerting.log"
echo "=== verify-superset-alerting $(date -u +%FT%TZ) ===" >> "$LOG_FILE"
ALERT_NAME="端到端验证告警(scripts/55)"

# **选择器必须带 component=web**,否则会选到 worker/beat,那两个 pod 里
# 没有名为 superset 的容器,exec 直接失败。
SS_POD=$(kubectl get pod -n superset \
  -l app.kubernetes.io/name=superset,app.kubernetes.io/component=web \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
[ -n "${SS_POD:-}" ] || { echo "!! 没有 Running 的 superset web pod" | tee -a "$LOG_FILE"; exit 1; }
IMG=$(kubectl -n superset get deploy superset -o jsonpath='{.spec.template.spec.containers[0].image}')

cleanup() {
  if [ "${KEEP:-0}" = "1" ]; then
    echo "--> KEEP=1,保留 sink 和告警" | tee -a "$LOG_FILE"; return
  fi
  echo "--> 清理临时资源" | tee -a "$LOG_FILE"
  kubectl -n superset delete deploy/alert-verify-sink svc/alert-verify-sink \
    cm/alert-verify-sink-code --ignore-not-found >/dev/null 2>&1 || true
  kubectl exec -i -n superset "$SS_POD" -c superset -- python - <<PYCLEAN >/dev/null 2>&1 || true
from superset.app import create_app
app = create_app()
with app.app_context():
    from superset.extensions import db
    from superset.reports.models import ReportSchedule
    rs = db.session.query(ReportSchedule).filter_by(name="${ALERT_NAME}").first()
    if rs:
        db.session.delete(rs); db.session.commit()
PYCLEAN
}
trap cleanup EXIT

echo "--> 起一次性 HTTP sink(用 Superset 自己的镜像,不引入外部依赖)" | tee -a "$LOG_FILE"
kubectl apply -f - >>"$LOG_FILE" 2>&1 <<EOF
apiVersion: v1
kind: ConfigMap
metadata: {name: alert-verify-sink-code, namespace: superset}
data:
  sink.py: |
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            print("RECEIVED", body.decode("utf-8", "replace"), flush=True)
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.end_headers(); self.wfile.write(b'{"errcode":0,"errmsg":"ok"}')
        def log_message(self, *a): pass
    HTTPServer(("0.0.0.0", 8000), H).serve_forever()
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: alert-verify-sink, namespace: superset}
spec:
  replicas: 1
  selector: {matchLabels: {app: alert-verify-sink}}
  template:
    metadata: {labels: {app: alert-verify-sink}}
    spec:
      containers:
      - name: sink
        image: ${IMG}
        command: ["python", "/code/sink.py"]
        volumeMounts: [{name: code, mountPath: /code}]
        resources: {requests: {cpu: 20m, memory: 64Mi}, limits: {memory: 256Mi}}
      volumes: [{name: code, configMap: {name: alert-verify-sink-code}}]
---
apiVersion: v1
kind: Service
metadata: {name: alert-verify-sink, namespace: superset}
spec:
  selector: {app: alert-verify-sink}
  ports: [{port: 8000, targetPort: 8000}]
EOF
kubectl -n superset rollout status deploy/alert-verify-sink --timeout=180s >>"$LOG_FILE" 2>&1

echo "--> 建告警(每分钟,条件恒真)" | tee -a "$LOG_FILE"
kubectl exec -i -n superset "$SS_POD" -c superset -- python - <<PY 2>&1 | grep -E "^\[验证\]" | tee -a "$LOG_FILE"
from superset.app import create_app
app = create_app()
with app.app_context():
    from superset.extensions import db
    from superset.models.core import Database
    from superset.models.dashboard import Dashboard
    from superset.reports.models import (ReportSchedule, ReportScheduleType, ReportDataFormat,
        ReportRecipients, ReportRecipientType, ReportScheduleValidatorType)
    from superset import security_manager as sm

    dbs = db.session.query(Database).all()
    target = next((d for d in dbs if "trino" in d.database_name.lower()), dbs[0])
    # **告警必须关联一个看板或图表。** 不关联的话执行会在拼"在 Superset 里
    # 查看"那个链接时抛 'NoneType' object has no attribute 'id' —— 报错完全
    # 指不到真正的原因。UI 上这是必填项,所以只有脚本建的会踩到。
    dash = db.session.query(Dashboard).order_by(Dashboard.id).first()
    if dash is None:
        raise SystemExit("[验证] !! 一个看板都没有,先跑 scripts/08-create-demo-data.sh")

    rs = db.session.query(ReportSchedule).filter_by(name="${ALERT_NAME}").first()
    if rs is None:
        rs = ReportSchedule(name="${ALERT_NAME}", type=ReportScheduleType.ALERT)
        db.session.add(rs)
    rs.description = "scripts/55 的一次性验证,脚本结束时会自己删掉"
    rs.crontab = "* * * * *"
    rs.database = target
    rs.sql = "SELECT 1"
    rs.validator_type = ReportScheduleValidatorType.OPERATOR
    rs.validator_config_json = '{"op": ">", "threshold": 0}'
    # **TEXT 而不是 PNG。** 镜像里只有 selenium 库、没有 chromedriver/
    # chromium,PNG/PDF 的告警会一直卡在 Working 直到 working_timeout,
    # 而且不报错。带截图的告警要另做镜像,见 production-readiness-gaps.md。
    rs.report_format = ReportDataFormat.TEXT
    rs.dashboard = dash
    rs.active = True
    rs.working_timeout = 120
    rs.grace_period = 60
    rs.last_state = None
    rs.owners = [sm.find_user(username="admin") or sm.get_all_users()[0]]
    rs.recipients = [ReportRecipients(type=ReportRecipientType.WEBHOOK,
        recipient_config_json='{"target": "http://alert-verify-sink.superset.svc.cluster.local:8000/"}')]
    db.session.commit()
    print(f"[验证] 告警已建 id={rs.id} 数据源={target.database_name} 看板={dash.dashboard_title}")
PY

echo "--> 等两个调度周期(最多 4 分钟)" | tee -a "$LOG_FILE"
GOT=""
for _ in $(seq 1 24); do
  sleep 10
  GOT=$(kubectl -n superset logs deploy/alert-verify-sink --tail=1 2>/dev/null | grep '^RECEIVED' || true)
  [ -n "$GOT" ] && break
done

if [ -z "$GOT" ]; then
  echo "!! 4 分钟内 sink 没收到任何通知。按这个顺序查:" | tee -a "$LOG_FILE"
  echo "   1) report_execution_log 有没有新记录 → 没有的话是 beat 或 broker 的问题" | tee -a "$LOG_FILE"
  echo "   2) 有记录但状态一直是 Working → worker 卡住(截图?超时?)" | tee -a "$LOG_FILE"
  echo "   3) 状态是 Error → 看 worker 日志里的堆栈" | tee -a "$LOG_FILE"
  kubectl -n superset logs deploy/superset-worker --tail=40 >>"$LOG_FILE" 2>&1 || true
  exit 1
fi

echo "--> sink 收到通知了,核对形状" | tee -a "$LOG_FILE"
# **payload 走文件,不走管道。** 下面这段 python 的源码本身是用 heredoc
# 喂给 stdin 的,再往 stdin 里管一份 payload 会互相覆盖(2026-09-02 第一次
# 写就是这么错的,报的是 JSONDecodeError,和真正的原因毫无关系)。
PAYLOAD_FILE="$(mktemp)"
printf '%s' "${GOT#RECEIVED }" > "$PAYLOAD_FILE"
python3 - "$ALERT_NAME" "$PAYLOAD_FILE" <<'PYCHK' 2>&1 | tee -a "$LOG_FILE"
import json, sys
name = sys.argv[1]
raw = open(sys.argv[2], encoding="utf-8").read().strip()
d = json.loads(raw)
ok = True
def check(label, cond, extra=""):
    global ok
    print(f"  [{'✓' if cond else '✗'}] {label}{(' — ' + extra) if extra else ''}")
    ok = ok and cond
# 企微机器人只认这个形状。收到别的形状说明二开没生效 —— 上游的
# WebhookNotification 排在 plugins 前面,子类永远轮不到(ADR-091)。
check("payload 是企微 markdown 形状(说明二开真的生效了)",
      d.get("msgtype") == "markdown" and "markdown" in d,
      f"实际 msgtype={d.get('msgtype')!r},顶层字段={sorted(d)}")
content = d.get("markdown", {}).get("content", "")
check("内容里带告警名", name.split("(")[0] in content)
check("内容里带回 Superset 的链接", "](http" in content)
print("\n=== 告警链路端到端验证" + ("通过" if ok else "未通过") + " ===")
sys.exit(0 if ok else 1)
PYCHK
