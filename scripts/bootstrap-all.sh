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

# 等所有 ArgoCD Application 的目标 namespace 都被建出来。
#
# **为什么需要这一步**(2026-08-22 推倒重建验证抓到的):
# `scripts/00-generate-secrets.sh` 往十几个 namespace 里塞 Secret,但那些
# namespace 是 ArgoCD 同步各个 Application 时用 CreateNamespace=true 建的
# ——在一个全新集群上,第 2 步跑 00 的时候它们一个都还不存在,脚本会逐个
# 打印"跳过: xxx(namespace 还不存在)"然后过去。结果是 oauth2-proxy /
# spark-history-server / table-registration-app / feast 这些组件起来之后
# 一直 CreateContainerConfigError(`secret "minio-root" not found` 之类),
# 而且**不会自愈**——没有任何东西会回头再建那些 Secret。
#
# 增量式开发永远碰不到这个问题(namespace 早就在了),只有真的从空集群
# 拉起才暴露。这也是为什么这一步不能省:少了它,"一键部署"实际上要人
# 手动重跑第二遍才能得到一个能用的平台。
wait_for_namespaces() {
  local timeout="${1:-600}"
  local waited=0
  log "--> 等各个 Application 的目标 namespace 被 ArgoCD 建出来(最多 ${timeout}s)"
  while [ "$waited" -lt "$timeout" ]; do
    local missing=""
    for ns in $(kubectl -n argocd get applications \
        -o jsonpath='{range .items[*]}{.spec.destination.namespace}{"\n"}{end}' 2>/dev/null | sort -u); do
      [ -n "$ns" ] || continue
      kubectl get namespace "$ns" >/dev/null 2>&1 || missing="${missing} ${ns}"
    done
    if [ -z "$missing" ]; then
      log "--> 所有目标 namespace 都在了(等了 ${waited}s)"
      return 0
    fi
    sleep 15
    waited=$((waited + 15))
  done
  log "!! 等了 ${timeout}s 还有 namespace 没建出来:${missing}"
  log "   不中止——后面重跑 00 时这些 namespace 对应的 Secret 会再次跳过,"
  log "   等它们出现之后重跑一次这份脚本即可(幂等)。"
  return 0
}

# 等 Application 收敛。超时只警告不中止:有些 Application 要等后面的组件
# 专属初始化步骤(建账号、配数据源)跑完才会健康,在这里死等反而会卡死。
wait_apps_converged() {
  local timeout="${1:-1800}"
  local waited=0
  log "--> 等所有 Application 变成 Synced/Healthy(最多 ${timeout}s,超时只警告不中止)"
  while [ "$waited" -lt "$timeout" ]; do
    local n
    n=$(kubectl -n argocd get applications \
        -o custom-columns=S:.status.sync.status,H:.status.health.status --no-headers 2>/dev/null \
        | grep -vc 'Synced *Healthy' || true)
    if [ "$n" = "0" ]; then
      log "--> 全部收敛(等了 ${waited}s)"
      return 0
    fi
    [ $((waited % 120)) -eq 0 ] && log "    还有 ${n} 个没收敛(已等 ${waited}s)"
    sleep 30
    waited=$((waited + 30))
  done
  log "!! 等了 ${timeout}s 仍有 Application 没收敛,继续往下跑组件专属初始化。"
  log "   跑完之后用 kubectl get applications -n argocd 看剩下哪些,大多数情况下"
  log "   再重跑一次这份脚本就能收敛(幂等)。"
  return 0
}

step "生成/确认管理员密码 Secret(幂等,已存在的不会被覆盖)"
run_required "scripts/00-generate-secrets.sh" ./scripts/00-generate-secrets.sh

