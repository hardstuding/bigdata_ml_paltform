#!/usr/bin/env bash
# 从空集群到"核心链路能跑"的一键拉起脚本,按顺序串起 README"从零拉起
# 整套服务"那一节列的步骤 + 实际在 cloud-full 上真实部署时才暴露、但
# README 那份"7 步"清单当时漏记的几步(argo-workflows CRD、Trino 探针
# 修复不只是"重建才需要",第一次拉起就需要)。
#
# 这不是 docs/BACKLOG.md P1.3"环境 overlay 重构"那个更大的架构性任务
# (改配置真正切环境,不是这份脚本能替代的)——这份脚本做的是更朴素的
# 事:把现在已经存在、已经各自验证过的一堆脚本,按正确顺序 + 正确的等待
# 时机串起来,不需要人记住 7+ 条命令的先后关系和中间要等哪个 Application
# 变 Healthy。每一步本身的幂等性由它自己的脚本保证(这份脚本不重新实现
# 幂等逻辑),意外中断后重跑这份脚本是安全的。
#
# 用法:
#   ./scripts/bootstrap-all.sh                        # cloud-full / 生产 IDC 这类直连公网的环境
#   NEEDS_LOCAL_PROXY=1 ./scripts/bootstrap-all.sh     # local-lite(colima)这类需要过代理才能出网的环境
#
# 前提(和 README 一致,这份脚本不重新检查):已经有一个能用的
# Kubernetes 集群,kubectl/helm 能连上它,本机装了 git。
#
# 组件专属初始化(05/06/12/14 这几步)是"尽力而为"——对应的组件当前如果
# 是 park 状态(namespace/Deployment 还不存在),这份脚本会跳过并打印
# 原因,不会因为某个非核心组件还没拉起来就让整个脚本失败退出;核心 7 步
# (00~03,含新增的 25)任何一步失败都会让脚本立即停止,因为后面的步骤
# 大概率也会跟着失败,继续跑只会让日志更难看懂。
set -uo pipefail
cd "$(dirname "$0")/.."

mkdir -p logs
LOG_FILE="logs/bootstrap-all.log"
STEP=0

log() {
  local msg="$1"
  echo "$msg"
  echo "$(date -u +%FT%TZ) $msg" >> "$LOG_FILE"
}

step() {
  STEP=$((STEP + 1))
  log ""
  log "===== 第 ${STEP} 步:$1 ====="
}

# 核心步骤失败就停——后面的步骤依赖它,继续跑没有意义。
run_required() {
  local desc="$1"; shift
  log "--> $desc"
  if ! "$@" >>"$LOG_FILE" 2>&1; then
    log "!! 失败:$desc(退出码 $?)。完整日志见 ${LOG_FILE},修好问题后重新跑这份脚本(幂等,会跳过已经做完的部分)。"
    exit 1
  fi
}

# 组件专属初始化失败只警告不中止——很可能是对应组件当前是 park 状态,
# 不是这份脚本自己的问题。
run_optional() {
  local desc="$1"; shift
  log "--> $desc(尽力而为,组件没拉起来会跳过)"
  if ! "$@" >>"$LOG_FILE" 2>&1; then
    log "!! 跳过:$desc 失败了,可能是对应组件还是 park 状态。完整输出见 ${LOG_FILE},不影响后面的步骤。"
  fi
}

wait_healthy() {
  local app="$1"
  local timeout="${2:-300s}"
  log "--> 等 ArgoCD Application '${app}' 变成 Healthy(最多 ${timeout})"
  if ! kubectl -n argocd wait --for=jsonpath='{.status.health.status}'=Healthy "application/${app}" --timeout="${timeout}" >>"$LOG_FILE" 2>&1; then
    log "!! Application '${app}' 没能在 ${timeout} 内变 Healthy,后面依赖它的步骤大概率也会失败。先用 kubectl get application -n argocd ${app} -o yaml 查一下,再重跑这份脚本。"
    exit 1
  fi
}

step "生成/确认管理员密码 Secret(幂等,已存在的不会被覆盖)"
run_required "scripts/00-generate-secrets.sh" ./scripts/00-generate-secrets.sh

step "灌回本地镜像缓存(只对 local-lite 有意义,cloud-full 走另一套远程镜像准备流程)"
if [ -d image-cache ] && [ -n "$(ls -A image-cache 2>/dev/null)" ]; then
  # 这一步操作的是"跑这份脚本的机器自己的本地 docker 存储",不是
  # kubectl 当前指向的那个集群——对 local-lite(colima 就在这台机器上)
  # 有意义;对 cloud-full,镜像准备是在远端云主机上单独做的(见
  # scripts/21-bootstrap-cloud-vm.sh + scripts/22/23-*-remote*.sh,这两个
  # 脚本要在 ArgoCD 开始调度组件、镜像真的被需要之前就做完,不属于这份
  # "集群里的东西怎么串起来"脚本的范围,这里不自动帮你判断/触发)。
  run_optional "scripts/17-load-image-cache.sh" ./scripts/17-load-image-cache.sh
