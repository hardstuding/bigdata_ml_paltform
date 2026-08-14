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
  # 2026-08-13 推倒重建测试时实测踩到的坑:在全新集群上,这个脚本比 ArgoCD
  # 装的还早(见 README"从零拉起整套服务"的步骤顺序),spark-operator/
  # seatunnel 这类命名空间是它们各自 Application 的 CreateNamespace=true
  # 建的,这时候还不存在——直接 kubectl apply 会报 "namespaces ... not
  # found" 让整个脚本中止。和下面 spark-operator/permission-request-app 的
  # oauth2-proxy-secret 是同一个模式,跳过、等对应 Application 先同步一次、
  # namespace 建出来之后重跑这个脚本(幂等,不会重复复制/覆盖)。
  if ! kubectl get namespace "$dst_ns" >/dev/null 2>&1; then
    echo "跳过: ${dst_ns}/${name}(namespace 还不存在,等对应 Application 先同步一次)"
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

# Trino 的 OAuth2(Authorization Code 模式)是给人在浏览器里操作设计的,
# Superset 的 SQL Lab 要拿一个后端到后端的身份连 Trino,不能走这条路。Trino
# 原生支持多种认证方式并存(http-server.authentication.type=OAUTH2,PASSWORD,
# 见 docs/decisions/,服务端按顺序尝试,客户端发 Basic Auth 会自然落到
# PASSWORD 这条),给 Superset 建一个专门的服务账号用 PASSWORD 认证,人类还是
# 走 Keycloak OAuth2。密码文件要 bcrypt 哈希,cost 至少 8(Trino 文档写的
# 最低要求),依赖系统自带的 htpasswd(macOS/大多数 Linux 发行版都有,来自
# apache2-utils 或系统自带的 httpd 工具)。
## trino-service-account 这个 Secret 的 password.db 是一个共享的 htpasswd
## 风格文件(一行一个 `username:bcryptHash`),Trino 的 file authenticator
## 一次只能指到一个文件,所以每新增一个服务账号(2026-08-14 新增
## table_registration_service,见 docs/decisions/043-table-registration-tool.md)
## 都要往同一个文件里追加一行,不能各建各的 Secret——用
## ensure_trino_service_account 这个函数统一处理"没有这个用户就追加、密码是
## 独立生成、已有的用户不动"这几件事,保证幂等,不会因为新增账号而破坏已有
## 账号的密码。
ensure_trino_service_account() {
  local username="$1"
  local pw hash
  if kubectl -n trino get secret trino-service-account >/dev/null 2>&1; then
    local existing_db
    existing_db="$(kubectl -n trino get secret trino-service-account -o jsonpath='{.data.password\.db}' | base64 -d)"
    if echo "$existing_db" | grep -q "^${username}:"; then
      echo "已存在,跳过: trino/trino-service-account 里的 ${username}"
      return
    fi
    pw="$(gen_password)"
    hash="$(htpasswd -nbBC 10 "$username" "$pw")"
    local new_db
    new_db="$(printf '%s\n%s' "$existing_db" "$hash")"
    # 用 merge patch 只追加/更新 password.db 和这个用户专属的
    # password-<username> 这两个 key,不碰 Secret 里已有的其它 key——尤其是
    # 顶层的 username/password,那两个字段是最早创建这个 Secret 时那个账号
    # (superset_service)专用、被复制进 superset 命名空间直接消费的,后来新增
    # 的账号绝不能覆盖掉它们,否则会打断 Superset 的 Trino 连接。
    kubectl -n trino patch secret trino-service-account --type merge -p "$(python3 -c "
import json, base64, sys
db, pw = sys.argv[1].encode(), sys.argv[2].encode()
print(json.dumps({'data': {
    'password.db': base64.b64encode(db).decode(),
    'password-${username}': base64.b64encode(pw).decode(),
}}))
" "$new_db" "$pw")" >/dev/null
    echo "已追加: trino/trino-service-account 里的 ${username}(密码见 ${OUT_FILE})"
  else
    pw="$(gen_password)"
    hash="$(htpasswd -nbBC 10 "$username" "$pw")"
    kubectl -n trino create secret generic trino-service-account \
      --from-literal=username="$username" \
      --from-literal=password="$pw" \
      --from-literal="password-${username}=${pw}" \
      --from-literal=password.db="$hash" \
      --from-literal=password-authenticator.properties="password-authenticator.name=file
file.password-file=/secrets/trino-service-account/password.db"
    echo "已创建: trino/trino-service-account(首个账号 ${username})"
  fi
  echo "trino/${username}: $pw" >> "$OUT_FILE"
}

ensure_trino_service_account superset_service
# table-registration-app(ADR-043)专用的 Trino 服务账号,不复用
# superset_service——各组件各自独立账号是这个项目的一贯做法(见 ADR-021),
# 方便以后单独追溯/吊销。
ensure_trino_service_account table_registration_service

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
# apps/definitions/mlflow-oauth2-proxy.yaml)。
#
# cookie-secret 长度这里之前的注释写错了(2026-08-12 部署
# permission-request-app 的 oauth2-proxy 时才真正启动一次、实测报错才发现):
# oauth2-proxy 校验的是**这个字符串本身的原始长度**要是 16/24/32,不是
# "base64 解码之后"的字节数。`openssl rand -base64 32` 是 32 字节随机数
# 编码成 base64,字符串长度会变成 44(带 padding),报
# "cookie_secret must be 16, 24, or 32 bytes... but is 44 bytes"。正确做法
# 是倒推着凑:要一个刚好 32 字符、没有 padding 的 base64 字符串,原始字节数
# 要能被 3 整除且编码后不产生 `=`,`openssl rand -base64 24` 算出来正好是
# 32 个字符。不能用 gen_password(那个函数会剔除 +/= 字符,破坏 base64
# 编码的字符分布)。
# client-id 不是真的密钥,但 chart 的 existingSecret 机制要求这三个 key 都在
# 同一个 Secret 里,直接存字面量 "mlflow"。client-secret 由
# 03-configure-keycloak.sh 建 Keycloak client 之后 patch 进来。
if kubectl -n mlflow get secret oauth2-proxy-secret >/dev/null 2>&1; then
  echo "已存在,跳过: mlflow/oauth2-proxy-secret"
