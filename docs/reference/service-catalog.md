# 服务目录

> **这份文件是生成的,不要手改。**源码是 `platform/service-catalog.yaml`,
> 改完跑 `python3 scripts/check-service-catalog.py --write-doc` 重新生成。
> CI 会校验两者不漂移。

一个地方回答:这个服务是干什么的、谁负责、坏了要紧吗、出问题看哪里。
排障 Runbook(`troubleshooting.md`)回答的是「这个症状怎么处理」,
这份目录回答它前面那个问题:「这是什么、归谁、坏了影响谁」。

## 在请求路径上(它挂了,用户立刻感知)

| 服务 | 用途 | 负责组 |
|---|---|---|
| **hive-metastore** | Iceberg 表的元数据服务,Trino/Spark/Flink 共用同一份 | platform-team |
| **ingress-nginx** | 集群入口,所有从外面进来的 HTTP 请求都经过它 | platform-team |
| **keycloak** | 统一身份认证(OIDC),所有组件的登录都走它 | platform-team |
| **kserve-resources** | 在线推理服务的运行时(sklearn / xgboost / triton 等) | platform-team |
| **minio** | 对象存储,湖仓 warehouse / 模型产物 / Spark 事件日志 / 备份都在它上面 | platform-team |
| **opa** | Trino 的细粒度访问控制策略引擎(行列级 + 表级) | platform-team |
| **platform-portal** | 平台统一门户,列出所有工具入口 + 六条链路状态 + 我的作业 + 队列配额 | platform-team |
| **postgres** | 元数据库(Keycloak / Airflow / Superset / MLflow / OpenMetadata / HMS 共用) | platform-team |
| **trino** | 交互式 SQL 查询引擎,读写 Iceberg 湖仓;权限走 OPA | platform-team |

## AI/ML

| 服务 | 用途 | 负责组 | 面向用户 | 依赖 | 出问题看哪里 |
|---|---|---|---|---|---|
| **argo-workflows** | 训练/批处理的工作流编排,notebook 里 SDK 触发的就是它 | platform-team | 是 | keycloak、minio | BACKLOG 1.5(SSO RBAC 那次) |
| **feast** | 特征存储,离线读 Iceberg、在线走 Redis | platform-team | 否 | trino、minio、hive-metastore | ADR-042 |
| **jupyterhub** | 多用户 Notebook,镜像里带 platform_sdk,可直接提交作业 | platform-team | 是 | keycloak、argo-workflows、minio | ADR-058 |
| **kserve-resources** | 在线推理服务的运行时(sklearn / xgboost / triton 等)<br>deploymentMode 是 Standard(不装 Knative),所以做不了 canary 灰度 | platform-team | 否 | minio、mlflow | ADR-027 / ADR-075 |
| **mlflow** | 实验跟踪 + 模型注册表;上线前的审批章也打在这里 | platform-team | 是 | postgres、minio、keycloak | ADR-026 / ADR-080 |

## 入口

| 服务 | 用途 | 负责组 | 面向用户 | 依赖 | 出问题看哪里 |
|---|---|---|---|---|---|
| **ingress-nginx** | 集群入口,所有从外面进来的 HTTP 请求都经过它<br>cloud-full 上走 NodePort(和别的项目共享节点,抢不到 80/443) | platform-team | 否 | — | docs/operations/troubleshooting.md 的「网络与 Ingress 层」 |
| **keycloak** | 统一身份认证(OIDC),所有组件的登录都走它<br>它挂了等于整个平台登录不了,是全平台唯一的单点 | platform-team | 是 | postgres | docs/operations/troubleshooting.md 的「认证 SSO 层」 |
| **platform-portal** | 平台统一门户,列出所有工具入口 + 六条链路状态 + 我的作业 + 队列配额 | platform-team | 是 | keycloak、kueue、kube-prometheus-stack | docs/operations/troubleshooting.md 的「网络与 Ingress 层」 |

## 可观测

| 服务 | 用途 | 负责组 | 面向用户 | 依赖 | 出问题看哪里 |
|---|---|---|---|---|---|
| **alloy** | 日志采集 agent,把所有 pod 的 stdout 送进 Loki | platform-team | 否 | loki | ADR-020 |
| **golden-path-probes** | 六条真实业务链路的探针,回答「一件真实的事现在做不做得成」<br>探针失败会让这个 Application 黄一阵,下次跑通自愈(ADR-079 末尾) | platform-team | 否 | trino、openmetadata、mlflow、kserve-resources | ADR-079 |
| **kube-prometheus-stack** | 指标采集 + Alertmanager + Grafana,门户的链路状态也读它<br>cloud-full 保留 15 天 + 20Gi 持久卷;在 2026-08-28 之前是 6h + emptyDir,指标每次开机清零 | platform-team | 是 | keycloak | docs/operations/troubleshooting.md |
| **loki** | 日志聚合,和指标同一个 Grafana 界面 | platform-team | 否 | minio | ADR-020 / ADR-061 |
| **opencost** | 按命名空间/按组算成本,给管理驾驶舱和容量看板供数 | platform-team | 否 | — | ADR-069 |

