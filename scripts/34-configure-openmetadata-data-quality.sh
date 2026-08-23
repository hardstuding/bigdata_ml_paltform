#!/usr/bin/env bash
# 给 Iceberg 表配数据质量断言,跑在 OpenMetadata 自带的 Data Quality 上。
#
# **为什么这件事排在生产可用性缺口的第一位**(见
# docs/production-readiness-gaps.md):作业挂了会告警、有人管;**数据错了
# 不会告警**——它一路流进报表和模型训练集,几周后业务方发现"这个数字不对"
# 时,已经回溯不出哪天开始错的、下游哪些结果被污染了。这类事故的挽回成本
# 比"任务失败"高一个数量级,而在这个脚本之前,平台层面一行防护都没有。
#
# **为什么复用 OpenMetadata 而不是引入 Great Expectations / Soda**
# (zhenghe 2026-08-23 提示"openmetadata 好像带有相关的功能,可能可以复用",
# 核实之后确认是对的):
#   1. 断言结果**要和数据目录长在一起**。分析师在查一张表之前就能看到"这张
#      表昨天的质量检查过没过",这是这套东西有没有用的关键——只把结果发进
#      告警通道的话,只有运维看得到,用数据的人看不到,等于白做。独立的
#      质量框架要额外做一层回写目录才能达到同样效果。
#   2. 执行编排已经有了。OpenMetadata 的 TestSuite 类型 IngestionPipeline
#      走的是和 scripts/29 那条 metadata 采集完全相同的 k8s 原生编排
#      (ADR-015),不用再引入一个调度器。
#   3. 连接配置已经有了。复用 scripts/29 建好的那个 "trino" DatabaseService,
#      不用再维护第二份 Trino 凭据。
#
# 代价要说清楚:断言只能用 OpenMetadata 内置的那 25 个 testDefinition
# (实机核对过数量),表达能力不如自己写 SQL。目前这三条(非空、主键唯一、
# 关键字段非空)覆盖的是最常见的事故类型,不是全部。真需要复杂断言时再
# 单独评估,不要因为"框架不够强"就现在推翻这个选择——先有防护比防护完美
# 重要得多。
#
# 认证/执行路径和 scripts/29 完全一致(kubectl exec 进 table-registration-app
# 复用它的 python3+requests 和 OPENMETADATA_TOKEN),理由见那个脚本头部。
#
# 前置条件:scripts/29 已经跑过并且 scripts/30 验证通过(目录里得先有
# trino.iceberg.demo.orders 这张表,断言是挂在表实体上的)。
#
# 幂等:测试套件和 pipeline 都是"有就复用",测试用例重复创建返回 409 会被
# 当成"已存在"跳过。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/configure-openmetadata-data-quality.log"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

OM_NS="openmetadata"
TRA_NS="table-registration-app"

if ! kubectl -n "$OM_NS" get deploy openmetadata >/dev/null 2>&1; then
  log "openmetadata 没部署,跳过(组件应该还是 park 状态)。"
  exit 0
fi
TRA_POD="$(kubectl -n "$TRA_NS" get pod -l app=table-registration-app -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [ -z "$TRA_POD" ]; then
  log "table-registration-app 没有 Running 的 Pod,跳过。"
  exit 0
fi

PY_SCRIPT="$(mktemp)"
trap 'rm -f "$PY_SCRIPT"' EXIT
cat > "$PY_SCRIPT" <<'PYEOF'
import os
import sys

import requests

TOKEN = os.environ["OPENMETADATA_TOKEN"]
BASE = os.environ.get("OPENMETADATA_URL", "http://openmetadata.openmetadata.svc.cluster.local:8585")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

TABLE_FQN = "trino.iceberg.demo.orders"
SUITE_FQN = f"{TABLE_FQN}.testSuite"


def om(method, path, body=None, ok=(200, 201)):
    resp = requests.request(method, f"{BASE}{path}", headers=HEADERS, json=body, timeout=60)
    if resp.status_code not in ok:
        raise RuntimeError(f"{method} {path} -> {resp.status_code} {resp.text[:400]}")
    return resp.json() if resp.content else None


# 表必须已经在目录里——断言是挂在表实体上的。目录里没有它,说明 scripts/29
# 的元数据采集还没跑过,这时候建断言只会建出一堆指向不存在实体的孤儿对象。
table = om("GET", f"/api/v1/tables/name/{TABLE_FQN}")
print(f"表已在目录里: {table['fullyQualifiedName']}")