else
  COOKIE_SECRET="$(openssl rand -base64 24)"
  kubectl -n mlflow create secret generic oauth2-proxy-secret \
    --from-literal=client-id=mlflow \
    --from-literal=cookie-secret="$COOKIE_SECRET" \
    --from-literal=client-secret=PLACEHOLDER
  echo "已创建: mlflow/oauth2-proxy-secret(client-secret 是占位符,等 03-configure-keycloak.sh 填真值)"
fi

# Spark History Server 同样没有原生 OIDC,同一个 oauth2-proxy 模式(见
# ADR-029)。spark-operator 这个 namespace 是 Spark Operator 自己的
# Application 建的(CreateNamespace=true),这段要等它先跑过一次、namespace
# 已经存在才能成功——和 spark-history-server 本身一样,是"配置就绪,等
# Spark Operator 真正启用时才生效"的状态。
if kubectl -n spark-operator get secret oauth2-proxy-secret >/dev/null 2>&1; then
  echo "已存在,跳过: spark-operator/oauth2-proxy-secret"
elif ! kubectl get namespace spark-operator >/dev/null 2>&1; then
  echo "跳过: spark-operator/oauth2-proxy-secret(namespace 还不存在,Spark Operator 还没启用)"
else
  COOKIE_SECRET="$(openssl rand -base64 24)"
  kubectl -n spark-operator create secret generic oauth2-proxy-secret \
    --from-literal=client-id=spark-history-server \
    --from-literal=cookie-secret="$COOKIE_SECRET" \
    --from-literal=client-secret=PLACEHOLDER
  echo "已创建: spark-operator/oauth2-proxy-secret(client-secret 是占位符,等 03-configure-keycloak.sh 填真值)"
fi

# 权限自助申请门户,同一个 oauth2-proxy 模式(见 ADR-032)。
if kubectl -n permission-request-app get secret oauth2-proxy-secret >/dev/null 2>&1; then
  echo "已存在,跳过: permission-request-app/oauth2-proxy-secret"
elif ! kubectl get namespace permission-request-app >/dev/null 2>&1; then
  echo "跳过: permission-request-app/oauth2-proxy-secret(namespace 还不存在,等这个 Application 先同步一次)"
else
  COOKIE_SECRET="$(openssl rand -base64 24)"
  kubectl -n permission-request-app create secret generic oauth2-proxy-secret \
    --from-literal=client-id=permission-request-app \
    --from-literal=cookie-secret="$COOKIE_SECRET" \
    --from-literal=client-secret=PLACEHOLDER
  echo "已创建: permission-request-app/oauth2-proxy-secret(client-secret 是占位符,等 03-configure-keycloak.sh 填真值)"
fi

# 建表注册工具,同一个 oauth2-proxy 模式(见 ADR-043)。
if kubectl -n table-registration-app get secret oauth2-proxy-secret >/dev/null 2>&1; then
  echo "已存在,跳过: table-registration-app/oauth2-proxy-secret"
elif ! kubectl get namespace table-registration-app >/dev/null 2>&1; then
  echo "跳过: table-registration-app/oauth2-proxy-secret(namespace 还不存在,等这个 Application 先同步一次)"
else
  COOKIE_SECRET="$(openssl rand -base64 24)"
  kubectl -n table-registration-app create secret generic oauth2-proxy-secret \
    --from-literal=client-id=table-registration-app \
    --from-literal=cookie-secret="$COOKIE_SECRET" \
    --from-literal=client-secret=PLACEHOLDER
  echo "已创建: table-registration-app/oauth2-proxy-secret(client-secret 是占位符,等 03-configure-keycloak.sh 填真值)"
fi

echo "==> 复制 MinIO 凭据到需要连它的命名空间"
# spark-operator: SparkApplication driver/executor 直连 MinIO(S3A)读写
# Iceberg warehouse,和 Trino 当初踩的是同一个坑,同样要复制一份
# (ADR-036 验证 Spark+Iceberg 链路时发现 driver pod 报 secret not found)。
MINIO_CONSUMER_NAMESPACES="trino data mlflow spark-operator seatunnel"
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

echo "==> 复制 Trino 服务账号凭据给 Superset(配 Trino 数据源连接要用)"
copy_secret trino superset trino-service-account

echo "==> 复制 Trino 服务账号凭据给建表注册工具(ADR-043,连 Trino 执行建表 DDL 要用)"
copy_secret trino table-registration-app trino-service-account

echo
echo "完成。新生成的凭据(如果有)已追加到: ${OUT_FILE}"
echo "这个文件不会被提交到 git(在 .gitignore 里),自己保管好。"
