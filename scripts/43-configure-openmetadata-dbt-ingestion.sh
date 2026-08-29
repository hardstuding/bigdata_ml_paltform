#!/usr/bin/env bash
# 让 OpenMetadata 消费 dbt 的产物,把 dbt 模型的血缘/描述/测试接进数据目录。
#
# **为什么现在才做**:`dbt_demo` 这个 Airflow DAG 从 2026-08-16 起就在往
# `s3://lakehouse/dbt-artifacts/platform_demo/` 上传 `manifest.json` 和
# `catalog.json`——路径就是 OpenMetadata 的 dbt 连接器期望的位置——但
# **没有任何东西去消费它们**(`docs/project/capability-matrix.md` 里如实记着这条)。也就是说
# 产物一直在生成、一直没人读,目录里的血缘那半一直是空的。
#
# 这个脚本补上消费端:在已有的 `trino` DatabaseService 上再挂一条
# `pipelineType: dbt` 的 IngestionPipeline,数据源指向 MinIO 上那两个文件。
#
# **字段名不是猜的**,是从 OpenMetadata 的 JSON Schema 源码核实的:
#   - metadataIngestion/dbtPipeline.json          → sourceConfig.config.type = "DBT"
#   - metadataIngestion/dbtconfig/dbtS3Config.json → dbtConfigType = "s3",
#                                                    dbtSecurityConfig / dbtPrefixConfig
#   - security/credentials/awsCredentials.json    → awsAccessKeyId / awsSecretAccessKey /
#                                                    awsRegion(**必填**)/ endPointURL
# MinIO 不是真的 AWS,`awsRegion` 用 us-east-1 这个 S3 客户端的通用默认值,
# 真正决定连到哪的是 `endPointURL`。
#
# 认证/执行路径和 scripts/29、34 完全一致(kubectl exec 进
# table-registration-app,借它的 python3+requests 和已经挂好的
# OPENMETADATA_TOKEN),不另起一套。
#
# 用法:./scripts/43-configure-openmetadata-dbt-ingestion.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/configure-openmetadata-dbt-ingestion.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

OM_NS="openmetadata"
TRA_NS="table-registration-app"
MINIO_NS="minio"

log "检查前置条件 ..."
if ! kubectl -n "$OM_NS" get deploy openmetadata >/dev/null 2>&1; then
  log "openmetadata 还没部署,跳过(组件应该还是 park 状态)。"
  exit 0
fi

TRA_POD="$(kubectl -n "$TRA_NS" get pod -l app=table-registration-app -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [ -z "$TRA_POD" ]; then
  log "table-registration-app 找不到 Running 的 Pod,跳过(这个脚本借它调 OpenMetadata API)。"
  exit 0
fi

MINIO_AK="$(kubectl -n "$MINIO_NS" get secret minio-root -o jsonpath='{.data.rootUser}' | base64 -d)"
MINIO_SK="$(kubectl -n "$MINIO_NS" get secret minio-root -o jsonpath='{.data.rootPassword}' | base64 -d)"
[ -n "$MINIO_AK" ] && [ -n "$MINIO_SK" ] || { log "读不到 minio/minio-root,先跑 scripts/00-generate-secrets.sh。"; exit 1; }

# dbt 产物在不在。**不先检查这个的话**,后面 deploy 会"成功",而采集任务
# 到点跑起来才报找不到文件——这个仓库被"看起来成功了"坑过太多次。
log "确认 dbt 产物真的在 MinIO 上 ..."
if ! kubectl -n "$MINIO_NS" exec deploy/minio -- sh -c \
  'mc alias set l http://localhost:9000 $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD >/dev/null 2>&1; mc ls --recursive l/lakehouse/dbt-artifacts/' 2>/dev/null \
  | tee -a "$LOG_FILE" | grep -q "manifest.json"; then
  log "!! s3://lakehouse/dbt-artifacts/ 下没有 manifest.json。"
  log "   先在 Airflow 里触发一次 dbt_demo DAG(它会跑 dbt build + dbt docs generate 并上传),再回来跑这个脚本。"
  exit 1
fi

PY_SCRIPT="$(mktemp)"
trap 'rm -f "$PY_SCRIPT"' EXIT
cat > "$PY_SCRIPT" <<'PYEOF'
import os
import sys

import requests

