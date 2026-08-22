# scripts/ 导航

51 个文件,编号(`00-` 到 `26-`)**不等于执行顺序**——真正的执行顺序由
[`bootstrap-all.sh`](bootstrap-all.sh) 编排,编号只是历史上添加的先后。
这份导航按"你想干什么"分类,2026-08-21 的 P5 瘦身审计
(见 [`docs/BACKLOG.md`](../docs/BACKLOG.md) P5)整理。

> **审计结论(如实记录)**:这次逐个核对了全部 51 个文件,**没有找到
> 该删的死代码**——原本 BACKLOG 里的假设是"作为开源项目里面很多应该
> 是没用的",实际不成立:每个文件都对应一个真实用途,demo 脚本对开源
> 项目而言是"证明平台能力可复现"的资产不是负担。真正的问题是**可读性**
> (编号撑不住、五类东西混在一个平坦目录里),所以这次的产出是这份
> 导航,不是一批 `git rm`。

## 1. 从空集群拉起(部署主线)

**直接跑 [`bootstrap-all.sh`](bootstrap-all.sh) 就行**,它按正确顺序串起
下面这些,每一步都幂等,中途失败直接重跑整份脚本没有副作用。

| 脚本 | 作用 |
|---|---|
| `00-generate-secrets.sh` | 生成各组件管理员密码 + 建对应 Secret(幂等,不轮换已有密码) |
| `01-bootstrap-argocd.sh` | 装 ArgoCD 本身(唯一允许手动 helm install 的例外) |
| `02-bootstrap-root-apps.sh` | 把两个 app-of-apps 交给 ArgoCD,之后全部走 GitOps |
| `04-install-kube-prometheus-crds.sh` | kube-prometheus CRD 太大,ArgoCD 装不了,单独装 |
| `16-install-cloudnative-pg-crds.sh` | 同上,CloudNativePG 的 CRD |
| `25-install-argo-workflows-crds.sh` | 同上,Argo Workflows 的 CRD |
| `03-configure-keycloak.sh` | 建 platform realm + 各组件 OIDC client(**生成产物**,改要改 `templates/scripts/`) |
| `12-sync-iam.py` | `platform/iam/` 的组织架构/角色数据 → Keycloak |
| `05-configure-airflow.sh` | 建 Airflow 初始管理员 |
| `06-configure-superset-datasources.sh` | 给 Superset 注册 Trino 数据源 |
| `14-configure-airflow-seatunnel-variable.sh` | 给 SeaTunnel DAG 写 MinIO 凭据 |
| `07-fix-trino-liveness-probe.sh` | 修 chart 硬编码的坏探针(**现在是可选**:`apps/trino-liveness-fix/` 那个 CronJob 会自动巡检修复,这个脚本只是"不想等 5 分钟"的快捷方式) |
| `20-configure-openmetadata-search-truststore.sh` | OpenMetadata 连 OpenSearch 的自签证书信任 |

## 2. 端到端 demo / 能力验证

不是部署必需,是"证明这个平台真的能干活"的可复现示例。**开源项目里这类
脚本是资产**——新人靠它判断这个平台是不是真的能用,不是摆设。

| 脚本 | 验证哪条链路 |
|---|---|
| `08-create-demo-data.sh` | 湖仓核心:建真实 Iceberg 表(Trino → Hive Metastore → MinIO) |
| `13-run-spark-iceberg-demo.sh` | 批处理:Spark Operator 提交作业读写同一批 Iceberg 表 |
| `15-create-device-events-dashboard.sh` | 数据工程:SeaTunnel 写的表 → Superset 看板 |
| `18-table-registration-demo.sh` | 治理:自助建表注册 + 回写负责人/安全等级 |
| `19-feast-feature-pipeline.sh` | 特征:Iceberg → Spark 离线 → Redis 在线 → Feature Server 查询 |
| `09-train-demo-model.sh` | AI/ML:训练 sklearn 模型 → MLflow 注册 |
| `11-deploy-demo-inference-service.sh` | AI/ML:MLflow 模型 → KServe 上线推理 |
| `10-install-kserve-serving-runtimes.sh` | 上面那条的前置(装 ClusterServingRuntime) |

