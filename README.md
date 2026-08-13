# bigdata_ml_paltform

Kubernetes-native 的 Data + AI 平台骨架:统一身份认证、GitOps 驱动的部署方式、按规模分级的环境画像(本机验证 → 云端集成 → 生产)。目标不是照抄一套 CDH,而是给还在用 YARN 时代大数据平台的团队一条能力对等、但运维模型现代化的迁移路径,同时兼容接入现有的遗留 Hadoop 集群做渐进迁移。

## 这个项目提供什么

- **一次登录,处处可用**:所有组件统一接 Keycloak SSO(OIDC),角色/权限按组织架构里的组(group)分发,不是每个组件单独一套账号体系。
- **GitOps 是唯一的操作接口**:改一个 YAML、`git push`,ArgoCD 自动把集群状态收敛过去。没有隐藏在某个人电脑里的手动步骤——这也是为什么这套平台对 AI Agent 友好:Agent 能做的操作和人能做的操作是同一套(改 git、看 ArgoCD 状态),不需要给 Agent 开额外的特权通道。
- **按规模分级,不是"要么全装要么不装"**:`local-lite`(笔记本电脑验证)/ `cloud-full`(功能完整的集成环境)/ `prod`(生产)三档,同一套组件定义,资源画像不同。
- **可插拔基础设施**:公司已经有 Postgres/Kafka/对象存储了?不强制重新部署一份,配置里标出了怎么改接现有的(见 [ADR-030](docs/decisions/030-pluggable-external-infrastructure.md))。
- **只用官方支持的部署方式**:不用 Bitnami 或来源不明的社区 chart,没有官方 Helm chart 的组件才自己写 manifest(见 [ADR-008](docs/decisions/008-avoid-bitnami.md))。每个决策的取舍理由都留了 ADR,不是"我们就是这么做的",是"为什么这么做、还有什么后果"。

## 快速上手

```bash
git clone <这个仓库的地址> bigdata_ml_paltform && cd bigdata_ml_paltform
./scripts/00-generate-secrets.sh
./scripts/17-load-image-cache.sh          # 可选:本机之前用 export-image-cache.sh 存过镜像备份的话,先灌回本地,后面部署不用逐个连外网拉
./scripts/01-bootstrap-argocd.sh          # 需要代理才能出网的环境:NEEDS_LOCAL_PROXY=1 ./scripts/01-bootstrap-argocd.sh
./scripts/02-bootstrap-root-apps.sh
./scripts/04-install-kube-prometheus-crds.sh
./scripts/16-install-cloudnative-pg-crds.sh
kubectl -n argocd wait --for=jsonpath='{.status.health.status}'=Healthy application/keycloak --timeout=300s
./scripts/03-configure-keycloak.sh
```

