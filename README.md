# bigdata_ml_paltform

[![Validate](https://github.com/hardstuding/bigdata_ml_paltform/actions/workflows/validate.yml/badge.svg)](https://github.com/hardstuding/bigdata_ml_paltform/actions/workflows/validate.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

Kubernetes-native 的 Data + AI 平台骨架:统一身份认证、GitOps 驱动的部署方式、按规模分级的环境画像(本机验证 → 云端集成 → 生产)。目标不是照抄一套 CDH,而是给还在用 YARN 时代大数据平台的团队一条能力对等、但运维模型现代化的迁移路径,同时兼容接入现有的遗留 Hadoop 集群做渐进迁移。

## 这个项目提供什么

- **一次登录,处处可用**:所有组件统一接 Keycloak SSO(OIDC),角色/权限按组织架构里的组(group)分发,不是每个组件单独一套账号体系。
- **GitOps 是唯一的操作接口**:改一个 YAML、`git push`,ArgoCD 自动把集群状态收敛过去。没有隐藏在某个人电脑里的手动步骤——这也是为什么这套平台对 AI Agent 友好:Agent 能做的操作和人能做的操作是同一套(改 git、看 ArgoCD 状态),不需要给 Agent 开额外的特权通道。
- **按规模分级,不是"要么全装要么不装"**:`local-lite`(笔记本电脑验证)/ `cloud-full`(功能完整的集成环境)/ `prod`(生产)三档,同一套组件定义,资源画像不同。
- **可插拔基础设施**:公司已经有 Postgres/Kafka/对象存储了?不强制重新部署一份,配置里标出了怎么改接现有的(见 [ADR-030](docs/decisions/030-pluggable-external-infrastructure.md))。
- **只用官方支持的部署方式**:不用 Bitnami 或来源不明的社区 chart,没有官方 Helm chart 的组件才自己写 manifest(见 [ADR-008](docs/decisions/008-avoid-bitnami.md))。每个决策的取舍理由都留了 ADR,不是"我们就是这么做的",是"为什么这么做、还有什么后果"。

## 文档

不知道该看哪一份,从 **[`docs/README.md`](docs/README.md)** 进 —— 它按
"你现在想干什么"分了四类(上手 / 使用 / 运维 / 设计取舍)。

## 第一次接触这个平台?

先看 [`docs/getting-started.md`](docs/getting-started.md) —— 半小时亲手跑通一条
"数据进来 → 能查 → 能看 → 能训练 → 能上线"的完整链路,先有整体感觉,
再看细节。下面这段"快速上手"是**部署**用的,不是使用引导。

## 快速上手(部署)

> **完全没接触过这个项目(人或 AI)?直接看
> [`docs/operations/deploy-from-scratch.md`](docs/operations/deploy-from-scratch.md)**
> —— 那份从"你要先准备什么"开始,一路到"怎么确认真的能用",不用先读别的。
> 下面这段是给已经熟悉的人看的速查。

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
environments/     # local-lite / cloud-full / prod 三个环境画像;每个环境的 config.yaml 里 enabled_components 列表决定这个环境要哪些组件(apps/components/ 是全部组件的源码)
scripts/          # 一键拉起 / 常用运维 / 校验脚本 —— 51 个文件的分类导航见 scripts/README.md(编号不等于执行顺序)
docs/
  README.md         # 文档索引 —— 不知道该看哪份就从这里进
  getting-started.md  # 半小时跑通一条完整链路
  usage-guide.md    # 按角色的使用说明(查数 / 跑作业 / 训模型 / 建表)
  architecture.md   # 架构总览、分层、选型原则
  reference/        # 参照类:服务目录(每个服务是什么、归谁、坏了影响谁)
  operations/       # 运维:Runbook、备份恢复、升级、调优、入离职
  decisions/        # ADR —— 每个非显而易见的技术决策,连同踩过的坑
  project/          # 项目自身的过程记录(进度、待办、评审)——不是使用文档
  journal/          # 按月的排障叙事归档
```

> **`docs/project/` 和其它目录是刻意分开的**:它回答"我们做到哪了、
> 接下来做什么",而不是"这个平台怎么用"。找使用说明不用进那个目录。

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

**一键版本**:下面手动的 7+ 步(含之前漏记的 argo-workflows CRD、Trino
探针修复这些)现在串成了一个脚本,`scripts/bootstrap-all.sh`——2026-08-16
在 cloud-full 上真实跑过全流程验证(对着一个已经完全跑起来的集群幂等重跑
一遍,14 步全部成功,零失败),不是纸面设计。核心步骤(装 ArgoCD、拉起
root-apps、装各种 CRD、配 Keycloak)任何一步失败都会让脚本停下来,后面
"组件专属初始化"那几步(建 Airflow 账号、配 Superset 数据源等)是尽力
而为——对应组件如果还是 park 状态会自动跳过,不会拖垮整个脚本:

```bash
git clone <这个仓库的地址> bigdata_ml_paltform && cd bigdata_ml_paltform
./scripts/bootstrap-all.sh
```

装别的环境画像用 `TARGET_ENV`(默认 `cloud-full`):

```bash
TARGET_ENV=local-lite NEEDS_LOCAL_PROXY=1 ./scripts/bootstrap-all.sh
```

脚本第一步会校验这个工作区当前渲染的就是 `TARGET_ENV` 那个环境
——`apps/definitions/` 和 `platform/apps/` 是渲染产物,同一时刻只能代表
一个环境,拿着 local-lite 的渲染结果去部署 cloud-full,每个 Pod 都会
Running、ArgoCD 全绿,但装出来的是错的那套。不一致时脚本会停下来告诉你
该跑什么,不会自动帮你渲染(渲染改的是本地文件,ArgoCD 读的是 git 远端,
不 commit+push 不生效,自动渲染只会制造"我明明渲染过了"的错觉)。

完整执行日志在 `logs/bootstrap-all.log`(不进 git)。中途失败了直接重跑
这份脚本就行——每一步各自的脚本本来就是幂等的,重跑不会产生副作用。

**手动逐步版本**(想理解每一步在做什么、或者调试某一步卡住时用):

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

# 6.7 argo-workflows 的 CRD 已经 vendor 进仓库(见脚本头部注释,原来
#     chart 靠一个 pre-install Job 运行时下载,cloud-full 连不上代理地址
#     会卡死),单独装一次,不依赖任何网络。
./scripts/25-install-argo-workflows-crds.sh

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
127.0.0.1 argocd.local-lite.test grafana.local-lite.test keycloak.local-lite.test jupyterhub.local-lite.test argo-workflows.local-lite.test permission-request.local-lite.test table-registration.local-lite.test portal.local-lite.test trino.local-lite.test superset.local-lite.test openmetadata.local-lite.test mlflow.local-lite.test spark-history.local-lite.test
```

**后续所有变更**(加组件、改配置、升级版本)都是:改 `apps/components/*.yaml` 或 `templates/` 下的源 → 重新渲染 → commit → push,ArgoCD 自动同步,不需要再手动跑脚本或 `kubectl apply`。

上面这些只在"一个全新的空集群"上做一次。**完整、按真实执行顺序、标了必需/尽力的那份清单在 [`scripts/README.md` 的「从空集群拉起」](scripts/README.md#1-从空集群拉起部署主线)** —— 那张表由 `scripts/check-bootstrap-coverage.py` 保证和 `bootstrap-all.sh` 一致,这里这份手动版只是给想理解每一步在做什么的人看的。

### 组件专属的初始化脚本(什么时候要**重新**跑)

**这些现在都已经在 `bootstrap-all.sh` 里了**,全新集群不用单独跑。这张表
回答的是另一个问题:**它们什么时候需要重新跑一次**。

这些是各组件自己的命令式初始化,不归 GitOps 管(要么是账号/密码这类不该
进 git 的东西,要么是官方 chart 就没提供声明式配置的能力)。**每次对应
组件的 Application 第一次 Sync、或者它的 Deployment/StatefulSet 被整个重建
(不是简单重启)时都要重新跑**,不是跑一次就永久生效:

| 脚本 | 做什么 | 什么时候要(重新)跑 |
|---|---|---|
| `scripts/03-configure-keycloak.sh` | 建 platform realm + 各组件的 OIDC client | 每接一个新的 SSO 组件之后都要重跑一次(幂等,已存在的 client/用户不会被覆盖)——比如 JupyterHub/Argo Workflows 的 client 就是它们的 Application 先建好自己的 namespace 之后,再跑这个脚本才能建成 |
| `scripts/12-sync-iam.py` | 把 `platform/iam/` 里的组织架构/角色数据同步进 Keycloak(Group/Role/成员) | 改了 `platform/iam/` 下任意文件之后 |
| `scripts/05-configure-airflow.sh` | 建 Airflow 初始管理员账号 | Airflow 从 `enabled_components` 里启用、Deployment 第一次起来之后 |
| `scripts/06-configure-superset-datasources.sh` | 给 Superset 注册 Trino 数据源(服务账号认证) | Superset 或 Trino 任一个被重建之后 |
| `scripts/07-fix-trino-liveness-probe.sh` | 修 Trino chart 里硬编码错的 livenessProbe(见 ADR-017) | 可选的立即手动修复快捷方式——`apps/trino-liveness-fix/` 这个 CronJob(2026-08-20 起)每 5 分钟自动巡检并修复,`trino-coordinator` 被重新创建后不手动跑这个脚本也会在几分钟内自愈,见 docs/project/roadmap.md 2.3 |
| `scripts/10-install-kserve-serving-runtimes.sh` | 装 KServe 的 ClusterServingRuntime(sklearn/xgboost/mlserver 等,官方 chart 不带) | **2026-08-21 起已并入 `bootstrap-all.sh`,不用单独跑**。不装的话 KServe 起来了但一个 runtime 都没有,要等真去上线模型才发现 |
| `scripts/20-configure-openmetadata-search-truststore.sh` | 让 OpenMetadata 信任 OpenSearch 的自签证书 | **2026-08-21 起已并入 `bootstrap-all.sh`**。不跑的话 OpenMetadata 连不上 OpenSearch,搜索/目录是坏的,但首页能打开,容易被误判成部署成功 |
| `scripts/14-configure-airflow-seatunnel-variable.sh` | 给 `seatunnel_device_events` 这个 DAG 写 MinIO 凭据(Airflow Variable) | Airflow 从 `enabled_components` 里启用、webserver 第一次起来之后 |
| `scripts/45-configure-acr-pull.sh` | 给各命名空间配私有镜像仓库的拉取凭据 | **新增一个命名空间之后**(它按命名空间逐个建 Secret 并 patch ServiceAccount,新 namespace 不会自动有)。ACR 凭据本身要人工提供一次,见 [`docs/operations/image-registry.md`](docs/operations/image-registry.md) |
| `scripts/34-configure-openmetadata-data-quality.sh` | 建数据质量断言 | OpenMetadata 重建之后,或者要给新表加断言时 |
| `scripts/43-configure-openmetadata-dbt-ingestion.sh` | dbt 血缘接进目录 | 同上。**注意顺序**:元数据采集(`29`)必须先跑过,否则这一步会报 `Success 100%` 而血缘一条都没建(表还没进目录,边无处可挂) |

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

- **想知道"这个平台现在到底能用来干什么" → [`docs/project/capability-matrix.md`](docs/project/capability-matrix.md)** —— 五个角色(分析师/大数据开发/算法/运维/管理)× 完整工作链路 × 每一环今天的真实状态。**这是"我们做到哪了"的唯一权威入口**,衡量标准是"某个岗位能不能独立完成一件真实工作",不是"部署了哪些组件"(见 [ADR-057](docs/decisions/057-architecture-review-2026-08-19.md))
- **参与开发** → [`CONTRIBUTING.md`](CONTRIBUTING.md);仓库自身的工程约定(包括给 AI 助手的协作规则)在 [`CLAUDE.md`](CLAUDE.md)
- **实际使用这套平台,先打开 `http://portal.local-lite.test`**(ADR-047)—— 统一门户,现在有哪些工具、分别是干什么的、点哪里进去,一个页面看完;各工具共用同一个 Keycloak SSO,登录一次到处能用,不用重复输密码
- [`docs/usage-guide.md`](docs/usage-guide.md) —— 按角色和任务组织的使用指南,
  每节统一成**前置条件 → 操作 → 预期结果 → 常见失败**。不是运维文档
- [`docs/project/roadmap.md`](docs/project/roadmap.md) —— 记下来但先不做的想法,按优先级排,不打断当前主线
- [`docs/architecture.md`](docs/architecture.md) —— 架构总览、分层设计、组件清单、路线图(Phase 0-4)、还没定的设计决策
- [`docs/decisions/README.md`](docs/decisions/README.md) —— **ADR 主题索引**(按平台底座/湖仓/SSO/权限治理/可观测性等分组,带验证状态)。**这里刻意不写份数** —— 写了就会过期(原来写的是 57,实际早就 80 多了),而一个会过期的数字对读者没有任何用处
- [`docs/decisions/`](docs/decisions/) —— ADR 原文,每个非显而易见的技术选择,包含理由、踩过的坑、后续更正(是不是验证过、验证到什么程度都写在里面,不是"我们决定这么做"就完了)
- [`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md) —— 真实踩过的坑,排障时先查这里
- [`docs/journal/`](docs/journal/) —— 按月归档的排障与验证日志(2026-08-19 从 `project/current-work.md` 拆出来的,那份文件只留当前状态)
- [`docs/operations/upgrade.md`](docs/operations/upgrade.md) —— 当前版本清单 + 升级流程
- [`docs/operations/backup.md`](docs/operations/backup.md) —— 备份策略
- [`docs/operations/tuning.md`](docs/operations/tuning.md) —— 哪些参数预期要按自己情况调,调哪个文件
- [`docs/operations/onboarding-offboarding.md`](docs/operations/onboarding-offboarding.md) —— 新人怎么拿到权限、人离开时怎么收回,这套机制覆盖到哪、覆盖不到哪
- [`environments/cloud-full/README.md`](environments/cloud-full/README.md)、[`environments/prod/README.md`](environments/prod/README.md) —— 接入 cloud-full/prod 硬件时的资源规划参考(不是自动生效的配置,现状和取舍原因见文档开头)
- [`CONTRIBUTING.md`](CONTRIBUTING.md) —— 想贡献代码/提 PR,先看这个
- [`SECURITY.md`](SECURITY.md) —— 安全问题怎么报告,当前已知的安全假设

## 当前状态

**权威来源是 [`docs/project/capability-matrix.md`](docs/project/capability-matrix.md)** —— 这里不维护第二份会
过时的进度清单(2026-08-19 起,见
[ADR-057](docs/decisions/057-architecture-review-2026-08-19.md))。那份表
每一行带**验证级别**(生产验证 / 集成验证 / demo / 未验证)、最后验证时间
和证据链接,并且有 CI 检查拦着"没验就标已完成"。

一句话现状:**五个角色里四个能独立开工** —— 运维、数据分析师、
大数据开发、算法工程师;管理角色只有第一版驾驶舱。**没有任何一格是"生产
验证"** —— 这套东西还没上过生产,门禁条件写在
[`docs/project/production-readiness-gaps.md`](docs/project/production-readiness-gaps.md)。

底座部分已经比较扎实:GitOps 单一变更入口、从空集群一键拉起(真的推倒
重建跑通过,ADR-039)、企业级权限治理全链路(分级审批/到期回收/权限交接,
ADR-044/045/050)、Trino 细粒度访问控制已正式生效(ADR-051)、指标+日志+
告警+每日备份(含恢复演练)都在。

**最大的一条债务是"没上过生产"** —— 所有结论都来自一台单节点云主机,
门禁条件逐条列在
[`production-readiness-gaps.md`](docs/project/production-readiness-gaps.md)。
其余待办按优先级排在
[`roadmap.md`](docs/project/roadmap.md)。
