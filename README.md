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
- ✅ OpenMetadata + OpenSearch:已验证 web 应用正常响应。Postgres 后端(不用默认 MySQL),采集编排用官方 k8s 模式(不用 Airflow,见 ADR)
- ✅ MLflow:官方 OCI chart,已验证 health check + 真实建实验 API 调用,Postgres 后端 + MinIO 存 artifact
- ✅ 真实 Ingress + 域名(`<组件>.local-lite.test`,见 ADR-016):ArgoCD、Keycloak、Grafana 已切换,不再用 port-forward。Keycloak 顺手接上了共享 Postgres(之前的临时 H2 一重启就把 realm 全部丢光)
- ✅ Trino 接 Keycloak OAuth2 SSO(见 ADR-017):OAuth2 授权跳转已验证(正确的 client_id/redirect_uri)。踩坑清单里的教训(TLS 强制要求、internal-communication、X-Forwarded 信任)是 Trino 专属的,Superset 那边基本不适用
- ✅ Superset 接 Keycloak OAuth2 SSO:比 Trino 简单得多(Flask-AppBuilder 的 OAuth 不要求自己起 HTTPS),`/login/keycloak` 跳转已验证(正确的 client_id/redirect_uri)。踩了一个新坑:`AUTH_TYPE = AUTH_OAUTH` 这条代码路径需要 `authlib` 包,基础镜像不带,和当初 psycopg2 一样得在 bootstrapScript 里手动装
- ✅ CoreDNS 自定义解析(`platform/coredns-custom/`):集群内部 pod 现在能统一解析 `*.local-lite.test`,不用再给每个 chart 单独想 hostAliases 的办法
- 浏览器完整登录(不只是服务端跳转)待你在自己机器上加好 `/etc/hosts`(`argocd`/`grafana`/`trino`/`superset`.local-lite.test → 127.0.0.1)后自己试一遍
- ✅ 本地镜像缓存(见 ADR-018):`scripts/list-project-images.py` 扫描出这个项目用到的全部镜像,`scripts/export-image-cache.sh` 导出到本地 `image-cache/`(git-ignored)——为公司内网出不去国外做准备,以后能直接搬这份缓存去内网机器 `docker load` + 推到公司内部仓库,不用重新连国外源拉
- ⏳ 还没碰:JupyterHub、Argo Workflows、KServe(Phase 3 剩余部分);OpenMetadata/MLflow 的 Ingress 域名 + Keycloak SSO(下一步);端到端 demo 链路;用户行为日志分析——当前优先级是"打通已验证组件之间的关系",不是继续加新组件,见 `docs/decisions/`

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