MINIO_AK, MINIO_SK = sys.argv[1], sys.argv[2]
TOKEN = os.environ["OPENMETADATA_TOKEN"]
BASE = os.environ.get("OPENMETADATA_URL", "http://openmetadata.openmetadata.svc.cluster.local:8585")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def om(method, path, body=None):
    resp = requests.request(method, f"{BASE}{path}", headers=HEADERS, json=body, timeout=30)
    if not resp.ok:
        print(f"!! {method} {path} -> {resp.status_code}\n{resp.text[:1500]}")
        resp.raise_for_status()
    return resp.json() if resp.content else None


# dbt 这条管道挂在已有的 trino DatabaseService 上(scripts/29 建的那个),
# 不新建 service —— dbt 模型最终落地成的就是 Trino 里的表,挂在别处会让
# 同一张表在目录里出现两次。
svc = om("GET", "/api/v1/services/databaseServices/name/trino")

pipeline = om("PUT", "/api/v1/services/ingestionPipelines", {
    "name": "trino_dbt",
    "displayName": "Trino dbt Ingestion",
    "pipelineType": "dbt",
    "service": {"id": svc["id"], "type": "databaseService"},
    "sourceConfig": {"config": {
        "type": "DBT",
        "dbtConfigSource": {
            "dbtConfigType": "s3",
            "dbtSecurityConfig": {
                # MinIO 不是真 AWS:awsRegion 是 schema 的必填项,给个 S3
                # 客户端的通用默认值就行,真正决定连到哪的是 endPointURL。
                "awsRegion": "us-east-1",
                "awsAccessKeyId": MINIO_AK,
                "awsSecretAccessKey": MINIO_SK,
                "endPointURL": "http://minio.minio.svc.cluster.local:9000",
            },
            "dbtPrefixConfig": {
                "dbtBucketName": "lakehouse",
                "dbtObjectPrefix": "dbt-artifacts/platform_demo",
            },
        },
        # 描述和 owner 不让 dbt 覆盖:表的负责人是 table-registration-app
        # 那条流程登记的(ADR-043),那是有审批的真实归属;dbt 里的 owner
        # 只是 yml 里随手写的字符串,覆盖过去等于用弱信息盖掉强信息。
        "dbtUpdateDescriptions": False,
        "dbtUpdateOwners": False,
        "includeTags": True,
    }},
    # 和元数据采集(0 */6)、数据质量(30 */6)都错开,不要三条同时抢资源
    # ——openmetadata 命名空间的内存配额是有限的(2026-08-28 才因为这个
    # 卡过一次,见 platform/resource-quotas/manifests/quotas.yaml)。
    "airflowConfig": {"scheduleInterval": "45 */6 * * *"},
    "loggerLevel": "INFO",
})
print(f"ingestion pipeline ready: {pipeline['fullyQualifiedName']} (id={pipeline['id']})")

om("POST", f"/api/v1/services/ingestionPipelines/deploy/{pipeline['id']}")
print("deploy 请求已提交")
print(f"PIPELINE_ID={pipeline['id']}")
PYEOF

log "PUT IngestionPipeline(trino_dbt,每 6 小时,和另外两条错开)+ deploy ..."
RESULT="$(kubectl -n "$TRA_NS" exec -i "$TRA_POD" -- python3 - "$MINIO_AK" "$MINIO_SK" < "$PY_SCRIPT" 2>&1 | tee -a "$LOG_FILE")"
PIPELINE_ID="$(echo "$RESULT" | grep -oE 'PIPELINE_ID=[a-f0-9-]+' | cut -d= -f2 || true)"
[ -n "$PIPELINE_ID" ] || { log "错误:没拿到 pipeline id,上面输出里有具体报错。"; exit 1; }

log "把 OpenMetadata 建的 CronJob 的 startingDeadlineSeconds 放大(理由见那个脚本)..."
"$(dirname "$0")/fix-openmetadata-cronjob-deadline.sh" 2>&1 | tee -a "$LOG_FILE"

log "完成。pipeline id=${PIPELINE_ID}"
log "接下来跑 ./scripts/44-verify-openmetadata-dbt-lineage.sh 触发一次真实采集并核实血缘真的进来了"
log "(deploy 成功不等于采集跑过一次,更不等于血缘真的建起来了)。"
