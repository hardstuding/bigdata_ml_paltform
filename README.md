# bigdata_ml_paltform

Kubernetes-native Data + AI Platform. 目标不是复刻 CDH,而是一套本地能验证、按 profile 切换规模、最终原样搬上生产的平台骨架,同时兼容现有的遗留 Hadoop 集群。

架构总览、组件清单、路线图见 [`docs/architecture.md`](docs/architecture.md);关键决策的取舍理由见 [`docs/decisions/`](docs/decisions);日常运维操作见 [`docs/operations/`](docs/operations)。

## 仓库结构

```
infra/           # 本地/云端集群自举(OrbStack + k8s、云端节点初始化)
platform/        # 平台底座:ArgoCD、ingress-nginx、cert-manager、Keycloak、监控
apps/            # 各业务组件,每个组件一个目录,独立 Helm/Kustomize 配置
environments/    # local-lite / cloud-full / prod 三个环境画像的 values 覆盖
scripts/         # 一键拉起 / 销毁 / 常用运维脚本
docs/            # 架构文档、ADR、运维手册 —— 权威版本,新会话/新 agent 靠这个对齐上下文
```

## 环境画像

| Profile | 用途 | 位置 |
|---|---|---|
| `local-lite` | 本机验证 GitOps 流程 + 存储/元数据打通 | Mac (M2/16GB, colima + k3s) |
| `cloud-full` | 功能完整的开发与集成环境 | 云服务器 |
| `prod` | 替换现有遗留大数据平台 | 生产环境 |

同一套 Helm chart,不同 `environments/<profile>/values.yaml` 决定开哪些组件、配多少资源。

## 当前状态

- ✅ Phase 0(平台底座):ArgoCD + ingress-nginx + cert-manager + Keycloak + kube-prometheus-stack,全部 Synced/Healthy
- ✅ Phase 1(湖仓核心):MinIO + Postgres + Hive Metastore + Trino,已验证端到端建表/写入/读出 Iceberg 表,数据真实落盘到 MinIO(Spark 侧读写还未验证,留到 Spark Operator 真正跑作业时一起做)+ **Superset**,已验证 `/health` 和登录页正常
- ✅ Phase 2(数据工程,配置已验证、当前收在 `environments/cloud-full/pending-definitions/`):Kafka(Strimzi)、Spark Operator、Airflow、SeaTunnel、Trino、Superset
- ⏳ 还没碰的:OpenMetadata(血缘/目录)——预计是目前所有组件里最复杂的一个(默认自带 OpenSearch + 自己的 MySQL/Airflow,需要单独规划)

详见 [`docs/architecture.md`](docs/architecture.md) 里的路线图,踩过的坑都记在 [`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md)。

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

# 4. 装 ArgoCD 本身(唯一一次手动 helm install,之后全部交给 GitOps)
#    本机+colima 这种需要过代理才能出网的环境,前面加 NEEDS_LOCAL_PROXY=1
./scripts/01-bootstrap-argocd.sh

# 5. 把两个 app-of-apps 交给 ArgoCD,后面所有组件的增删改都是 git push
./scripts/02-bootstrap-root-apps.sh

# 6. kube-prometheus-stack 的 CRD 太大,ArgoCD 应付不了,单独装一次
#    (只在第一次装、或者升级这个组件版本时需要跑)
./scripts/04-install-kube-prometheus-crds.sh
```

跑完用 `kubectl get applications -n argocd` 看所有组件是不是 `Synced`/`Healthy`。卡住了先查 [`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md),这台机器踩过的坑基本都记在那了。

**后续所有变更**(加组件、改配置、升级版本)都是:改 `platform/apps/*.yaml` 或 `apps/definitions/*.yaml` → commit → push,ArgoCD 自动同步,不需要再手动跑脚本或 `kubectl apply`。上面 6 步只在"一个全新的空集群"上需要做一次。