## 平台

| 服务 | 用途 | 负责组 | 面向用户 | 依赖 | 出问题看哪里 |
|---|---|---|---|---|---|
| **cert-manager** | 证书签发。三档环境共用 platform-issuer 这个名字,背后接自签还是真实 CA 由环境配置决定 | platform-team | 否 | — | ADR-060 |
| **kueue** | 按组分配计算配额,同 cohort 内空闲可互借 | platform-team | 否 | — | ADR-064 |
| **platform-jobs** | git 里的定时作业(jobs/ 下写个 schedule 就会定时跑,不用写 DAG)<br>manifest 是生成物,不要手改;CI 校验它和 jobs/ 不漂移 | platform-team | 是 | argo-workflows、trino、kueue | jobs/README.md;生成器是 scripts/render-jobs.py |
| **postgres-backup** | 每天把 Postgres 全量备份传到 MinIO | platform-team | 否 | postgres、minio | ADR-033 |

## 数据

| 服务 | 用途 | 负责组 | 面向用户 | 依赖 | 出问题看哪里 |
|---|---|---|---|---|---|
| **airflow** | 任务调度(dbt / SeaTunnel / Feast 物化等 DAG) | platform-team | 是 | postgres、trino、minio | docs/operations/troubleshooting.md 的「各组件专属故障」 |
| **flink-streaming-demo** | 设备事件流式聚合写 Iceberg,验证流处理链路 | platform-team | 否 | kafka-cluster、hive-metastore、minio | ADR-062 |
| **hive-metastore** | Iceberg 表的元数据服务,Trino/Spark/Flink 共用同一份<br>版本锁 3.1.3,不能升 4.x(Iceberg 的 Hive 客户端只会发 get_table) | platform-team | 否 | postgres | apps/hive-metastore/manifests/deployment.yaml 顶部注释 |
| **kafka-cluster** | 消息总线,承载审计事件流和设备事件流 | platform-team | 否 | — | ADR-062 |
| **minio** | 对象存储,湖仓 warehouse / 模型产物 / Spark 事件日志 / 备份都在它上面<br>有 NetworkPolicy 白名单,新增消费方要同时加白名单(踩过三次) | platform-team | 否 | — | docs/operations/troubleshooting.md 的「存储与 S3A 层」 |
| **postgres** | 元数据库(Keycloak / Airflow / Superset / MLflow / OpenMetadata / HMS 共用)<br>CloudNativePG 管理;每天备份到 MinIO,但那不是异地备份 | platform-team | 否 | — | ADR-033(备份恢复) |
| **seatunnel** | 数据集成(把外部数据源搬进湖仓) | platform-team | 否 | minio、hive-metastore | ADR-054 |
| **spark-history-server** | 看已结束 Spark 作业的执行详情,日志读 s3a://spark-logs/ | platform-team | 是 | minio | ADR-036 |
| **superset** | BI 看板,数据源是 Trino;用 impersonation 继承 Trino 的表权限 | platform-team | 是 | trino、postgres、keycloak | ADR-077(汉化)/ ADR-051(权限) |
| **trino** | 交互式 SQL 查询引擎,读写 Iceberg 湖仓;权限走 OPA<br>startupProbe 预算 610s,机器满载时启动会超过两分钟(2026-08-29) | platform-team | 是 | hive-metastore、minio、opa、keycloak | docs/operations/troubleshooting.md 的「各组件专属故障」 |

## 治理

| 服务 | 用途 | 负责组 | 面向用户 | 依赖 | 出问题看哪里 |
|---|---|---|---|---|---|
| **flink-audit-sink** | 把 Trino 的查询审计从 Kafka 落进 Iceberg 审计表 | platform-team | 否 | kafka-cluster、hive-metastore、minio | ADR-066 |
| **iam-sync** | 把 platform/iam/ 里的组织架构同步进 Keycloak | platform-team | 否 | keycloak | scripts/12-sync-iam.py |
| **opa** | Trino 的细粒度访问控制策略引擎(行列级 + 表级) | platform-team | 否 | — | ADR-051 / ADR-078 |
| **openmetadata** | 数据目录 / 血缘 / 表安全等级标注 / 数据质量断言<br>采集管道的 CronJob 由它自己生成,startingDeadlineSeconds 要用 scripts/fix-openmetadata-cronjob-deadline.sh 放大,否则永不触发 | platform-team | 是 | postgres、opensearch、trino、minio | ADR-065 / ADR-070 / ADR-082 |
| **opensearch** | OpenMetadata 的搜索后端(目录里搜表靠它) | platform-team | 否 | — | scripts/20-configure-openmetadata-search-truststore.sh |
| **permission-request-app** | 表权限申请 + 分级审批 + 到期自动回收 | platform-team | 是 | postgres、keycloak、opa | ADR-044 / ADR-045 / ADR-050 |
| **schema-registry** | Karapace,Kafka 消息的 schema 契约与兼容性校验<br>Flink 作业还没接,schema 目前仍写死在 SQL 里 | platform-team | 否 | kafka-cluster | ADR-068 |
| **table-registration-app** | 建表登记 + 回写负责人/安全等级到目录 | platform-team | 是 | trino、openmetadata、keycloak | ADR-043 |

