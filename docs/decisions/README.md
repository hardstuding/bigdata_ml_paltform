# ADR 索引

每个非显而易见的技术选择都有一份 ADR,记录理由、踩过的坑、以及后续
更正。**ADR 里"状态"一栏写的是验证到什么程度**(设计完成 / 已部署 /
已实测),不是"我们决定这么做"就完了——读的时候先看状态。

- 想知道**某个角色今天能做什么** → [`../project/capability-matrix.md`](../project/capability-matrix.md)
- 想知道**现在在做什么** → [`../project/current-work.md`](../project/current-work.md)
- 想知道**架构全貌** → [`../architecture.md`](../architecture.md)

> 编号连续性:**没有 ADR-049**(编号跳过,不是文件丢失)。

## 元:项目方向与评审

| # | 标题 | 状态 |
|---|---|---|
| [057](057-architecture-review-2026-08-19.md) | 架构评估与结构性调整:从"组件视角"转到"角色视角" | 已采纳,分三批,**第一批(文档重组)已完成** |
| [055](055-external-review-response-2026-08-15.md) | 对外部(Codex)项目评审的响应 | 已采纳(部分执行,部分明确延后) |
| [013](013-production-capacity-baseline.md) | 生产容量基线(参考数据,非决策) | 记录 |

## 平台底座与基础设施选型

| # | 标题 | 状态 |
|---|---|---|
| [001](001-kubernetes-colima.md) | 用 Kubernetes 做统一调度层,本地 colima + k3s | 已采纳 |
| [004](004-environment-profiles.md) | 用环境画像而不是分叉代码适配不同规模 | 已采纳,**2026-08-14 修正过落地机制**(另见 ADR-057) |
| [005](005-argocd-gitops.md) | ArgoCD + GitOps 作为唯一变更入口 | 已采纳 |
| [008](008-avoid-bitnami.md) | 全项目避开 Bitnami 镜像与 chart | 已采纳 |
| [010](010-optional-components-versioning.md) | 组件可选可替换,版本锁定并记录升级路径 | 已采纳 |
| [018](018-local-image-cache.md) | 本地镜像缓存 + 导出(为内网出不去做准备) | 已采纳 |
| [030](030-pluggable-external-infrastructure.md) | 可插拔基础设施:允许接公司已有的 Postgres/Kafka/对象存储/SSO | 已采纳,Postgres 已推广到多组件 |
| [054](054-cloud-full-bare-vm-bootstrap.md) | cloud-full 裸机引导流程(也适用自建 IDC) | 进行中,持续更新 |

## 湖仓与数据工程

| # | 标题 | 状态 |
|---|---|---|
| [002](002-iceberg-lakehouse.md) | 表格式用 Iceberg,不锁死在引擎私有格式 | 已采纳 |
| [003](003-no-hdfs-on-k8s.md) | 不在 k8s 里重建 HDFS/YARN,联邦查询遗留集群 | 已采纳 |
| [007](007-kafka-not-redpanda.md) | 消息队列用 Kafka(不是 Redpanda) | 已采纳 |
| [011](011-seatunnel-not-airbyte.md) | 数据集成用 SeaTunnel(不是 Airbyte) | 已采纳 |
| [036](036-spark-iceberg-pipeline.md) | Spark 读写 Iceberg 端到端验证 | 已验证 |
| [037](037-data-engineering-pipeline.md) | SeaTunnel → Iceberg → Airflow → Superset 端到端 | 已验证 |
| [056](056-flink-role-design.md) | Flink 在这套架构里的角色 | **设计完成,没有部署任何东西** |
| [062](062-flink-streaming-pipeline.md) | Flink 流式计算实现:Kafka -> Flink -> Iceberg | 代码/manifest 完成,**未在真实集群验证** |

## 数据目录、血缘与分析师工具

| # | 标题 | 状态 |
|---|---|---|
| [015](015-openmetadata-architecture.md) | OpenMetadata:Postgres 后端 + k8s 原生采集编排 | 已验证(**cloud-full 上未部署**,见 project/capability-matrix.md) |
| [014](014-spark-lineage-official-agent.md) | Spark 血缘用官方 agent,不自己解析 SQL | **设计成立但 artifact 需重选** —— 选定的 openmetadata-spark-agent 只有 2024 年的 1.0-beta(Java 11 + OpenLineage 1.7),不认 Spark 4;Spark 血缘至今没落地 |
| [052](052-seatunnel-lineage.md) | SeaTunnel 表级血缘推 OpenMetadata | 已实现,API 机制已验证 |
| [012](012-dbt-analyst-platform.md) | 分析师开发平台:dbt + Cosmos + OpenMetadata | 方向已定 |
| [053](053-dbt-analyst-platform-mvp.md) | dbt MVP:在 Trino/Iceberg 上跑,先不接 Cosmos | 最小骨架已实现 |

