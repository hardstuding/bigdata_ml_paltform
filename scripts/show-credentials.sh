#!/usr/bin/env bash
# 从**集群**读当前有效的凭据,而不是去翻 secrets/generated-credentials.txt。
#
# **为什么需要这个**(2026-08-27 实测):那个文件是 `scripts/00-generate-secrets.sh`
# 每次运行**追加**出来的,于是:
#
#   - **同一个键攒了多份,只有最后一份有效。** 实测 `trino/superset_service`
#     两条里只有第 2 条能对上集群;
#   - **同一个键最多攒到 4 份。** `trino/trino-internal-secret` 就有 4 条,
#     只有最后一条对得上集群;
#     (顺带记一个教训:第一次查这条时我用临时正则取值,把行里的 `secret=`
#     前缀也当成值的一部分,于是得出"4 条全是死的"这个**错误结论**。用
#     `--audit-file` 这条统一的解析路径才对。**一次性写的解析代码,比它要
#     检查的数据更容易出错。**)
#   - **不完整。** 集群里 115 个非系统 secret,文件里只有 17 个键;
#     `data/postgres-root` 集群里有、文件里根本没有。
#
# 也就是说:拿那个文件去登录,很可能拿到一个死密码,然后开始怀疑是不是
# 账号配错了——这类"看着有答案、其实是过期答案"的坑,比没有答案更费时间。
#
# 集群里的 Secret 才是唯一权威。这个脚本就是去读它。
#
# 用法:
#   ./scripts/show-credentials.sh              # 只列有哪些、指纹、缺不缺,**不打印明文**
#   ./scripts/show-credentials.sh --show       # 打印明文(慎用,会留在终端历史里)
#   ./scripts/show-credentials.sh --show trino # 只看某个关键字相关的
#   ./scripts/show-credentials.sh --audit-file # 逐行检查 secrets/generated-credentials.txt
#                                              # 里哪些条目已经失效(不打印任何明文)
#   ./scripts/show-credentials.sh --write-pruned  # 同上,并把"只保留有效条目"的结果
#                                              # 写到 secrets/generated-credentials.pruned.txt
#                                              # **绝不改原文件**,你自己比对完再决定替换
#
# **刻意不写日志文件。** 这个仓库其它脚本都往 logs/ 里 tee,这个不能——
# 那等于把明文凭据又落一份盘,正是要解决的问题本身。
set -euo pipefail
cd "$(dirname "$0")/.."

SHOW=0
AUDIT=0
PRUNE=0
FILTER=""
for arg in "$@"; do
  case "$arg" in
    --show) SHOW=1 ;;
    --audit-file) AUDIT=1 ;;
    --write-pruned) AUDIT=1; PRUNE=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) FILTER="$arg" ;;
  esac
done

# 平台自己生成/管理的凭据清单:namespace secret key 说明
# 新增组件时在这里加一行。**不自动扫全部 Secret**,因为那会把各 chart 自己
# 生成的一堆内部凭据也列出来,噪音淹掉真正要用的那几个。
read -r -d '' CREDS <<'EOS' || true
argocd|argocd-initial-admin-secret|password|ArgoCD 管理员
keycloak|keycloak-admin|password|Keycloak 管理员(realm 管理入口)
monitoring|grafana-admin|admin-password|Grafana 管理员
minio|minio-root|rootPassword|MinIO 根账号(对象存储)
data|postgres-root|password|Postgres 超级用户
trino|trino-service-account|password-superset_service|Trino:Superset 用
trino|trino-service-account|password-table_registration_service|Trino:建表工具用
trino|trino-service-account|password-dbt_demo_service|Trino:dbt 用
trino|trino-service-account|password-platform_sdk_demo_service|Trino:platform_sdk 用
trino|trino-service-account|password-openmetadata_service|Trino:OpenMetadata 采集用
trino|trino-service-account|password-goldenpath_probe|Trino:黄金链路探针用
trino|trino-internal-secret|secret|Trino 节点间通信密钥
openmetadata|openmetadata-postgresql-secrets|openmetadata-postgresql-password|OpenMetadata 的库
openmetadata|opensearch-admin|password|OpenSearch 管理员
EOS

printf "%-46s %-10s %s\n" "凭据" "状态" "说明"
printf "%-46s %-10s %s\n" "$(printf '%.0s-' {1..46})" "----------" "----------------"

missing=0
while IFS='|' read -r ns sec key desc; do
  [ -n "$ns" ] || continue
  label="${ns}/${sec}:${key}"
  [ -z "$FILTER" ] || case "$label $desc" in *"$FILTER"*) ;; *) continue ;; esac
  val="$(kubectl -n "$ns" get secret "$sec" -o "jsonpath={.data.${key}}" 2>/dev/null || true)"
  if [ -z "$val" ]; then
    printf "%-46s %-10s %s\n" "$label" "缺失" "$desc"
    missing=$((missing+1))
    continue
  fi
  plain="$(echo "$val" | base64 -d 2>/dev/null || true)"
  if [ "$SHOW" = "1" ]; then
    printf "%-46s %-10s %s\n" "$label" "$plain" "$desc"
  else
    fp="$(printf '%s' "$plain" | shasum -a 256 | cut -c1-8)"
    printf "%-46s %-10s %s\n" "$label" "$fp" "$desc"
  fi