## 不单独立条目的支撑资源

写在这里是为了让「没登记」和「不需要登记」能区分开 —— 直接不写的话,
下次有人加了新组件忘了登记,检查器分不出是遗漏还是有意。

| 组件 | 归属 |
|---|---|
| `airflow-db-init.yaml` | 一次性建库 Job,归属 airflow |
| `alert-echo-sink.yaml` | 告警出口的验证终点,归属告警链路(ADR-081) |
| `argo-training-workflow-template.yaml` | WorkflowTemplate,归属 argo-workflows |
| `cloudnative-pg-operator.yaml` | operator,归属 postgres |
| `dbt-demo.yaml` | dbt 项目,归属 airflow 的 dbt_demo DAG |
| `flink-kubernetes-operator.yaml` | operator,归属两个 flink 作业 |
| `kafka-operator.yaml` | operator(Strimzi),归属 kafka-cluster |
| `kafka-producer-device-events.yaml` | 造数 CronJob,归属 flink-streaming-demo |
| `keycloak-db-init.yaml` | 一次性建库 Job,归属 keycloak |
| `kserve-crd.yaml` | CRD,归属 kserve-resources |
| `kserve-inference-monitoring.yaml` | PodMonitor,归属 kserve-resources(推理指标) |
| `kueue-queues.yaml` | 队列定义,归属 kueue |
| `mlflow-db-init.yaml` | 一次性建库 Job,归属 mlflow |
| `mlflow-oauth2-proxy.yaml` | 认证边车,归属 mlflow |
| `network-policies.yaml` | 集群级网络策略,归属平台本身 |
| `openmetadata-db-init.yaml` | 一次性建库 Job,归属 openmetadata |
| `openmetadata-quality-alerts.yaml` | 质量告警 CronJob,归属 openmetadata |
| `permission-request-app-oauth2-proxy.yaml` | 认证边车,归属 permission-request-app |
| `platform-iam-rbac.yaml` | RBAC,归属平台本身 |
| `platform-portal-oauth2-proxy.yaml` | 认证边车,归属 platform-portal |
| `platform-sdk-submitter-rbac.yaml` | RBAC,归属 jupyterhub 的 SDK 提交路径 |
| `platform/apps/alertmanager-notification.yaml` | 告警路由与出口配置,归属 kube-prometheus-stack |
| `platform/apps/cert-manager-issuers.yaml` | ClusterIssuer 定义,归属 cert-manager |
| `platform/apps/coredns-custom.yaml` | 集群内 DNS 补充记录,归属集群本身 |
| `platform/apps/grafana-audit-dashboard.yaml` | 看板定义,归属 kube-prometheus-stack |
| `platform/apps/grafana-capacity-dashboard.yaml` | 看板定义,归属 kube-prometheus-stack |
| `platform/apps/grafana-cost-dashboard.yaml` | 看板定义,归属 opencost |
| `platform/apps/grafana-goldenpath-dashboard.yaml` | 看板定义,归属 golden-path-probes |
| `platform/apps/grafana-inference-dashboard.yaml` | 看板定义,归属 kserve-resources |
| `platform/apps/grafana-overview-dashboard.yaml` | 看板定义,归属 kube-prometheus-stack |
| `platform/apps/prometheus-rules.yaml` | 告警规则,归属 kube-prometheus-stack |
| `resource-quotas.yaml` | 命名空间配额,归属平台本身 |
| `spark-history-server-oauth2-proxy.yaml` | 认证边车,归属 spark-history-server |
| `spark-operator.yaml` | operator,归属 spark 作业 |
| `superset-db-init.yaml` | 一次性建库 Job,归属 superset |
| `table-registration-app-oauth2-proxy.yaml` | 认证边车,归属 table-registration-app |
| `trino-groups.yaml` | Trino 的组成员文件,归属 trino |
| `trino-liveness-fix.yaml` | 巡检修复 CronJob,归属 trino |
| `trino-tls.yaml` | Trino 的证书,归属 trino |