## 身份认证与 SSO

| # | 标题 | 状态 |
|---|---|---|
| [009](009-keycloak-oidc-integration.md) | ArgoCD / Grafana 接 Keycloak OIDC | 已采纳 |
| [016](016-ingress-domains-local-lite.md) | 真实 Ingress + 静态域名替代 port-forward | 已验证 |
| [017](017-trino-oauth2-sso.md) | Trino 接 Keycloak OAuth2(比别的组件麻烦得多的原因) | 已验证 |
| [019](019-mlflow-oauth2-proxy-sso.md) | MLflow 用 oauth2-proxy 挡在前面接 SSO | 已验证 |
| [021](021-trino-service-account-auth.md) | Trino 服务账号:OAUTH2 + PASSWORD 并存 | 已采纳 |
| [023](023-mlflow-training-demo-service-access.md) | 训练任务直连内部 Service,不走 oauth2-proxy | 已验证 |
| [025](025-jupyterhub-sso.md) | JupyterHub 接 Keycloak SSO | 已用真实浏览器验证 |
| [026](026-argo-workflows-sso.md) | Argo Workflows 接 SSO + CRD 安装的网络坑 | 已验证(**登录从没真实跑过**,见 CURRENT_WORK) |

> 2026-08-16 cloud-full 上这一整块出过一次四层连环故障(NodePort 端口 +
> Keycloak hostname 推断 + issuer/backchannel 地址冲突 + nginx 缓冲区),
> 根因和修法记在 [`../journal/2026-08.md`](../journal/2026-08.md),不在
> 上面这些 ADR 里。

## 权限治理与 IAM

| # | 标题 | 状态 |
|---|---|---|
| [006](006-ai-agent-identity-v1.md) | AI Agent 身份 v1:从简 | 已采纳 |
| [028](028-iam-org-model.md) | 组织架构/角色数据模型 + Keycloak Group 同步 | 已验证 |
| [031](031-iam-auto-sync-cronjob.md) | IAM 自动同步 CronJob | 已验证 |
| [032](032-permission-request-app.md) | 权限自助申请门户 | 已部署 |
| [040](040-enterprise-governance-roadmap.md) | 企业级治理需求归档(表权限/血缘/审批/隔离) | 记录归档 |
| [043](043-table-registration-tool.md) | 建表注册工具(OA 审批 Phase 1) | 已部署 |
| [044](044-tiered-approval-workflow.md) | 分级审批工作流(Phase 2) | 已实现,端到端验证 |
| [045](045-approval-backend-notifications-escalation.md) | 可插拔审批后端 + 通知/升级/交接/审计(Phase 3) | 已实现 |
| [046](046-catalog-browse-for-table-access.md) | 申请改成浏览目录勾选 | 已实现(**体验依赖 OpenMetadata**) |
| [050](050-grant-expiry-reclamation.md) | 表访问授权到期回收 | 已实测 |
| [051](051-trino-opa-access-control.md) | Trino 细粒度访问控制:OPA 策略引擎 | **2026-08-16 已正式生效** |
| [063](063-trino-column-row-level-security.md) | 敏感字段行列级策略:Trino OPA 列级脱敏 + 行级过滤 | 策略/测试完成,`opa test` 28/28;未在真实集群验证 |
| [048](048-ai-operator-role.md) | AI 运维角色:独立身份 + 阶段性收紧 + 危险操作审批 | **部分实现**(开发阶段 RBAC 已实测,收紧未做) |

## AI / ML

| # | 标题 | 状态 |
|---|---|---|
| [027](027-kserve-model-serving.md) | KServe 模型上线服务 | 已验证 |
| [042](042-feast-feature-store.md) | Feast 特征存储 | 已验证 |
| [058](058-lightweight-developer-experience.md) | 开发者体验:薄 SDK + 脚手架 + skill,不自建平台 UI | 第一、二批已验证,第三批(skill)未经真实使用验证 |
| [059](059-resource-profiles.md) | 规格分档:同一份组件定义,三个环境不同资源规格 | 机制已实现并验证(2026-08-21),helm 类和裸 manifest 类各有一个样本(Trino、Postgres HA),其余组件按需铺开 |