# 1. 基础测试套件(basic test suite)。**1.13.3 的路径是 /basic 不是
#    /executable**(实测:/executable 返回 405 Method Not Allowed),而且
#    字段叫 basicEntityReference。已存在时 POST 会失败,直接改用 GET 拿。
suite = requests.get(f"{BASE}/api/v1/dataQuality/testSuites/name/{SUITE_FQN}",
                     headers=HEADERS, timeout=30)
if suite.status_code == 200:
    suite = suite.json()
    print(f"测试套件已存在: {suite['fullyQualifiedName']}")
else:
    suite = om("POST", "/api/v1/dataQuality/testSuites/basic",
               {"name": SUITE_FQN, "basicEntityReference": TABLE_FQN})
    print(f"测试套件已创建: {suite['fullyQualifiedName']}")

# 2. 断言。**注意 body 里不能带 testSuite 字段**——1.13.3 实测带上(不管是
#    字符串 FQN 还是 EntityReference 对象)一律返回 400 Invalid request
#    format,套件是从 entityLink 推断的。这类"少传一个字段反而对"的细节
#    没法从文档看出来,是实机试出来的。
CASES = [
    # 行数为零是最常见也最容易被忽略的事故:上游断供、分区路径写错、
    # 过滤条件写反,现象都是"任务成功但表是空的"。
    ("orders_row_count_not_empty", "tableRowCountToBeBetween",
     f"<#E::table::{TABLE_FQN}>",
     [{"name": "minValue", "value": "1"}, {"name": "maxValue", "value": "100000000"}]),
    # 主键重复 = 下游所有聚合数字翻倍,而且不会报错。
    ("orders_order_id_unique", "columnValuesToBeUnique",
     f"<#E::table::{TABLE_FQN}::columns::order_id>", []),
    # 金额字段变 null 是这个平台实测过的失效模式(ADR-062:Flink 的
    # ignore-parse-errors 会把解析失败静默变成 null)。
    ("orders_amount_not_null", "columnValuesToBeNotNull",
     f"<#E::table::{TABLE_FQN}::columns::amount>", []),
]

for name, definition, entity_link, params in CASES:
    body = {"name": name, "entityLink": entity_link,
            "testDefinition": definition, "parameterValues": params}
    resp = requests.post(f"{BASE}/api/v1/dataQuality/testCases",
                         headers=HEADERS, json=body, timeout=30)
    if resp.status_code in (200, 201):
        print(f"断言已创建: {name} ({definition})")
    elif resp.status_code == 409:
        print(f"断言已存在: {name}")
    else:
        raise RuntimeError(f"建断言 {name} 失败 -> {resp.status_code} {resp.text[:400]}")

# 3. 执行这批断言的 pipeline。和 scripts/29 的 metadata 采集错开半小时跑
#    (元数据采集在 0 分,这个在 30 分):质量检查读的是采集之后的目录状态,
#    同时跑没有意义,还会同时压 Trino。
pipeline = om("PUT", "/api/v1/services/ingestionPipelines", {
    "name": "orders_data_quality",
    "displayName": "orders 数据质量检查",
    "pipelineType": "TestSuite",
    "service": {"id": suite["id"], "type": "testSuite"},
    "sourceConfig": {"config": {"type": "TestSuite", "entityFullyQualifiedName": TABLE_FQN}},
    "airflowConfig": {"scheduleInterval": "30 */6 * * *"},
    "loggerLevel": "INFO",
})
print(f"质量检查 pipeline: {pipeline['fullyQualifiedName']}")

om("POST", f"/api/v1/services/ingestionPipelines/deploy/{pipeline['id']}")
print("deploy 请求已提交")
print(f"PIPELINE_ID={pipeline['id']}")
PYEOF

log "建测试套件 + 3 条断言 + TestSuite pipeline(每 6 小时,和元数据采集错开半小时)..."
RESULT="$(kubectl -n "$TRA_NS" exec -i "$TRA_POD" -- python3 - < "$PY_SCRIPT" 2>&1 | tee -a "$LOG_FILE")"
PIPELINE_ID="$(echo "$RESULT" | grep -oE 'PIPELINE_ID=[a-f0-9-]+' | cut -d= -f2 || true)"
[ -n "$PIPELINE_ID" ] || { log "错误:没拿到 pipeline id,上面输出里有具体报错。"; exit 1; }

log "完成。pipeline id=${PIPELINE_ID}"
log "接下来跑 ./scripts/35-verify-openmetadata-data-quality.sh 触发一次真实检查并核实结果"
log "(deploy 成功不等于断言真的跑过一次——这个项目被'看起来成功了'坑过太多次)。"