step "灌回本地镜像缓存(只对 local-lite 有意义,cloud-full 走另一套远程镜像准备流程)"
if [ "$TARGET_ENV" != "local-lite" ]; then
  # 2026-08-22:加这个判断之前,从 Mac 上 bootstrap cloud-full 会去灌本机
  # 的 image-cache/(那是给 local-lite 准备的),本机没开 docker 时会刷出
  # 二十多行 "!! 加载失败: xxx",看着像一键部署坏了,其实完全无关。
  # 这一步操作的是**跑脚本这台机器自己的本地 docker**,只有 local-lite
  # (colima 就在这台机器上)才有意义。
  log "--> TARGET_ENV=${TARGET_ENV} 不是 local-lite,跳过(这一步灌的是本机 docker,只对 local-lite 有意义)"
  # 2026-08-22 实测教训:境内云主机**直连 ghcr.io 只有约 80KB/s**,一个
  # 几百 MB 的镜像要拉几小时,Pod 会长时间卡在 ContainerCreating 而且看
  # 不出是"慢"还是"死了"。走国内镜像站(scripts/23)实测约 2.3MB/s,差
  # 约 30 倍。
  #
  # 这一步没法在这份脚本里自动做:scripts/23 要 SSH 到云主机上执行,需要
  # CLOUD_VM_IP/CLOUD_VM_KEY,而这份脚本本身只用 kubectl、不假设有 SSH
  # 通道。所以这里只能提醒——但**必须提醒**,因为不做的后果不是"慢一点",
  # 是新组件可能几小时都起不来。
  log ""
  log "    ⚠️  如果目标集群在境内网络、而且这次引入了**新镜像**,先在另一个"
  log "        终端跑一遍(可以和这份脚本并行):"
  log "          CLOUD_VM_IP=<公网IP> CLOUD_VM_KEY=<私钥> ./scripts/23-pull-images-remote-via-mirror.sh"
  log "        它是幂等的,已有的镜像会跳过。不跑的话,新镜像可能卡几小时。"
  log ""
elif [ -d image-cache ] && [ -n "$(ls -A image-cache 2>/dev/null)" ]; then
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

step "装 Kueue 的 CRD(第四个太大的,见 ADR-064)"
run_required "scripts/33-install-kueue-crds.sh" ./scripts/33-install-kueue-crds.sh

step "等 Keycloak Application Healthy"
wait_healthy keycloak 300s

step "配置 Keycloak(platform realm + 各组件 OIDC client + 初始登录用户)"
run_required "scripts/03-configure-keycloak.sh" ./scripts/03-configure-keycloak.sh

step "等 namespace 建出来,然后补建第一次漏掉的 Secret(全新集群必需,见 wait_for_namespaces 注释)"
wait_for_namespaces 600
run_required "scripts/00-generate-secrets.sh(第二遍,补 namespace 建好之后才能建的 Secret)" ./scripts/00-generate-secrets.sh

step "等所有 Application 收敛,再做组件专属初始化"
# 顺序很重要:下面那些"建 Airflow 账号""配 Superset 数据源"的步骤,前提是
# 对应组件已经跑起来了。2026-08-22 之前这些步骤紧跟在配 Keycloak 后面,
# 在一个全新集群上执行时组件一个都还没起来,于是全部打印"跳过"然后过去
# ——脚本报"全部完成",实际上一件都没做。
wait_apps_converged 1800

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

step "配 OpenMetadata 的 Trino 元数据自动采集(不配的话数据目录里只有人工录入的表)"
# 借 table-registration-app 的 Pod 跑(它容器里有 python3+requests,而且
# 已经挂了 OPENMETADATA_TOKEN/OPENMETADATA_URL);OpenMetadata 自己的镜像
# 里只有 wget 没有 python3,新起一次性 Pod 在这台云主机上又容易卡镜像拉取
# (scripts/27 注释里记过)。
if kubectl -n table-registration-app get pod -l app=table-registration-app >/dev/null 2>&1; then
  run_optional "scripts/29-configure-openmetadata-trino-ingestion.sh" ./scripts/29-configure-openmetadata-trino-ingestion.sh
else
  log "--> table-registration-app 还没起来,跳过"
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