完整步骤、每步在做什么、常见卡点见下面["从零拉起整套服务"](#从零拉起整套服务新集群--迁移到-gitlab--生产-idc)。

## 仓库结构

```
platform/        # 平台底座:ArgoCD、ingress-nginx、cert-manager、Keycloak、监控、审计看板、自定义 DNS
apps/
  definitions/    # 业务组件的 ArgoCD Application 定义(当前启用的)
  <component>/    # 没有官方 chart、我们自己写 manifest 的组件各占一个目录(postgres、hive-metastore、spark-history-server 等)
environments/     # local-lite / cloud-full / prod 三个环境画像;pending-definitions/ 收着"配置已验证、本机资源关系暂时收起来"的组件
scripts/          # 一键拉起 / 常用运维 / 校验脚本,编号大致是执行顺序
docs/
  architecture.md   # 架构总览、组件清单、路线图
  decisions/        # ADR——每个非显而易见的技术决策,连同踩过的坑
  operations/       # 运维手册:排障记录、升级流程、版本清单、备份
```

## 环境画像

| Profile | 用途 | 位置 |
|---|---|---|
| `local-lite` | 本机验证 GitOps 流程 + 核心功能打通 | 单机(实测 Mac M2/16GB, colima + k3s) |
| `cloud-full` | 功能完整的开发与集成环境 | 云服务器 |
| `prod` | 替换现有遗留大数据平台 | 生产环境 |

同一套 Application 定义,`environments/<profile>/` 决定开哪些组件、配多少资源。组件清单、当前 Phase 进度见 [`docs/architecture.md`](docs/architecture.md) 的路线图。

## 组件

### 标准开源组件(官方 chart/官方镜像,我们只是配了 values)

用法、原理、故障排查以对应项目自己的官方文档为准,这里不重复维护——重复抄一遍容易随上游升级过时。这个仓库里对应的只是"怎么把它接进这套平台"的集成配置,踩过的坑记在 [ADR](docs/decisions/) 里。完整版本清单(自动生成,不是手写维护、不会过时)见 [`docs/operations/upgrade.md`](docs/operations/upgrade.md),跑 `python3 scripts/list-component-versions.py` 拿到最新的。

平台底座:ArgoCD、[ingress-nginx](https://kubernetes.github.io/ingress-nginx/)、[cert-manager](https://cert-manager.io/)、[Keycloak](https://www.keycloak.org/)(codecentric/keycloakx chart)、[kube-prometheus-stack](https://github.com/prometheus-operator/kube-prometheus-stack)(Prometheus + Grafana)、[Loki](https://grafana.com/oss/loki/) + [Grafana Alloy](https://grafana.com/docs/alloy/)。

湖仓核心:[MinIO](https://min.io/)、[Trino](https://trino.io/)、[Apache Superset](https://superset.apache.org/)、[OpenMetadata](https://open-metadata.org/)、[OpenSearch](https://opensearch.org/)。

数据工程:[Apache Kafka](https://kafka.apache.org/)([Strimzi](https://strimzi.io/) operator)、[Apache Airflow](https://airflow.apache.org/)、[Apache SeaTunnel](https://seatunnel.apache.org/)、[Spark Operator](https://github.com/kubeflow/spark-operator)。

AI/ML:[JupyterHub](https://jupyterhub.readthedocs.io/)、[MLflow](https://mlflow.org/)、[Argo Workflows](https://argoproj.github.io/workflows/)、[KServe](https://kserve.github.io/website/)。

通用:[oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/)(给没有原生 OIDC 的组件挡一层 SSO,MLflow/Spark History Server 各用了一份)。

### 这个仓库自己维护的部分

没有官方 Helm chart 的组件(官方镜像,manifest 自己写的),以及平台自己的集成/运维工具链:

| 内容 | 是什么 | 文档 |
|---|---|---|
| `apps/postgres/`、`apps/hive-metastore/`、`apps/spark-history-server/` | 官方镜像 + 自己写的裸 K8s manifest(这几个组件没有官方 chart) | 各自的注释 + [ADR-029](docs/decisions/029-spark-permissions-and-observability.md) |
| `platform/iam/` + `scripts/12-sync-iam.py` | 组织架构/角色数据表(YAML+CSV)→ 同步进 Keycloak Group/Role 的声明式同步工具 | [ADR-028](docs/decisions/028-iam-org-model.md) |
| `apps/iam-sync/` | CronJob,每 5 分钟自动跑上面那个同步脚本,不用等人手动执行 | [ADR-031](docs/decisions/031-iam-auto-sync-cronjob.md) |
| `apps/permission-request-app/` | 权限自助申请门户(申请 -> platform-team 审批 -> 自动写回 `platform/iam/`) | [ADR-032](docs/decisions/032-permission-request-app.md) |
| `platform/coredns-custom/` | 让集群内 pod 能解析 `*.local-lite.test` 这类本地域名的自定义 DNS zone | [ADR-016](docs/decisions/016-ingress-domains-local-lite.md) |
| `platform/grafana-audit-dashboard/` | 平台审计日志看板(Keycloak 登录事件 + Trino 查询时间线) | [ADR-024](docs/decisions/024-platform-audit-logging.md) |
| `scripts/list-project-images.py` + `export-image-cache.sh` | 扫描全部用到的容器镜像、导出本地缓存(内网环境准备用) | [ADR-018](docs/decisions/018-local-image-cache.md) |
| `scripts/list-component-versions.py` | 汇总所有组件当前锁定的版本 | [ADR-010](docs/decisions/010-optional-components-versioning.md) |
| `scripts/validate-charts.py` | CI 用:所有 Application 的 chart 来源跑 `helm template`,纯 manifest 做语法检查 | [ADR-022](docs/decisions/022-ci-chart-validation.md) |
| `scripts/08/09/11-*.sh` | 端到端 demo(湖仓核心、AI/ML 训练→上线两条主线) | ADR-021/023/027 |

## 从零拉起整套服务(新集群 / 迁移到 GitLab / 生产 IDC)

前提:已经有一个能用的 Kubernetes 集群,`kubectl`/`helm` 能连上它,本机装了 `git`。

```bash
# 1. 把仓库同步过去(如果目标环境要从 GitLab 拉,先在那边 git clone)
git clone <这个仓库的地址> bigdata_ml_paltform && cd bigdata_ml_paltform

# 2. 如果仓库地址换了(比如从这个 GitHub demo 迁移到你的 GitLab),
#    先把 ArgoCD Application 里硬编码的 repoURL 换掉,改完记得 commit + push
./scripts/set-repo-url.sh https://gitlab.com/<你的路径>/bigdata_ml_paltform.git
git add -A && git commit -m "chore: 迁移仓库地址" && git push

# 3. 生成各组件的管理员密码,建好对应的 Secret(幂等,重复跑不会轮换已有密码)
./scripts/00-generate-secrets.sh

# 3.5 可选:这台机器之前用 export-image-cache.sh 存过镜像备份的话(比如
#     重建同一台 colima、或者从别的机器搬了一份 image-cache/ 过来),先灌回
#     本地 docker——k3s 走 cri-dockerd,和 docker 是同一份存储,灌进去
#     kubelet 立刻能直接用,不用后面每个组件都连外网现拉(实测过差距:
#     一个 400MB 的镜像,网络拉取 5 分钟以上,本地灌只要 5.7 秒)。
./scripts/17-load-image-cache.sh

# 4. 装 ArgoCD 本身(唯一一次手动 helm install,之后全部交给 GitOps)
#    本机+colima 这种需要过代理才能出网的环境,前面加 NEEDS_LOCAL_PROXY=1
./scripts/01-bootstrap-argocd.sh

# 5. 把两个 app-of-apps 交给 ArgoCD,后面所有组件的增删改都是 git push
./scripts/02-bootstrap-root-apps.sh

# 6. kube-prometheus-stack 的 CRD 太大,ArgoCD 应付不了,单独装一次
#    (只在第一次装、或者升级这个组件版本时需要跑)
./scripts/04-install-kube-prometheus-crds.sh

# 6.5 CloudNativePG 的 CRD(clusters/poolers 这两个太大,同样的坑,见
#     ADR-038)也要单独装一次,不然 apps/definitions/postgres.yaml 这个
#     Application 会一直卡在 Missing(`Cluster` 这个 kind 不存在)。
./scripts/16-install-cloudnative-pg-crds.sh

# 7. 等 keycloak Application Synced/Healthy 之后,建 platform realm +
#    ArgoCD/Grafana 等组件的 OIDC client + 一个初始登录用户。SSO 能不能用
#    全靠这一步,不是可选项。Keycloak realm/client/user 是 kcadm.sh 命令式
#    建的,不在 GitOps 范围内(见 ADR-009),重建集群必须重新跑。
kubectl -n argocd wait --for=jsonpath='{.status.health.status}'=Healthy application/keycloak --timeout=300s
./scripts/03-configure-keycloak.sh
```

跑完用 `kubectl get applications -n argocd` 看所有组件是不是 `Synced`/`Healthy`。卡住了先查 [`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md),这台机器踩过的坑基本都记在那了。

`local-lite` 用的是自造域名(不是真实 DNS,见 [ADR-016](docs/decisions/016-ingress-domains-local-lite.md)),要在自己电脑的 `/etc/hosts` 里加一行才能用浏览器访问(集群内部的 pod 靠 `platform/coredns-custom/` 自动解析,不需要这一步)。当前用到的域名(启用一个组件才需要加对应那一行,不用一次性全加):

```
127.0.0.1 argocd.local-lite.test grafana.local-lite.test keycloak.local-lite.test jupyterhub.local-lite.test argo-workflows.local-lite.test permission-request.local-lite.test trino.local-lite.test superset.local-lite.test openmetadata.local-lite.test mlflow.local-lite.test spark-history.local-lite.test
```

**后续所有变更**(加组件、改配置、升级版本)都是:改 `platform/apps/*.yaml` 或 `apps/definitions/*.yaml` → commit → push,ArgoCD 自动同步,不需要再手动跑脚本或 `kubectl apply`。上面 7 步只在"一个全新的空集群"上需要做一次(第 7 步例外——见下面)。

### 组件专属的初始化脚本(不在上面 7 步里,按需跑)

这些脚本是各组件自己的命令式初始化,不归 GitOps 管(要么是账号/密码这类不该进 git 的东西,要么是官方 chart 就没提供声明式配置的能力)。**每次对应组件的 Application 第一次 Sync、或者它的 Deployment/StatefulSet 被整个重建(不是简单重启)时都要重新跑**,不是跑一次就永久生效:

| 脚本 | 做什么 | 什么时候要(重新)跑 |
|---|---|---|
| `scripts/03-configure-keycloak.sh` | 建 platform realm + 各组件的 OIDC client | 每接一个新的 SSO 组件之后都要重跑一次(幂等,已存在的 client/用户不会被覆盖)——比如 JupyterHub/Argo Workflows 的 client 就是它们的 Application 先建好自己的 namespace 之后,再跑这个脚本才能建成 |
| `scripts/12-sync-iam.py` | 把 `platform/iam/` 里的组织架构/角色数据同步进 Keycloak(Group/Role/成员) | 改了 `platform/iam/` 下任意文件之后 |
| `scripts/05-configure-airflow.sh` | 建 Airflow 初始管理员账号 | Airflow 从 `pending-definitions/` 拉回来、Deployment 第一次起来之后 |
| `scripts/06-configure-superset-datasources.sh` | 给 Superset 注册 Trino 数据源(服务账号认证) | Superset 或 Trino 任一个被重建之后 |
| `scripts/07-fix-trino-liveness-probe.sh` | 修 Trino chart 里硬编码错的 livenessProbe(见 ADR-017) | **每次** `trino-coordinator` 这个 Deployment 被重新创建(不是重启,是整个 Deployment 对象重建)都要重跑,否则会一直被 kubelet 杀死重启 |
| `scripts/10-install-kserve-serving-runtimes.sh` | 装 KServe 的 ClusterServingRuntime(sklearn/xgboost/mlserver 等,官方 chart 不带) | KServe 装完之后跑一次;这些是集群级资源,自身不占用运行时资源,不需要跟着组件重建反复重跑 |
| `scripts/14-configure-airflow-seatunnel-variable.sh` | 给 `seatunnel_device_events` 这个 DAG 写 MinIO 凭据(Airflow Variable) | Airflow 从 `pending-definitions/` 拉回来、webserver 第一次起来之后 |

### Demo / 演示脚本(可选,验证平台端到端能力用)

`scripts/08-create-demo-data.sh`(湖仓核心:Iceberg → Trino → Superset)、
`scripts/09-train-demo-model.sh`(AI/ML:训练 → MLflow 注册)、
`scripts/11-deploy-demo-inference-service.sh`(AI/ML:MLflow → KServe 上线)、
`scripts/13-run-spark-iceberg-demo.sh`(湖仓核心:Spark 通过 Spark Operator
读写 Iceberg,见 ADR-036)、
`scripts/15-create-device-events-dashboard.sh`(数据工程:SeaTunnel 写的表
在 Superset 建看板,见 ADR-037)——
这几个不是平台必需的初始化步骤,是用来验证端到端链路真的打通的演示脚本,
随时可以重跑重建,细节和已知坑见对应的 ADR。

## 文档地图

- [`docs/architecture.md`](docs/architecture.md) —— 架构总览、分层设计、组件清单、路线图(Phase 0-4)、还没定的设计决策
- [`docs/decisions/`](docs/decisions/) —— ADR,每个非显而易见的技术选择,包含理由、踩过的坑、后续更正(是不是验证过、验证到什么程度都写在里面,不是"我们决定这么做"就完了)
- [`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md) —— 真实踩过的坑,排障时先查这里
- [`docs/operations/upgrade.md`](docs/operations/upgrade.md) —— 当前版本清单 + 升级流程
- [`docs/operations/backup.md`](docs/operations/backup.md) —— 备份策略
- [`docs/operations/tuning.md`](docs/operations/tuning.md) —— 哪些参数预期要按自己情况调,调哪个文件

## 当前状态

Phase 0(平台底座)、Phase 1(湖仓核心)、Phase 2(数据工程:SeaTunnel → Iceberg → Airflow 调度 → Superset 看板)、Phase 3(AI/ML:JupyterHub/Argo Workflows/MLflow/KServe)核心链路均已验证;这台本机资源有限,验证过的组件按需收在 `pending-definitions/`,不是常驻全开。Kafka 单独验证过健康,还没接进端到端数据管道。企业级权限管理(组织架构同步、按组分角色)已落地,可插拔基础设施(Postgres/对象存储都已经推广到多个组件,见 ADR-030)持续推进中,细粒度数据权限(Trino 行列级)还没开始。完整的、持续更新的状态见 [`docs/architecture.md`](docs/architecture.md) 的路线图表格——这里不重复维护一份会过时的清单。