## 可观测性、可靠性与运维

| # | 标题 | 状态 |
|---|---|---|
| [020](020-centralized-logging-loki-alloy.md) | 集中日志:Loki + Alloy(不用 Promtail) | 已验证 |
| [024](024-platform-audit-logging.md) | 平台审计日志复用 Loki,不新增平台 | 已验证 |
| [034](034-alertmanager.md) | 打开 Alertmanager | 已采纳(**没配外部通知渠道**) |
| [029](029-spark-permissions-and-observability.md) | Spark 权限 + 可观测性(YARN 的替代方案) | 配置就绪,未验证 |
| [033](033-postgres-backup.md) | 共享 Postgres 每日自动备份 | 已验证(含恢复演练) |
| [035](035-network-policy.md) | NetworkPolicy | 已推广到核心命名空间 |
| [038](038-cloudnativepg-evaluation.md) | CloudNativePG:给共享 Postgres 找 HA 升级路径 | 已完成迁移和切流量 |
| [039](039-teardown-rebuild-test.md) | 推倒重建测试:验证"一键部署"是真的 | local-lite 2026-08-13 / cloud-full 2026-08-22,都通过 |
| [041](041-queue-resource-management.md) | ResourceQuota + LimitRange + PriorityClass | 已验证 |
| [064](064-role-based-resource-quota.md) | 按组分配计算配额 + 空闲互借(Kueue) | **已实机验证**(cloud-full,2026-08-23) |
| [065](065-data-quality-on-openmetadata.md) | 数据质量断言复用 OpenMetadata | 已实机验证(cloud-full) |
| [066](066-trino-query-audit.md) | Trino 查询审计走 Kafka event listener | **已实机验证**(2026-08-24):Trino → Kafka → Flink → Iceberg 全链路。`audit.query_events` 一次查询一行、`audit.query_table_access` 一次表访问一行,实测 3 条查询 → 5 行事件 / 4 行表访问。2026-08-30 补了 `audit` 黄金链路探针(待实机验证) |
| [067](067-portal-as-workbench.md) | 门户从链接目录改成工作台 | **已上线并实机验证**(2026-08-30):按角色显示工具、我的表权限、待我审批、作业详情页 |
| [068](068-schema-registry.md) | Schema Registry 选 Karapace | **已部署并实机验证**(2026-08-25):加可选字段放行、`double`→`string` 被 409 拦在发送侧。**Flink 作业还没接上它**,schema 仍写死在 SQL 里 |
| [069](069-cost-attribution.md) | 成本归属用 OpenCost,单价自己填 | **已部署并实机出数**(2026-08-26):29 个命名空间合计 ¥0.808/小时;按组的 PromQL 在真集群验过。**管理视角(按月聚合、和预算对比)仍未做** |
| [070](070-data-freshness-slo.md) | 新鲜度当成一条数据质量断言 | **检测已实机验证**(2026-08-23);**告警出口 2026-08-28 打通**(ADR-081) |
| [071](071-platform-alert-rules.md) | 平台自定义告警规则 + Flink 作业级指标 | **已部署并生效**(2026-08-19 起,规则抓到过真实问题)。2026-08-30 修正了 `FlinkMetricsMissing` 一条名不副实的注释 |
| [072](072-openmetadata-2-upgrade.md) | OpenMetadata 升到 2.0.0 大版本 | **已升级并验证通过**(cloud-full) |
| [073](073-quality-alerts-to-alertmanager.md) | 质量/新鲜度断言结果推给 Alertmanager | 已实现,**未部署验证**(逻辑用假数据在本地跑过) |
| [074](074-superset-impersonation.md) | Superset 透传登录用户身份给 Trino | **已上线并实机验证**(2026-08-27);盘表已做,影响面确认极小 |
| [075](075-kserve-runtime-matrix.md) | KServe runtime 精简到 4 个,其余进 optional/ | **已部署**;默认那 4 个 runtime 由 `inference` 黄金链路探针持续验证(2026-08-29 实测通过) |
| [076](076-spark-4-evaluation.md) | Spark 4.x 评估 | **已执行** |
| [077](077-superset-chinese-ui.md) | Superset 汉化 | **已构建并实机验证**(2026-08-28):language pack 加载成功,4054 条译文 |
| [078](078-trino-group-provider.md) | Trino file group provider | **已实现并实机验证通过** |
| [079](079-golden-path-probes.md) | 按链路探测(D 线第一步)+ 黄金链路看板 | **已部署并实机验证**(2026-08-29:六条探针最近一次全部 Completed) |
| [080](080-model-approval-and-rollback.md) | 模型上线审批 + 回滚(C 线第一步) | **已实机验证**(2026-08-28):训练→注册→审批→部署→真实推理→回滚守卫,全链路 |
| [081](081-alert-delivery-verified-with-echo-sink.md) | 用回显接收端让「告警送得出去」持续被验证 | **已实机验证**(2026-08-28):告警从 Alertmanager 出去、POST 到外部终点、payload 完整可见 |
| [022](022-ci-chart-validation.md) | CI:push/PR 前跑 `helm template` | 已采纳 |
| [083](083-internal-package-registry.md) | 内部包共享:MinIO 上的静态 PEP 503 索引,不引入包服务器 | **已实机验证**(2026-08-29):pod 里不加任何参数 `pip install platform-helpers` 装上并能用 |
| [082](082-dbt-lineage-ingestion.md) | 让 dbt 的产物真的被消费:血缘接进 OpenMetadata | **已实现并实机验证通过**(2026-08-29 02:23,cloud-full) |
| [084](084-analyst-sql-workbench.md) | 分析师的浏览器 SQL 入口:复用 Superset SQL Lab | **已实机验证**(2026-08-30):SQL Lab 上 `current_user` 是登录者本人、没 grant 的表被 `PERMISSION_DENIED` 拒、列级脱敏生效 |
| [085](085-inference-payload-logging.md) | 推理请求留痕:KServe 自带 logger → Kafka → Iceberg | **已实现,未部署验证** |
| [086](086-approval-belongs-to-oa.md) | 审批流程外包给 OA,平台只留策略和执行 | **协议已改对(`APPROVAL_BACKEND=oa`),没有真实 OA 可对接,未验证** |
| [087](087-feature-drift-monitoring.md) | 特征漂移:训练时把基线存进 MLflow tag,一个 jobs/ 作业算 PSI | **已实现,2026-09-01 实机验证通过** |
| [088](088-minio-console-sso.md) | MinIO 控制台接 Keycloak;策略只给 platform-team(读桶=绕过 OPA) | **已部署验证 —— 但 SSO 登录这一半没成立,见文末「实机验证的结果」** |
| [089](089-secret-management-openbao.md) | 用户凭据托管用 OpenBao(不是 Vault:许可证);开发档自动解封的代价写清楚 | **第一阶段完成,2026-09-01 实机验证通过** |
| [090](090-customization-layers.md) | 组件二开分三层,优先用上游扩展点;企微告警作为样例 | **已实现(企业微信告警作为第一个样例)** |
| [091](091-superset-alerting-chain.md) | Superset 告警是一整条链(开关+broker+beat+worker+通知实现),不是一个开关 | **已实现并实机验证(通知已真实投递,收到的 payload 逐字段核对过)** |
| [092](092-airflow-remote-logging.md) | Airflow 任务日志写 MinIO —— task pod 跑完即删,不配远程日志就等于没有排障能力 | **已实现并实机验证(重跑一次失败任务,日志从 MinIO 读得到)** |
| [093](093-opensearch-http-tls.md) | OpenSearch 的 HTTP 层不启用 TLS —— demo 证书的 SAN 里没有集群内服务名,主机名校验过不去导致血缘整个坏掉 | **已实现并实机验证(血缘推送恢复,产物核对过)** |
| [061](061-vendor-grafana-charts.md) | helm 有个改不掉的 120s 超时:alloy/loki 的 chart vendor 进仓库 | 已实现并在 cloud-full 验证 |
| [060](060-conditional-rendering-and-tls-issuer.md) | 条件生成(`render-if`)+ TLS 签发方 / 告警通知渠道按环境切换 | 已实现;ACME 那一档**还没有在真实环境验证过**(缺真实域名和 ICP 备案) |

## 产品与门户

| # | 标题 | 状态 |
|---|---|---|
| [047](047-platform-portal.md) | 平台门户:统一入口页面,不是新的认证系统 | 已实现 |