**下面三个是 pod 里跑的载荷,不是给人直接执行的**(通过 ConfigMap 挂进
容器,内容和 `apps/*/manifests/script-configmap.yaml` 保持同步):
`spark_iceberg_demo.py`、`train_demo_model.py`、`train_from_feast_features.py`

## 3. cloud-full 云主机生命周期 ⚠️ 涉及真实计费

| 脚本 | 作用 |
|---|---|
| `cloud-full-preflight.sh` | **操作云主机前先跑这个**——计费资源门禁检查 |
| `21-bootstrap-cloud-vm.sh` | 裸 ECS → 挂数据盘 + Docker + k3s(含 `--disable traefik`) |
| `22-load-image-cache-remote.sh` | 本机镜像缓存 → 云主机 |
| `23-pull-images-remote-via-mirror.sh` | 云主机直接走国内镜像源拉(通常比 22 快得多) |
| `24-install-idle-shutdown-watchdog.sh` | 空闲自动关机看门狗(**不进 git**,是个人环境脚本) |
| `26-stop-cloud-vm-economical.sh` | 停机并显式指定经济模式(`StoppedMode=StopCharging`) |
| `32-start-cloud-vm.sh` | 开机 + 拿新公网 IP + 重建 SSH 隧道 + 刷新 kubeconfig,四件事一条命令 |

> 注意:经济模式停机只停**计算**费用,**磁盘一直在计费**。

## 4. 本机 local-lite(colima)便利工具

只在这台 Mac 上有意义,和 cloud-full/prod 无关。

`local-lite-enable-swap.sh`、`local-lite-resize-colima-memory.sh`、
`local-lite-toggle-heavy.sh`(按 `enabled_components` 开关重组件)、
`local-lite-watch.sh`(后台记录集群状态)

## 5. 校验 / 生成 / CI

| 脚本 | 作用 | 接进 CI? |
|---|---|---|
| `validate-charts.py` | 所有 Application 跑 `helm template` | ✅ |
| `render-environment-config.py` | `environments/<env>/config.yaml` → 部署文件(变量替换 + 组件选择) | ✅ `--check` 防漂移 |
| `sync-airflow-dags-configmap.py` | `apps/airflow/dags/*.py` → ConfigMap | ✅ `--check` 防漂移 |
| `list-component-versions.py` | 汇总所有组件锁定的版本 | |
| `list-project-images.py` | 扫描全部用到的镜像 | |
| `check-manual-credentials.sh` | 扫出"哪些 Secret 需要人工填、现在缺哪个"(只读) | |
| `verify-image-digests.sh` | 校验已加载镜像的 digest 和官方一致(防镜像站投毒) | |

## 6. 运维 / 安全 / 迁移

| 脚本 | 作用 |
|---|---|
| `confirm-destructive-kubectl.sh` | 破坏性 kubectl 操作的防护层(历史上真误删过 namespace) |
| `restore-postgres-backup.sh` | 从 MinIO 里的备份恢复 Postgres |
| `set-repo-url.sh` | 迁移仓库地址(换 GitLab / 改名)时批量改 repoURL |
| `export-image-cache.sh` | 导出本机镜像缓存(arm64,给内网/离线环境) |
| `export-image-cache-amd64.sh` | 同上,x86_64 版本(给云主机/生产) |
| `export-image-cache-amd64-backfill.sh` | 补拉上面那个跳过的镜像(本地已有 tag 缓存会导致 `--platform` 不生效,按 digest 重拉绕开) |
| `17-load-image-cache.sh` | 把导出的缓存灌回本地 docker |
