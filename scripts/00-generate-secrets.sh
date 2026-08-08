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

echo "==> 建命名空间"
for ns in keycloak monitoring minio data; do
  ensure_ns "$ns"
done

echo "==> 生成/创建 Secret(已存在的不会重新生成,不会轮换密码)"
echo "# $(date -u +%FT%TZ) 生成的凭据,不要提交到 git" >> "$OUT_FILE"

ensure_secret keycloak    keycloak-admin    username=admin    password=RANDOM
ensure_secret monitoring  grafana-admin     admin-user=admin  admin-password=RANDOM
ensure_secret minio       minio-root        rootUser=admin    rootPassword=RANDOM
ensure_secret data        postgres-root     username=postgres password=RANDOM
ensure_secret data        hive-metastore-db username=hive     password=RANDOM

echo
echo "完成。新生成的凭据(如果有)已追加到: ${OUT_FILE}"
echo "这个文件不会被提交到 git(在 .gitignore 里),自己保管好。"
