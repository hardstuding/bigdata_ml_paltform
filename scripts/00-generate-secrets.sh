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
for ns in keycloak monitoring minio data airflow trino superset openmetadata mlflow; do
  ensure_ns "$ns"
done

echo "==> 生成/创建 Secret(已存在的不会重新生成,不会轮换密码)"
echo "# $(date -u +%FT%TZ) 生成的凭据,不要提交到 git" >> "$OUT_FILE"

ensure_secret keycloak    keycloak-admin    username=admin    password=RANDOM
# 和 mlflow-db-secret 一个模式:直接建在 keycloak 命名空间,create-db-job 和
# keycloakx chart 自己都从这一份读,不用跨命名空间复制。
ensure_secret keycloak    keycloak-db       password=RANDOM
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

# Trino 开了 OAuth2 认证之后,即使是单节点 coordinator-only(没有独立
# worker),启动时也会强制校验 internal-communication.shared-secret 配了没有
# (报 "Shared secret is required when authentication is enabled"),不是可选项。
ensure_secret trino     trino-internal-secret secret=RANDOM

ensure_secret data superset-db username=superset password=RANDOM

# Superset chart 默认把 DB_USER/DB_PASS/SUPERSET_SECRET_KEY 这些当明文写进
# values(会进公开仓库的 git 历史)。改成建一个独立 Secret,通过覆盖 chart
# 的 envFromSecret(单数,chart 主 Secret 的名字)机制整个换成我们自己的,
# 不写死在 Application 的 valuesObject 里。REDIS_* 几个 key 是占位值——
# Redis/Celery 整体关掉了(local-lite 简化),但 wait-for-postgres 这类
# initContainer 的 envFrom 是硬编码引用这一个 Secret 的,缺了 key 数量不对
# 也无所谓,占位值不会被用到。
if kubectl -n superset get secret superset-db-secrets >/dev/null 2>&1; then
  echo "已存在,跳过: superset/superset-db-secrets"
else
  SUPERSET_DB_PW=$(kubectl -n data get secret superset-db -o jsonpath='{.data.password}' | base64 -d)
  SUPERSET_SECRET_KEY=$(openssl rand -base64 42)
  kubectl -n superset create secret generic superset-db-secrets \
    --from-literal=DB_USER=superset \
    --from-literal=DB_PASS="$SUPERSET_DB_PW" \
    --from-literal=DB_HOST=postgres.data.svc.cluster.local \
    --from-literal=DB_PORT=5432 \
    --from-literal=DB_NAME=superset \
    --from-literal=SUPERSET_SECRET_KEY="$SUPERSET_SECRET_KEY" \
    --from-literal=REDIS_HOST=unused \
    --from-literal=REDIS_PORT=6379 \
    --from-literal=REDIS_USER= \
    --from-literal=REDIS_PASSWORD= \
    --from-literal=REDIS_DB=1 \
    --from-literal=REDIS_CELERY_DB=0 \
    --from-literal=REDIS_PROTO=redis
  echo "已创建: superset/superset-db-secrets"
fi

# OpenMetadata 的 database.auth.password 引用这个 Secret,key 名字是
# chart 自己约定的 "openmetadata-postgresql-password",不能随便改。
if kubectl -n openmetadata get secret openmetadata-postgresql-secrets >/dev/null 2>&1; then
  echo "已存在,跳过: openmetadata/openmetadata-postgresql-secrets"
else
  OM_DB_PW="$(gen_password)"
  kubectl -n openmetadata create secret generic openmetadata-postgresql-secrets \
    --from-literal=openmetadata-postgresql-password="$OM_DB_PW"
  echo "已创建: openmetadata/openmetadata-postgresql-secrets"
fi

# OpenSearch 2.12+ 起,自带的 security 插件强制要求设置初始 admin 密码,不设
# 直接拒绝启动。同一个密码也要喂给 OpenMetadata 的 elasticsearch.auth 配置,
# 两边必须一致。
if kubectl -n openmetadata get secret opensearch-admin >/dev/null 2>&1; then
  echo "已存在,跳过: openmetadata/opensearch-admin"
else
  kubectl -n openmetadata create secret generic opensearch-admin \
    --from-literal=password="$(gen_password)A1!"
  echo "已创建: openmetadata/opensearch-admin"
fi

# MLflow chart 的 backendStoreUriFrom 要的是完整连接串(带 key "uri"),
# 不是分开的 host/user/pass,自己拼。密码单独存一份(key "password")给
# create-db-job 用,两边都从这一个 Secret 读,不重复生成密码。
if kubectl -n mlflow get secret mlflow-db-secret >/dev/null 2>&1; then
  echo "已存在,跳过: mlflow/mlflow-db-secret"
else
  MLFLOW_DB_PW="$(gen_password)"
  MLFLOW_DB_URI="postgresql://mlflow:${MLFLOW_DB_PW}@postgres.data.svc.cluster.local:5432/mlflow"
  kubectl -n mlflow create secret generic mlflow-db-secret \
    --from-literal=password="$MLFLOW_DB_PW" \
    --from-literal=uri="$MLFLOW_DB_URI"
  echo "已创建: mlflow/mlflow-db-secret"
fi

# MLflow 本身没有原生 OIDC/SSO 支持(开源版只有本地用户名密码的 basic-auth
# app,不接 Keycloak),接 SSO 用 oauth2-proxy 挡在前面(见
# apps/definitions/mlflow-oauth2-proxy.yaml)。cookie-secret 不能用
# gen_password(那个函数会剔除 +/= 字符,破坏 base64 编码,oauth2-proxy 要求
# 解码后正好是 16/24/32 字节,和当初 airflow-fernet-key 同样的坑)。
# client-id 不是真的密钥,但 chart 的 existingSecret 机制要求这三个 key 都在
# 同一个 Secret 里,直接存字面量 "mlflow"。client-secret 由
# 03-configure-keycloak.sh 建 Keycloak client 之后 patch 进来。
if kubectl -n mlflow get secret oauth2-proxy-secret >/dev/null 2>&1; then
  echo "已存在,跳过: mlflow/oauth2-proxy-secret"
else
  COOKIE_SECRET="$(openssl rand -base64 32)"
  kubectl -n mlflow create secret generic oauth2-proxy-secret \
    --from-literal=client-id=mlflow \
    --from-literal=cookie-secret="$COOKIE_SECRET" \
    --from-literal=client-secret=PLACEHOLDER
  echo "已创建: mlflow/oauth2-proxy-secret(client-secret 是占位符,等 03-configure-keycloak.sh 填真值)"
fi

echo "==> 复制 MinIO 凭据到需要连它的命名空间"
MINIO_CONSUMER_NAMESPACES="trino data mlflow"
for ns in $MINIO_CONSUMER_NAMESPACES; do
  copy_secret minio "$ns" minio-root
done

echo "==> 复制 Postgres 管理员凭据到需要建库的命名空间"
# 各组件的 create-db-job 都是"在自己的命名空间里跑,通过网络连
# postgres.data.svc.cluster.local",但要用 postgres-root 的密码建库/建用户,
# 这个 Secret 本身在 data 命名空间,同样跨不过去,复制一份过去。
POSTGRES_ROOT_CONSUMER_NAMESPACES="openmetadata mlflow keycloak"
for ns in $POSTGRES_ROOT_CONSUMER_NAMESPACES; do
  copy_secret data "$ns" postgres-root
done

echo
echo "完成。新生成的凭据(如果有)已追加到: ${OUT_FILE}"
echo "这个文件不会被提交到 git(在 .gitignore 里),自己保管好。"
