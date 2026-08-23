#!/usr/bin/env bash
# 让"数据资产盘点"这条能力从"人工在 OpenMetadata UI 里手动登记表"变成
# "Trino 的 iceberg catalog 下所有 schema/表/字段自动被发现"——见
# docs/roles.md 里数据治理角色的缺口记录。
#
# 声明式怎么做的:不在 UI 上点。用 OpenMetadata 自己的 REST API(createOrUpdate
# 语义的 PUT)建/更新两个实体——一个 DatabaseService(名字固定叫 "trino",
# 复用 apps/table-registration-app/src/app.py 里 ensure_om_hierarchy_and_tags()
# 已经建出来的那个同名 service,不新建一个,避免同一个 Trino 在 OpenMetadata
# 目录里出现两份;那份原有配置只是给"手动 PUT 表/schema 实体"当挂载点用,
# 没有真实的认证信息,连不上 Trino,这次把它补成一份真的能连接、能被
# OpenMetadata 自己拿去扫描的完整连接配置)、一个 IngestionPipeline
# (metadata 类型,挂在这个 service 下)。这个脚本本身只需要跑一次(重跑是
# 幂等的,PUT 语义),不需要循环调用。
#
# 定期跑靠谁:**不是**我们自己再建一个 CronJob 或 Airflow DAG 去定时触发扫描
# ——ADR-015 已经决定 OpenMetadata 的采集编排用官方的
# pipelineServiceClientConfig.type: k8s 模式(apps/components/openmetadata.yaml
# 已经这样配了)。这个模式下,只要 IngestionPipeline 的 airflowConfig.
# scheduleInterval 带了 cron 表达式并且调过一次 /deploy 接口,OpenMetadata
# 后端自己会在 openmetadata 命名空间里建出对应的 k8s CronJob 长期按周期跑
# ——这是官方文档里 "Kubernetes Native Orchestrator" 这一页写的行为
# (docs.open-metadata.org/.../deployment/ingestion/kubernetes)。所以这个脚本
# 只是"一次性把配置声明进 OpenMetadata",periodic 执行本身是 OpenMetadata
# 自己的职责,不需要我们仿照 apps/iam-sync/ 的 CronJob 模式或者 Airflow DAG
# 模式再包一层——那两种模式解决的是"这个仓库自己的代码要定期跑",这次的
# "定期扫描 Trino"官方机制本来就是为这个场景设计的,绕开它自己造轮子反而
# 违背 ADR-015 的取舍(选 k8s 原生模式就是为了不用另外维护一个调度器)。
# 这次没有实机验证过 OpenMetadata 真的会建出这个 CronJob(见脚本末尾的
# 诚实标注),这是本轮离线交付最大的不确定项。
#
# 认证复用:和 scripts/27-configure-openmetadata-bot.sh 建的 ingestion-bot
# token 同一个,不新发明认证方式——但不是直接读 scripts/27 建的那两个 Secret
# (那两个是给 table-registration-app/permission-request-app 这两个 Pod 自己
# 消费的),而是 kubectl exec 进 table-registration-app 这个已经在跑的 Pod,
# 复用它容器里已经装好的 python3 + requests,以及它自己的 OPENMETADATA_TOKEN/
# OPENMETADATA_URL 环境变量(内容和 scripts/27 建的 token 是同一份 JWT)。
# 选这条路径而不是新起一个 Pod:OpenMetadata 自己的容器镜像里没有 python3/
# curl,只有 wget(scripts/27 注释里记录过);新起一个装了 python3+requests
# 的一次性 Pod 在这台云主机上又实测过容易卡在镜像拉取上(同样是 scripts/27
# 的注释)。table-registration-app 的镜像已经在这个集群上跑着、已经验证过
# 能连通 Trino 和 OpenMetadata 两边(它自己就是靠这两条连接工作的),复用它
# 是这三个选项里最不会引入新故障面的一个。这也意味着这个脚本依赖
# table-registration-app 这个组件已启用——cloud-full/prod 两个环境的
# enabled_components 里它和 openmetadata 已经同时打开,local-lite 没开
# openmetadata,不会触发这个依赖不满足的情况。
#
# 前置条件:
#   - openmetadata 命名空间的 Deployment 已经 Running
#   - table-registration-app 命名空间的 Pod 已经 Running,且已经有
#     table-registration-app-openmetadata 这个 Secret(scripts/27 建的)
#   - trino 命名空间的 trino-service-account 这个 Secret 里有
#     password-openmetadata_service 这个 key(scripts/00-generate-secrets.sh
#     的 ensure_trino_service_account openmetadata_service 建的)
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/configure-openmetadata-trino-ingestion.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

