#!/usr/bin/env bash
# 幂等地生成/创建平台底座和 Phase 1 组件需要的管理员账号 Secret。
# 不走 GitOps 是故意的:密码不该出现在 Git 历史里(尤其这是公开仓库),
# 由运维在拉起集群前手动跑一次这个脚本,后续组件的 Application 通过
# `existingSecret` / secretKeyRef 引用这些 Secret,只在 git 里出现 Secret
# 的名字,不出现值。
#
# 幂等:已存在的 Secret 不会被覆盖(不会意外轮换密码破坏已有连接)。
# 想真正轮换某个密码,先手动删除对应 Secret 再重新跑这个脚本。
#
# 用法:
#   ./scripts/00-generate-secrets.sh [输出凭据的文件路径,默认 secrets/generated-credentials.txt]
set -euo pipefail

OUT_FILE="${1:-secrets/generated-credentials.txt}"
mkdir -p "$(dirname "$OUT_FILE")"

gen_password() {
  openssl rand -base64 18 | tr -d '/+=' | cut -c1-20
}

ensure_ns() {
  kubectl create namespace "$1" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
}

# ensure_secret <namespace> <secret名> <key1=value1-or-random> [key2=...]
# 值传 "RANDOM" 表示自动生成随机密码,传字面量则原样使用(比如固定的 username)。
ensure_secret() {
  local ns="$1" name="$2"
  shift 2
  if kubectl -n "$ns" get secret "$name" >/dev/null 2>&1; then
    echo "已存在,跳过: ${ns}/${name}"
    return
  fi
  local args=()
  local record=("${ns}/${name}")
  for kv in "$@"; do
    local key="${kv%%=*}"
    local val="${kv#*=}"
    if [ "$val" = "RANDOM" ]; then
      val="$(gen_password)"
    fi
    args+=(--from-literal="${key}=${val}")
    record+=("${key}=${val}")
  done
  kubectl -n "$ns" create secret generic "$name" "${args[@]}"
  echo "已创建: ${ns}/${name}"
  echo "${record[*]}" >> "$OUT_FILE"
}

# copy_secret <源namespace> <目标namespace> <secret名>
# k8s Secret 是按命名空间隔离的,跨命名空间不能直接引用(踩过一次坑:Trino
# 连 MinIO 时 secretKeyRef 指向 minio 命名空间的 minio-root,报 not found)。
# 每多一个需要连 MinIO 的组件,就在下面 MINIO_CONSUMER_NAMESPACES 里加它的
# 命名空间,这个函数负责把凭据复制过去,保持一份来源(minio-root)、多份副本。
copy_secret() {
  local src_ns="$1" dst_ns="$2" name="$3"
  if kubectl -n "$dst_ns" get secret "$name" >/dev/null 2>&1; then
    echo "已存在,跳过: ${dst_ns}/${name}(复制自 ${src_ns})"
    return
  fi
  kubectl -n "$src_ns" get secret "$name" -o json \
    | python3 -c "
import json,sys
d = json.load(sys.stdin)
out = {'apiVersion':'v1','kind':'Secret','type':d.get('type','Opaque'),
       'metadata':{'name':d['metadata']['name'],'namespace':'$dst_ns'},
       'data':d.get('data',{})}
print(json.dumps(out))
" | kubectl apply -f - >/dev/null
  echo "已复制: ${src_ns}/${name} -> ${dst_ns}/${name}"
}

echo "==> 建命名空间"
for ns in keycloak monitoring minio data airflow trino; do
  ensure_ns "$ns"
done

echo "==> 生成/创建 Secret(已存在的不会重新生成,不会轮换密码)"
echo "# $(date -u +%FT%TZ) 生成的凭据,不要提交到 git" >> "$OUT_FILE"

ensure_secret keycloak    keycloak-admin    username=admin    password=RANDOM
ensure_secret monitoring  grafana-admin     admin-user=admin  admin-password=RANDOM
ensure_secret minio       minio-root        rootUser=admin    rootPassword=RANDOM
ensure_secret data        postgres-root     username=postgres password=RANDOM
ensure_secret data        hive-metastore-db username=hive     password=RANDOM
ensure_secret data        airflow-db        username=airflow  password=RANDOM
ensure_secret airflow     airflow-webserver-admin username=admin password=RANDOM

# Airflow 的几个密钥格式有特殊要求,不能用通用的 ensure_secret 随便生成:
# - fernet-key 必须是 urlsafe-base64 编码的 32 字节(Fernet.generate_key() 格式)
# - metadata 连接串依赖 airflow-db 的密码,要在那个 Secret 建好之后再拼
if kubectl -n airflow get secret airflow-fernet-key >/dev/null 2>&1; then
  echo "已存在,跳过: airflow/airflow-fernet-key"
else
  FERNET_KEY="$(openssl rand -base64 32 | tr '+/' '-_')"
  kubectl -n airflow create secret generic airflow-fernet-key --from-literal=fernet-key="$FERNET_KEY"
  echo "已创建: airflow/airflow-fernet-key"
fi

for s in airflow-api-secret:api-secret-key airflow-jwt-secret:jwt-secret; do
  name="${s%%:*}"; key="${s##*:}"
  if kubectl -n airflow get secret "$name" >/dev/null 2>&1; then
    echo "已存在,跳过: airflow/${name}"
  else
    kubectl -n airflow create secret generic "$name" --from-literal="${key}=$(gen_password)"
    echo "已创建: airflow/${name}"
  fi
done

if kubectl -n airflow get secret airflow-metadata >/dev/null 2>&1; then
  echo "已存在,跳过: airflow/airflow-metadata"
else
  AF_DB_PW=$(kubectl -n data get secret airflow-db -o jsonpath='{.data.password}' | base64 -d)
  CONN="postgresql+psycopg2://airflow:${AF_DB_PW}@postgres.data.svc.cluster.local:5432/airflow"
  kubectl -n airflow create secret generic airflow-metadata --from-literal=connection="$CONN"
  echo "已创建: airflow/airflow-metadata"
fi

echo "==> 复制 MinIO 凭据到需要连它的命名空间"
MINIO_CONSUMER_NAMESPACES="trino"
for ns in $MINIO_CONSUMER_NAMESPACES; do
  copy_secret minio "$ns" minio-root
done

echo
echo "完成。新生成的凭据(如果有)已追加到: ${OUT_FILE}"
echo "这个文件不会被提交到 git(在 .gitignore 里),自己保管好。"