else
  log "--> image-cache/ 不存在或是空的,跳过(local-lite 会走网络现拉镜像,更慢但能用;cloud-full 本来就不靠这个,忽略这条日志)"
fi

step "装 ArgoCD 本身(唯一一次手动 helm install,之后全部交给 GitOps)"
run_required "scripts/01-bootstrap-argocd.sh(NEEDS_LOCAL_PROXY=${NEEDS_LOCAL_PROXY:-0})" ./scripts/01-bootstrap-argocd.sh

step "把两个 app-of-apps 交给 ArgoCD"
run_required "scripts/02-bootstrap-root-apps.sh" ./scripts/02-bootstrap-root-apps.sh

step "装 kube-prometheus-stack 的 CRD(太大,ArgoCD 应付不了)"
run_required "scripts/04-install-kube-prometheus-crds.sh" ./scripts/04-install-kube-prometheus-crds.sh

step "装 CloudNativePG 的 CRD(同样太大,见 ADR-038)"
run_required "scripts/16-install-cloudnative-pg-crds.sh" ./scripts/16-install-cloudnative-pg-crds.sh

step "装 argo-workflows 的 CRD(vendor 进仓库,不依赖运行时下载,见脚本头部注释)"
run_required "scripts/25-install-argo-workflows-crds.sh" ./scripts/25-install-argo-workflows-crds.sh

step "等 Keycloak Application Healthy"
wait_healthy keycloak 300s

step "配置 Keycloak(platform realm + 各组件 OIDC client + 初始登录用户)"
run_required "scripts/03-configure-keycloak.sh" ./scripts/03-configure-keycloak.sh

log ""
log "===== 核心 7 步(README 那份清单 + 补的 argo-workflows CRD)全部完成 ====="
log "接下来是组件专属初始化——对应组件现在如果是 park 状态会自动跳过,"
log "以后从 pending-definitions/ 拉回来之后,单独重跑对应的那一条命令就行,"
log "不需要重跑整份脚本。"

step "修 Trino livenessProbe(chart 硬编码错误,见 ADR-017;每次 Deployment 重建都要重跑,含第一次拉起)"
if kubectl get deploy trino-coordinator -n trino >/dev/null 2>&1; then
  run_optional "scripts/07-fix-trino-liveness-probe.sh" ./scripts/07-fix-trino-liveness-probe.sh
else
  log "--> trino-coordinator 这个 Deployment 不存在(Trino 还是 park 状态),跳过"
fi

step "建 Airflow 初始管理员账号"
if kubectl get deploy airflow-webserver -n airflow >/dev/null 2>&1 || kubectl get deploy airflow -n airflow >/dev/null 2>&1; then
  run_optional "scripts/05-configure-airflow.sh" ./scripts/05-configure-airflow.sh
else
  log "--> airflow 命名空间/Deployment 不存在(还是 park 状态),跳过"
fi

step "给 Superset 注册 Trino 数据源"
if kubectl get deploy superset -n superset >/dev/null 2>&1 && kubectl get deploy trino-coordinator -n trino >/dev/null 2>&1; then
  run_optional "scripts/06-configure-superset-datasources.sh" ./scripts/06-configure-superset-datasources.sh
else
  log "--> superset 或 trino 还没都起来,跳过"
fi

step "同步 platform/iam/ 里的组织架构/角色数据进 Keycloak"
run_optional "scripts/12-sync-iam.py --no-create-users" python3 ./scripts/12-sync-iam.py --no-create-users

step "给 seatunnel_device_events 这个 DAG 写 MinIO 凭据"
if kubectl get deploy airflow-webserver -n airflow >/dev/null 2>&1 || kubectl get deploy airflow -n airflow >/dev/null 2>&1; then
  run_optional "scripts/14-configure-airflow-seatunnel-variable.sh" ./scripts/14-configure-airflow-seatunnel-variable.sh
else
  log "--> airflow 还没起来,跳过"
fi

log ""
log "===== 全部完成 ====="
log "用 kubectl get applications -n argocd 确认所有组件是不是 Synced/Healthy。"
log "卡住了先查 docs/operations/troubleshooting.md,完整执行日志在 ${LOG_FILE}。"