OM_NS="openmetadata"
TRA_NS="table-registration-app"
TRINO_NS="trino"

log "检查前置条件 ..."
if ! kubectl -n "$OM_NS" get deploy openmetadata >/dev/null 2>&1; then
  log "openmetadata 命名空间还没有 Deployment,跳过(组件应该还是 park 状态)。"
  exit 0
fi
if ! kubectl -n "$TRINO_NS" get secret trino-service-account -o jsonpath='{.data.password-openmetadata_service}' 2>/dev/null | grep -q .; then
  log "trino/trino-service-account 里还没有 openmetadata_service 这个账号,先重跑一遍 scripts/00-generate-secrets.sh。"
  exit 1
fi

TRA_POD="$(kubectl -n "$TRA_NS" get pod -l app=table-registration-app -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [ -z "$TRA_POD" ]; then
  log "table-registration-app 命名空间找不到 Running 的 Pod,跳过(这个脚本借用它的 python3+requests 调 OpenMetadata API,见脚本头部注释)。"
  exit 0
fi
if ! kubectl -n "$TRA_NS" get secret table-registration-app-openmetadata >/dev/null 2>&1; then
  log "table-registration-app 还没有 OPENMETADATA_TOKEN(scripts/27 建的 Secret 不存在),先跑 scripts/27-configure-openmetadata-bot.sh。"
  exit 0
fi
log "确认 ${TRA_NS}/${TRA_POD} 已经带着 OPENMETADATA_TOKEN,用它来调 OpenMetadata API。"

TRINO_PW="$(kubectl -n "$TRINO_NS" get secret trino-service-account -o jsonpath='{.data.password-openmetadata_service}' | base64 -d)"

PY_SCRIPT="$(mktemp)"
trap 'rm -f "$PY_SCRIPT"' EXIT
cat > "$PY_SCRIPT" <<'PYEOF'
import json
import os
import sys
import time

import requests

