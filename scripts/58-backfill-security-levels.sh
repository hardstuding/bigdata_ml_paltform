#!/usr/bin/env bash
# 给**采集发现的存量表**补上安全等级标签,否则它们永远申请不了权限。
#
# **问题是什么。** 权限申请门户按表在 OpenMetadata 里登记的安全等级算审批链
# (ADR-040/044);拿不到等级就直接拒:
#
#     在 OpenMetadata 里查不到这张表的安全等级(没登记过,或者
#     OPENMETADATA_TOKEN 没配置),请先用建表注册工具登记这张表
#
# 而等级是**建表注册工具在建表那一刻写进去的**(ADR-043)。采集器自动发现的
# 表没走过那条路,标签是空的 —— 2026-09-03 实测:目录里 200 张表,**带安全
# 等级标签的是 0 张**,也就是说「申请表权限」这条能力对现存的每一张表都
# 走不通,每次申请都被拒。
#
# 这不是 demo 环境特有的:接入任何一个已有的数据仓库,都会先有几百张没有
# 等级的表,然后才是新建的表走注册工具。**补登记是接入流程的一步**,不是
# 一次性的修补。
#
# **默认给 Level1,而不是更高。** 等级决定审批链的长度,给高了会让所有人
# 的申请都卡在多级审批上;给低了只是审批链短一级,而 Trino + OPA 那层的
# "没有 grant 就查不到"完全不受影响(ADR-051)。真正敏感的表应该由表负责人
# 显式调高 —— 这个脚本只负责让"没有等级"这个**阻断状态**消失。
#
# 幂等:已经有 SecurityLevel 标签的表跳过,不会覆盖别人设好的等级。
#
# 用法:
#   ./scripts/58-backfill-security-levels.sh                    # 全部,补 Level1
#   ./scripts/58-backfill-security-levels.sh demo               # 只补 demo schema
#   ./scripts/58-backfill-security-levels.sh demo 2             # 补成 Level2
#   DRY_RUN=1 ./scripts/58-backfill-security-levels.sh          # 只看会改哪些
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG="logs/backfill-security-levels.log"
echo "=== backfill-security-levels $(date -u +%FT%TZ) ===" >> "$LOG"

SCHEMA_FILTER="${1:-}"
LEVEL="${2:-1}"

# 借权限门户的 pod 跑 —— OPENMETADATA_TOKEN 和 URL 都在它的环境变量里,
# 不用在别处再配一份凭据(这个 token 是 27- 脚本取的 ingestion-bot token)。
POD=$(kubectl -n permission-request-app get pod \
  -l app=permission-request-app --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
[ -n "${POD:-}" ] || { echo "!! 找不到 Running 的 permission-request-app pod" | tee -a "$LOG"; exit 1; }

kubectl exec -i -n permission-request-app "$POD" -- python - \
  "$SCHEMA_FILTER" "$LEVEL" "${DRY_RUN:-0}" <<'PY' 2>&1 | tee -a "$LOG"
import json
import os
import sys
import urllib.request

schema_filter, level, dry = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
base = os.environ["OPENMETADATA_URL"]
tok = os.environ["OPENMETADATA_TOKEN"]


def call(method, path, body=None):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": "application/json-patch+json" if method == "PATCH"
                 else "application/json"},
        method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


tables, after = [], None
while True:
    q = f"/api/v1/tables?limit=200&fields=tags{'&after=' + after if after else ''}"
    page = call("GET", q)
    tables.extend(page["data"])
    after = (page.get("paging") or {}).get("after")
    if not after:
        break

todo = []
for t in tables:
    fqn = t["fullyQualifiedName"]
    if schema_filter and f".{schema_filter}." not in fqn:
        continue
    if any((tag.get("tagFQN") or "").startswith("SecurityLevel.")
           for tag in (t.get("tags") or [])):
        continue
    todo.append(t)

print(f"[补登记] 目录里 {len(tables)} 张表,其中需要补等级的 {len(todo)} 张"
      + (f"(只看 {schema_filter} schema)" if schema_filter else ""))
if dry:
    for t in todo[:20]:
        print("   会补:", t["fullyQualifiedName"])
    print("[补登记] DRY_RUN=1,没有真的改")
    raise SystemExit(0)

ok = fail = 0
for t in todo:
    # **用 JSON Patch 往 tags 里追加,不是整体 PUT 覆盖。**
    # PUT 要带上表的完整定义(列、描述、owner...),少一个字段就把它抹掉了;
    # 而这里只想加一个标签。
    patch = [{"op": "add", "path": "/tags/-",
              "value": {"tagFQN": f"SecurityLevel.Level{level}",
                        "source": "Classification",
                        "labelType": "Manual", "state": "Confirmed"}}]
    try:
        call("PATCH", f"/api/v1/tables/{t['id']}", patch)
        ok += 1
    except Exception as exc:  # noqa: BLE001
        fail += 1
        if fail <= 3:
            print(f"   !! {t['fullyQualifiedName']}: {type(exc).__name__} {exc}")

print(f"[补登记] 打上 Level{level} 的 {ok} 张,失败 {fail} 张")
raise SystemExit(1 if fail else 0)
PY
