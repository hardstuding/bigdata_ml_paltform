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
#   ./scripts/bootstrap-all.sh                                            # cloud-full(默认)
#   TARGET_ENV=local-lite NEEDS_LOCAL_PROXY=1 ./scripts/bootstrap-all.sh  # local-lite(colima,要过代理才能出网)
#   TARGET_ENV=prod ./scripts/bootstrap-all.sh                            # 生产 IDC
#
# TARGET_ENV 只用来**校验**这个工作区当前渲染的是不是这个环境(第 1 步),
# 不会自动帮你渲染——渲染改的是本地文件,ArgoCD 读的是 git 远端,自动渲染
# 只会制造"我明明渲染过了"的错觉。不一致时脚本会停下来告诉你该跑什么。
#
# 前提(和 README 一致,这份脚本不重新检查):已经有一个能用的
# Kubernetes 集群,kubectl/helm 能连上它,本机装了 git。
#
# 组件专属初始化(05/06/12/14 这几步)是"尽力而为"——对应的组件当前如果
# 是 park 状态(namespace/Deployment 还不存在),这份脚本会跳过并打印
# 原因,不会因为某个非核心组件还没拉起来就让整个脚本失败退出;核心步骤
# (00~03,含新增的 25)任何一步失败都会让脚本立即停止,因为后面的步骤
# 大概率也会跟着失败,继续跑只会让日志更难看懂。
set -uo pipefail
cd "$(dirname "$0")/.."

mkdir -p logs
LOG_FILE="logs/bootstrap-all.log"
# 要部署哪个环境画像。默认 cloud-full——这是目前唯一真实跑过完整
# 部署的环境;local-lite 用 `TARGET_ENV=local-lite NEEDS_LOCAL_PROXY=1
# ./scripts/bootstrap-all.sh`。
TARGET_ENV="${TARGET_ENV:-cloud-full}"
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

step "确认这个工作区当前渲染的就是要部署的环境(${TARGET_ENV})"
# 2026-08-22 补:这一步以前没有,是一键部署里一个很安静的坑。
# `apps/definitions/` 和 `platform/apps/` 是渲染产物,**同一时刻只能代表
# 一个环境**。如果这个工作区上一次跑的是 `render-environment-config.py
# local-lite`,现在直接 bootstrap 到一台 cloud-full 机器上,ArgoCD 会照着
# local-lite 的组件清单和域名去部署——每个 Pod 都 Running、ArgoCD 全绿,
# 但装出来的是错的那套东西。这类"看起来成功了"正是这个项目反复踩的坑。
#
# 这里只校验、不自动渲染:渲染会改工作区文件,而 ArgoCD 读的是 git 远端,
# 本地改完不 commit+push 根本不生效,自动渲染只会制造"我明明渲染过了"的
# 错觉。不一致就停下来,让人自己渲染 + 提交 + 推送。
run_required "scripts/render-environment-config.py ${TARGET_ENV} --check" \
  python3 ./scripts/render-environment-config.py "${TARGET_ENV}" --check

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
log "===== 核心步骤(环境校验 + README 那份清单 + 补的 argo-workflows CRD)全部完成 ====="
log "接下来是组件专属初始化——对应组件现在如果没启用会自动跳过,"
log "以后在 environments/<env>/config.yaml 的 enabled_components 里启用、"
log "重新渲染 apps/definitions/ 之后,单独重跑对应的那一条命令就行,"
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

step "装 KServe 的 ClusterServingRuntime(官方 chart 不带,不装的话模型上线时没有 runtime 可用)"
# 2026-08-21 补进来的:这一步以前不在 bootstrap 里,得人记得手动跑。
# 后果是"全新部署出来的平台,KServe 装好了但一个 runtime 都没有,直到有人
# 真的去上线模型才发现"——和这个仓库反复踩的"部署了 ≠ 能用"是同一类。
if kubectl get crd clusterservingruntimes.serving.kserve.io >/dev/null 2>&1; then
  run_optional "scripts/10-install-kserve-serving-runtimes.sh" ./scripts/10-install-kserve-serving-runtimes.sh
else
  log "--> KServe CRD 不存在(kserve 还没启用),跳过"
fi

step "配 OpenMetadata 连 OpenSearch 的自签证书信任"
# 同样是 2026-08-21 补进来的,原因同上:不跑这一步 OpenMetadata 连不上
# OpenSearch(https + 自签),搜索/目录功能是坏的,但首页能打开,很容易
# 被当成"部署成功了"。
if kubectl get deploy openmetadata -n openmetadata >/dev/null 2>&1; then
  run_optional "scripts/20-configure-openmetadata-search-truststore.sh" ./scripts/20-configure-openmetadata-search-truststore.sh
else
  log "--> openmetadata 还没起来,跳过"
fi

step "拿 OpenMetadata 的 ingestion-bot token,给 table-registration-app / permission-request-app / 血缘推送用"
# 2026-08-22 补进来的:此前这一步要人工去 OpenMetadata UI 建 bot、抄 JWT、
# 手动 kubectl create secret,导致血缘功能长期是 ❌。OpenMetadata 装好时
# 已经自动生成一个 ingestion-bot 的 unlimited JWT(存在 Postgres 里,Fernet
# 加密),这个脚本直接读出来解密用,不用人工介入。
if kubectl get deploy openmetadata -n openmetadata >/dev/null 2>&1; then
  run_optional "scripts/27-configure-openmetadata-bot.sh" ./scripts/27-configure-openmetadata-bot.sh
else
  log "--> openmetadata 还没起来,跳过"
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
