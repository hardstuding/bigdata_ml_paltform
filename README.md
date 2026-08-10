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

- ✅ Phase 0(平台底座):ArgoCD + ingress-nginx + cert-manager + Keycloak + kube-prometheus-stack,全部 Synced/Healthy。真实 Ingress + 域名(`<组件>.local-lite.test`,见 ADR-016)替代了 port-forward,CoreDNS 自定义解析(`platform/coredns-custom/`)让集群内部 pod 也能统一解析这些域名
- ✅ Phase 1(湖仓核心):MinIO + Postgres + Hive Metastore + Trino,已验证端到端建表/写入/读出 Iceberg 表,数据真实落盘到 MinIO(Spark 侧读写还未验证,留到 Spark Operator 真正跑作业时一起做)
- ✅ **端到端 demo 打通了,Data 和 AI 两条主线都有**:
  - 湖仓核心:真实 Iceberg 表(`iceberg.demo.orders`)→ Trino(服务账号认证,见 ADR-021)→ Superset Dataset/Chart/Dashboard,走的是 Superset 真实的图表查询链路验证过。`./scripts/08-create-demo-data.sh` 一键重建,浏览器登录后开 `http://superset.local-lite.test/superset/dashboard/demo-lakehouse-core-path/` 能看到图
  - AI/ML:真实训练一个 sklearn 模型 → MLflow 记录实验/指标 → Model Registry 注册,Registry API 确认真的存在(见 ADR-023)。`./scripts/09-train-demo-model.sh` 一键重跑(本机 Python 环境需要 `pip install mlflow-skinny scikit-learn skops boto3`),浏览器登录后开 `http://mlflow.local-lite.test/` 能看到实验和模型
- ✅ Phase 2(数据工程,配置已验证、当前收在 `environments/cloud-full/pending-definitions/`):Kafka(Strimzi)、Spark Operator、Airflow、SeaTunnel
- ✅ OpenMetadata + OpenSearch:已验证功能可用,配置收在 `pending-definitions/`(需要时用 `scripts/local-lite-toggle-heavy.sh on` 拉回来,colima 内存已经从 6GB 扩到 9GB,同时跑的余地比之前宽松很多);MLflow 目前保持在线跑 demo
- ✅ **Keycloak SSO 已经打通 ArgoCD、Grafana、Trino、Superset、OpenMetadata、MLflow 六个组件**,统一登录。踩坑细节见 ADR-017(Trino,原生 OIDC 但强制要求 TLS,外加一个 chart 把 livenessProbe 打死端口的隐藏 bug)、ADR-019(MLflow,没有原生 OIDC,用 oauth2-proxy 挡在前面)、ADR-021(Superset 后端连 Trino 用 OAUTH2+PASSWORD 并存的服务账号,OAuth2 的 Authorization Code 模式是给人在浏览器操作设计的,不适合服务到服务)、troubleshooting.md(OpenMetadata 认证配置只在数据库首次初始化时生效、Superset 缺 authlib 包、组件重新拉起来常见的 Postgres 密码漂移问题)。浏览器完整登录待你在自己机器上加好 `/etc/hosts`(`argocd`/`grafana`/`trino`/`superset`/`openmetadata`/`mlflow`.local-lite.test → 127.0.0.1)后自己试一遍
- ✅ 本地镜像缓存(见 ADR-018):`scripts/list-project-images.py` 扫描出这个项目用到的全部镜像,`scripts/export-image-cache.sh` 导出到本地 `image-cache/`(git-ignored)——为公司内网出不去国外做准备,以后能直接搬这份缓存去内网机器 `docker load` + 推到公司内部仓库,不用重新连国外源拉
- ✅ 集中日志(见 ADR-020):Loki(SingleBinary)+ Grafana Alloy,8 个命名空间的日志已经真实进了 Loki,Grafana 加了 Loki 数据源,指标和日志能在同一个界面查。Alloy 踩了两个坑:`loki.source.kubernetes` 被本机代理拦截拉不到数据,改用 hostPath 读日志文件;`/var/log/pods` 里的日志文件是指向 docker 日志驱动实际存储位置的符号链接,要多开一个 mount 才行
- ✅ colima 内存从 6GB 扩到 9GB(本机是 16GB,还有余量),之前几乎每次装重组件都要精细监控内存、装完就收回去的紧张状态大幅缓解
- ✅ CI 校验(见 ADR-022):`.github/workflows/validate.yml`,push/PR 时跑 `scripts/validate-charts.py`(所有 Application 的 Helm chart 来源跑 `helm template`,纯 manifest 来源做 YAML 语法检查)。明确拦不住"渲染成功但运行时跑不起来"这类问题(这次踩的坑大部分是这类),只拦 chart 版本写错/字段名写错/YAML 语法错误这些
- ⏳ 还没碰:JupyterHub、Argo Workflows、KServe(Phase 3 剩余部分);用户行为日志分析(PostHog+ClickHouse)——见 `docs/decisions/`

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