done <<< "$CREDS"

echo
if [ "$SHOW" = "1" ]; then
  echo "上面是**当前集群里真正有效**的值。注意它们现在留在你的终端历史里了。"
else
  echo "上面显示的是指纹(sha256 前 8 位),不是密码。要明文加 --show。"
  echo "指纹的用处:和 secrets/generated-credentials.txt 里的条目比对,一眼看出哪条还有效。"
fi
[ "$missing" = "0" ] || echo "!! 有 ${missing} 条读不到——可能是组件没启用,也可能是 scripts/00 还没跑过。"

if [ "$AUDIT" = "1" ]; then
  echo
  echo "=== 逐行检查 secrets/generated-credentials.txt ==="
  echo "(只比对指纹,不打印任何明文)"
  PRUNE="$PRUNE" python3 - <<'PYEOF'
import base64, hashlib, pathlib, re, subprocess

f = pathlib.Path("secrets/generated-credentials.txt")
if not f.exists():
    print("  没有这个文件,不用清理。")
    raise SystemExit(0)

def live(ns, sec, key):
    r = subprocess.run(["kubectl", "-n", ns, "get", "secret", sec, "-o", f"jsonpath={{.data.{key}}}"],
                       capture_output=True, text=True)
    if r.returncode or not r.stdout:
        return None
    try:
        return base64.b64decode(r.stdout).decode(errors="ignore")
    except Exception:
        return None

# 文件里的行形如 `trino/superset_service: xxx` 或 `ns/secret key=v password=v`。
# 只处理能明确对应到集群 Secret 的那几类,其余原样报"没法自动判断"。
KNOWN = {
    "trino/superset_service": ("trino", "trino-service-account", "password-superset_service"),
    "trino/table_registration_service": ("trino", "trino-service-account", "password-table_registration_service"),
    "trino/dbt_demo_service": ("trino", "trino-service-account", "password-dbt_demo_service"),
    "trino/platform_sdk_demo_service": ("trino", "trino-service-account", "password-platform_sdk_demo_service"),
    "trino/openmetadata_service": ("trino", "trino-service-account", "password-openmetadata_service"),
    "trino/trino-internal-secret": ("trino", "trino-internal-secret", "secret"),
    "minio/minio-root": ("minio", "minio-root", "rootPassword"),
    "data/postgres-root": ("data", "postgres-root", "password"),
    "monitoring/grafana-admin": ("monitoring", "grafana-admin", "admin-password"),
    "keycloak/keycloak-admin": ("keycloak", "keycloak-admin", "password"),
    "trino/goldenpath_probe": ("trino", "trino-service-account", "password-goldenpath_probe"),
    "keycloak/keycloak-db": ("keycloak", "keycloak-db", "password"),
    "data/hive-metastore-db": ("data", "hive-metastore-db", "password"),
    "data/airflow-db": ("data", "airflow-db", "password"),
    "data/superset-db": ("data", "superset-db", "password"),
    "airflow/airflow-webserver-admin": ("airflow", "airflow-webserver-admin", "password"),
    "permission-request-app/permission-request-app-internal":
        ("permission-request-app", "permission-request-app-internal", "password"),
}
cache = {}
stale = fresh = unknown = 0
stale_lines = set()
for i, line in enumerate(f.read_text().splitlines(), 1):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    m = re.match(r"^([a-z][a-z0-9-]*/[A-Za-z0-9_-]+)[: ]+(\S.*)$", line)
    if not m:
        continue
    label, rest = m.group(1), m.group(2)
    if label not in KNOWN:
        unknown += 1
        print(f"  第{i:3d} 行  {label:44s} 没法自动判断(不在已知清单里)")
        continue
    if label not in cache:
        cache[label] = live(*KNOWN[label])
    cur = cache[label]
    val = rest.split()[-1].split("=")[-1]
    if cur and val == cur:
        fresh += 1
        print(f"  第{i:3d} 行  {label:44s} 仍然有效")
    else:
        stale += 1
        stale_lines.add(i)
        print(f"  第{i:3d} 行  {label:44s} **已失效**")

print()
print(f"  有效 {fresh} 条 / 已失效 {stale} 条 / 没法自动判断 {unknown} 条")

import os
if os.environ.get("PRUNE") == "1":
    # **只写新文件,绝不改原文件。** 凭据文件删错了没法撤销,而且"哪条有效"
    # 的判断依赖当前连的是哪套集群——把决定权留给人。
    out = f.with_suffix(".pruned.txt")
    kept = []
    for i, line in enumerate(f.read_text().splitlines(), 1):
        if i in stale_lines:
            continue
        kept.append(line)
    out.write_text("\n".join(kept) + "\n")
    print(f"\n  已写出 {out}(去掉了 {len(stale_lines)} 行失效条目,原文件一个字没动)")
    print("  比对一下再决定要不要替换:")
    print(f"    diff {f} {out}")
if stale:
    print("  已失效的那些行可以删掉——它们是历史上某次生成、后来被覆盖的密码。")
    print("  **删之前先确认这台集群就是你要的那台**:换一套集群的话,\"失效\"只是")
    print("  说明那台集群没有这个值,不代表这条记录没用。")
PYEOF
fi
