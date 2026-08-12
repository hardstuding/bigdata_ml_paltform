#!/usr/bin/env bash
# 从 MinIO 里的 postgres-backup(见 apps/postgres-backup/)恢复一份备份。
#
# 刻意不自动化、不做成"选最新的自动恢复"——恢复是会覆盖当前数据的破坏性
# 操作,必须由人明确选哪一份、确认要恢复,不应该有任何脚本替你做这个决定。
#
# 用法:
#   ./scripts/restore-postgres-backup.sh              # 列出所有可用备份
#   ./scripts/restore-postgres-backup.sh <备份文件名>   # 恢复指定的那一份
set -euo pipefail
cd "$(dirname "$0")/.."

MINIO_USER=$(kubectl get secret -n minio minio-root -o jsonpath='{.data.rootUser}' | base64 -d)
MINIO_PASSWORD=$(kubectl get secret -n minio minio-root -o jsonpath='{.data.rootPassword}' | base64 -d)
PG_USER=$(kubectl get secret -n data postgres-root -o jsonpath='{.data.username}' | base64 -d)
PG_PASSWORD=$(kubectl get secret -n data postgres-root -o jsonpath='{.data.password}' | base64 -d)

if [ $# -eq 0 ]; then
  echo "可用备份(列的是 MinIO backups/postgres/ 下的文件,不是本地文件):"
  kubectl run pg-backup-list --rm -i --restart=Never --image=minio/mc:RELEASE.2025-08-13T08-35-41Z -- sh -c "
    mc alias set backupminio http://minio.minio.svc.cluster.local:9000 '${MINIO_USER}' '${MINIO_PASSWORD}' >/dev/null
    mc ls backupminio/backups/postgres/
  " 2>&1 | grep -v "^pod \|^If you don't see"
  echo
  echo "用法: $0 <上面列出的文件名>"
  exit 0
fi

BACKUP_FILE="$1"
echo "!!! 即将用 ${BACKUP_FILE} 覆盖当前 Postgres 数据 !!!"
echo "这会影响 Keycloak/Hive Metastore/MLflow/Airflow/Superset 等所有共用这个 Postgres 实例的组件。"
read -r -p "确认继续?输入 yes 继续,其他任意输入退出: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
  echo "取消。"
  exit 1
fi

echo "==> 从 MinIO 下载备份到本机临时文件"
TMPFILE=$(mktemp /tmp/postgres-restore-XXXXXX.sql.gz)
trap 'rm -f "$TMPFILE"' EXIT

kubectl run pg-backup-download --rm -i --restart=Never --image=minio/mc:RELEASE.2025-08-13T08-35-41Z -- sh -c "
  mc alias set backupminio http://minio.minio.svc.cluster.local:9000 '${MINIO_USER}' '${MINIO_PASSWORD}' >/dev/null
  mc cat backupminio/backups/postgres/${BACKUP_FILE}
" > "$TMPFILE" 2>/dev/null

echo "==> 下载完成: $TMPFILE ($(du -h "$TMPFILE" | cut -f1))"
echo "==> port-forward 到 Postgres"
kubectl port-forward -n data svc/postgres 15432:5432 >/tmp/pg-restore-pf.log 2>&1 &
PF_PID=$!
trap 'kill $PF_PID 2>/dev/null; rm -f "$TMPFILE"' EXIT
sleep 3

echo "==> 执行恢复(psql 读 gunzip 出来的 SQL,pg_dumpall 的输出本身就是幂等的 SQL 脚本)"
gunzip -c "$TMPFILE" | PGPASSWORD="$PG_PASSWORD" psql -h localhost -p 15432 -U "$PG_USER" -d postgres

echo "==> 完成。建议接下来手动检查几个关键组件(Keycloak 能不能登录、Trino 数据源还在不在)确认恢复符合预期。"