TRINO_PW = sys.argv[1]
TOKEN = os.environ["OPENMETADATA_TOKEN"]
BASE = os.environ.get("OPENMETADATA_URL", "http://openmetadata.openmetadata.svc.cluster.local:8585")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def om(method, path, body=None):
    resp = requests.request(method, f"{BASE}{path}", headers=HEADERS, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json() if resp.content else None


# 1. 补全/更新 "trino" 这个 DatabaseService 的连接配置。这个 service 之前
#    已经被 table-registration-app 的 ensure_om_hierarchy_and_tags() 建过一次
#    (createOrUpdate,PUT 语义,不会新建重名 service),但那份配置没有认证
#    信息,只是给手动 PUT 表实体当挂载点——这次用真实凭据覆盖掉。
#    verify: "False" 是因为 Trino 走的是 cert-manager 自签证书(见
#    apps/trino-tls/,docs/decisions/016),table-registration-app 自己连
#    Trino 时(app.py 的 trino.dbapi.connect)也是同样传 verify=False,这里
#    保持一致。**注意类型必须是布尔 false 不是字符串**(2026-08-23
#    实测踩过,见下面那段注释)——具体字段名 "verify" 是从 OpenMetadata 1.13.3 的
#    trinoConnection.json schema 源码核实的,不是猜的,但这条连接配置本身
#    这次没有在真实集群里跑通过一次扫描,离线状态下没法确认
#    OpenMetadata 内部真的会拿这个字段去跳过证书校验。
svc = om("PUT", "/api/v1/services/databaseServices", {
    "name": "trino",
    "serviceType": "Trino",
    "connection": {"config": {
        "type": "Trino",
        "scheme": "trino",
        "hostPort": "trino.trino.svc.cluster.local:8443",
        "username": "openmetadata_service",
        "authType": {"password": TRINO_PW},
        # **不要用顶层的 `verify` 字段。** 2026-08-23 实测两次:
        #   "verify": "False"  → CheckAccess 报 `invalid path: False`
        #   "verify": False    → 同样报 `invalid path: false`
        # 说明 OpenMetadata 这个字段的语义是 **CA 证书文件的路径**,不是
        # "要不要校验"的开关——不管传什么它都当路径去找文件。
        #
        # 正确做法是走 connectionArguments:这是个透传给底层 SQLAlchemy /
        # trino 客户端的通用 map,JSON 的 false 反序列化成 Python False,
        # 正好就是 trino 客户端 `verify` 参数认的那个布尔。这条和这个平台
        # 里已经跑通的那份 Trino 连接是同一个用法——
        # apps/table-registration-app/src/app.py 里就是
        # `trino.dbapi.connect(..., http_scheme="https", verify=False)`。
        #
        # Trino 走 https 是因为它的 OAuth2 认证硬性要求自己的 https 监听器
        # 打开(见 apps/trino-tls/),证书是 cert-manager 自签的,集群内这
        # 一跳不需要被信任,跳过校验是合理的。
        "connectionArguments": {"verify": False},
    }},
})
print(f"database service ready: {svc['fullyQualifiedName']} (id={svc['id']})")

# 2. 创建/更新 metadata ingestion pipeline。cron 定为每 6 小时一次
#    ("0 */6 * * *")——数据资产不是分钟级变化的东西,6 小时够新鲜度,也不会
#    给 Trino coordinator 加太多扫描负载。markDeletedTables=True:表被删掉之后
#    目录里也应该跟着标记删除,不然目录会越攒越多陈旧条目。
pipeline = om("PUT", "/api/v1/services/ingestionPipelines", {
    "name": "trino_metadata",
    "displayName": "Trino Metadata Ingestion",
    "pipelineType": "metadata",
    "service": {"id": svc["id"], "type": "databaseService"},
    "sourceConfig": {"config": {
        "type": "DatabaseMetadata",
        "markDeletedTables": True,
        "includeTables": True,
        "includeViews": True,
        "includeTags": True,
        "includeOwners": False,
    }},
    "airflowConfig": {"scheduleInterval": "0 */6 * * *"},
    "loggerLevel": "INFO",
})
print(f"ingestion pipeline ready: {pipeline['fullyQualifiedName']} (id={pipeline['id']})")

# 3. 部署——这一步之后 OpenMetadata 后端应该会在 openmetadata 命名空间里
#    建出一个跟着 scheduleInterval 跑的 k8s CronJob(k8s 原生编排模式,
#    ADR-015),这次没有实机确认过 CronJob 真的出现了,见脚本头部注释。
om("POST", f"/api/v1/services/ingestionPipelines/deploy/{pipeline['id']}")
print("deploy 请求已提交")

print(f"PIPELINE_ID={pipeline['id']}")
PYEOF

log "PUT DatabaseService(trino,补真实连接配置) + IngestionPipeline(trino_metadata,每 6 小时) + deploy ..."
RESULT="$(kubectl -n "$TRA_NS" exec -i "$TRA_POD" -- python3 - "$TRINO_PW" < "$PY_SCRIPT" 2>&1 | tee -a "$LOG_FILE")"

PIPELINE_ID="$(echo "$RESULT" | grep -oE 'PIPELINE_ID=[a-f0-9-]+' | cut -d= -f2 || true)"
if [ -z "$PIPELINE_ID" ]; then
  log "错误:没有拿到 ingestion pipeline 的 id,上面的输出里应该有具体报错。"
  exit 1
fi

log "完成。IngestionPipeline id=${PIPELINE_ID}。"
log "接下来跑 ./scripts/30-verify-openmetadata-trino-ingestion.sh 触发一次真实扫描并独立核实结果"
log "(不是只看这一步的返回码——deploy 成功不等于扫描真的跑成功过一次)。"
