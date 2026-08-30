# 各角色今天真的能做什么

这份文件是"我们做到哪了"的**唯一权威入口**。衡量标准只有一条:**某个岗位
的人,能不能不靠平台维护者在旁边帮忙,独立完成一件他日常真实要做的工作。**
组件部署了、ArgoCD 绿了、demo 跑过,都不算数。

判断某件事该不该做、该排多前,依据是它解锁哪个角色的哪条能力(ADR-057),
不是"还有哪个开源组件没接"。

## 怎么读这张表

**状态**:✅ 可用 · 🟡 有缺口 · ❌ 做不到

**验证级别**——这一栏比状态更重要,它回答的是"凭什么说它可用":

| 级别 | 含义 |
|---|---|
| **生产验证** | 在生产环境、真实数据、真实用户下跑过 |
| **集成验证** | 在 cloud-full 真集群上端到端跑通,判据是业务结果(查到几行、拿到什么角色),不是组件状态 |
| **demo** | 只在 demo 数据或单条路径上验过,规模/边界情况未知 |
| **未验证** | 配置写好了,但没有人真的跑过一次 |
| **计划中** | 没做 |

**今天全平台没有任何一格是「生产验证」。** 这不是疏漏,是事实——这套东西
还没上过生产。在 [`production-readiness-gaps.md`](production-readiness-gaps.md)
列的那些项目补齐之前,任何一格都不许标成生产验证,也不许对外说"生产可用"。

**最后验证**:那一格的证据是哪天产生的。**日期旧不代表坏了,但也不代表还
好着** —— 2026-08-27 就撞到过一次:MLflow 注册表那一格挂着 08-19 的 ✅,而
08-22 推倒重建之后模型早没了,是黄金链路探针(ADR-079)发现的。**能力表
记的是「某天验证过」,探针答的是「现在还成不成立」,两者不能互相替代。**

**更新纪律**:任何一次让某个角色多一项或少一项能力的改动,必须同步改这张
表。表过时了,项目就回到"不知道自己做到哪了"的状态。

> 旧版(2026-08-29 前)每一格里带着大段叙述,已整体移到
> [`capability-matrix-archive-2026-08.md`](capability-matrix-archive-2026-08.md),
> 一个字没删。

---

## 总览

| 角色 | 能独立开工? | 最大的一个卡点 |
|---|---|---|
| 运维 / 平台工程 | ✅ | 告警终点还是集群内 echo sink,没接真实渠道(按既定安排,等上测试/生产再接) |
| 数据分析师 | ✅ | SQL 工作台刚换成 Superset SQL Lab(ADR-084),**impersonation 没在 SQL Lab 上单独验过** |
| 大数据开发 | ✅ | 批和流两条链路都端到端通了;作业发布还是"一个脚本 + 一份 yaml",没有多文件项目和晋级路径 |
| 算法工程师 | ✅ | 灰度做不了是架构决定的(RawDeployment 无 Knative),不是没做 |
| 管理 / 数据治理 | 🟡 | 流程(审批/回收/交接)完整,视图空白——按月聚合的数据 2026-08-28 才开始攒 |

---

## 运维 / 平台工程

保证平台活着,出问题能定位,变更能安全上线和回滚。

| 环节 | 状态 | 验证级别 | 最后验证 | 证据 |
|---|---|---|---|---|
| 声明式部署 / 变更 | ✅ | 集成验证 | 2026-08-22 | [ADR-005](../decisions/005-argocd-gitops.md) · 推倒重建 [ADR-039](../decisions/039-teardown-rebuild-test.md) |
| 从空集群拉起 | ✅ | 集成验证 | 2026-08-29 | `scripts/bootstrap-all.sh`,失败会非零退出并写 `logs/bootstrap-report.json` |
| 指标 / 日志 | ✅ | 集成验证 | 2026-08-19 | kube-prometheus-stack + Loki/Alloy([ADR-020](../decisions/020-centralized-logging-loki-alloy.md)) |
| 告警产生 | ✅ | 集成验证 | 2026-08-19 | [ADR-034](../decisions/034-alertmanager.md),抓到过真实问题 |
| 告警送达 | ✅ | 集成验证 | 2026-08-28 | [ADR-081](../decisions/081-alert-delivery-verified-with-echo-sink.md)。终点是集群内 echo sink,**换真实渠道只改一个 Secret 的 url** |
| 数据质量断言 | ✅ | demo | 2026-08-23 | [ADR-065](../decisions/065-data-quality-on-openmetadata.md)/[070](../decisions/070-data-freshness-slo.md)。只覆盖两张表,失败时没有告警通道 |
| 备份 / 恢复 | ✅ | 集成验证 | 2026-08-18 | [ADR-033](../decisions/033-postgres-backup.md),做过恢复演练 |
| 资源治理(组件级上限) | ✅ | 集成验证 | 2026-08-20 | [ADR-041](../decisions/041-queue-resource-management.md) |
| 资源治理(按组分配 + 借用) | ✅ | 集成验证 | 2026-08-23 | [ADR-064](../decisions/064-role-based-resource-quota.md)。实测借用生效、超额被挡 |
| 查询审计留痕 | ✅ | 集成验证 | 2026-08-24 | [ADR-066](../decisions/066-trino-query-audit.md)。**还差**"审计流断了"的告警 |
| Schema 契约 | ✅ | 集成验证 | 2026-08-25 | [ADR-068](../decisions/068-schema-registry.md)。**Flink 作业还没接**,schema 仍写死在 SQL 里 |
| 成本可见性 | ✅ | 集成验证 | 2026-08-26 | [ADR-069](../decisions/069-cost-attribution.md) + `platform/grafana-cost-dashboard/` |
| 网络隔离 | ✅ | 集成验证 | 2026-08-18 | [ADR-035](../decisions/035-network-policy.md) |
| 破坏性操作防护 | ✅ | 集成验证 | 2026-08-15 | `scripts/confirm-destructive-kubectl.sh` · [事故记录](../operations/incidents.md) |
| 计费资源门禁 | ✅ | 集成验证 | 2026-08-19 | `scripts/cloud-full-preflight.sh` + 空闲自动关机看门狗 |
| 排障知识 | ✅ | — | 2026-08-22 | [Runbook](../operations/troubleshooting.md):59 条症状索引,66 个条目 |
| 黄金链路探针 | ✅ | 集成验证 | 2026-08-29 | [ADR-079](../decisions/079-golden-path-probes.md)。六条各一个 CronJob;当天最近一轮全部 Completed |
| 统一服务目录 | ✅ | 集成验证 | 2026-08-29 | [service-catalog.md](../reference/service-catalog.md)。关键是 `scripts/check-service-catalog.py` —— 漏登记/owner 不存在/依赖悬空/生成物漂移,CI 都会红 |
| 容量看板 | 🟡 | 未验证 | — | `platform/grafana-capacity-dashboard/` 6 个 panel 写好了,**没部署验证过** |
| Argo Workflows 授权 | ✅ | 集成验证 | 2026-08-19 | curl+cookie-jar:登录 → 列 → 建 → 查到 → 删 |

## 数据分析师

知道有哪些数据 → 拿到权限 → 查出来 → 做成看板给别人看。

| 环节 | 状态 | 验证级别 | 最后验证 | 证据 |
|---|---|---|---|---|
| 登录 / 统一入口 | ✅ | 集成验证 | 2026-08-30 | Keycloak SSO + platform-portal([ADR-047](../decisions/047-platform-portal.md))。**2026-08-30 实机验证按角色显示**:`data-analysts` 身份看不到 ArgoCD/Keycloak、看得到 SQL 工作台;`platform-team` 全部可见;三个自建应用都读得到 groups claim(此前 permission-request-app 读不到,导致组权限审批对所有人 403) |
| 找数据(有哪些表) | ✅ | 集成验证 | 2026-08-23 | 采集自动发现,目录里 100+ 张表,字段和 Trino 真实表结构一致 |
| 申请表权限 | 🟡 | demo | 2026-08-30 | [ADR-044](../decisions/044-tiered-approval-workflow.md)/[045](../decisions/045-approval-backend-notifications-escalation.md)。2026-08-30 实机验证:审批链算得对、拒绝接口不返回 500、时区换算和 groups 提示都在。**目录 → 申请表单的联动仍没验过**;组权限申请的批准按钮要真人登录才点得到 |
| 权限真正生效 | ✅ | 集成验证 | 2026-08-26 | Trino 接 OPA([ADR-051](../decisions/051-trino-opa-access-control.md)) |
| 权限到期回收 / 续期 | ✅ | 集成验证 | 2026-08-30 | [ADR-050](../decisions/050-grant-expiry-reclamation.md)。**2026-08-30 实机验证到期提醒和续期**:门户上快到期的排最前、标黄、显示剩余天数、带续期入口;点续期建出的是 `pending` 的新申请(**不是直接延期**),审批链真的有两级,重复提交被 409 挡住 |
| **SQL 工作台** | ✅ | 集成验证 | 2026-08-30 | [ADR-084](../decisions/084-analyst-sql-workbench.md)。从「Trino Web UI」(**那里根本没有 SQL 编辑器**)改成 Superset SQL Lab。**2026-08-30 实机验证四条**:这条连接上 `current_user` 是登录者本人(不是 superset_service)、有 grant 的表查得到、没 grant 的被 `PERMISSION_DENIED` 拒、列级脱敏生效(`138****5678`)。由 `scripts/46-verify-p15.sh sqllab` 可重复跑 |
| 建表 | ✅ | 集成验证 | 2026-08-30 | [ADR-043](../decisions/043-table-registration-tool.md)。2026-08-29 补完:字段说明、分区、质量断言(建真的 OpenMetadata testCase)、提交前预览、按等级的审批分流、负责人不能冒充。**2026-08-30 实机验证**:提交一张带字段说明和分区的表,`SHOW CREATE TABLE` 里 COMMENT 和 partitioning 都在;非平台组提交 2 级表被挡住并落了说明去哪的记录;预览返回的 DDL 和实际执行的一致 |
| SQL 数据转换(dbt) | 🟡 | 集成验证 | 2026-08-29 | [ADR-082](../decisions/082-dbt-lineage-ingestion.md),血缘查得到 `orders → stg_orders → daily_order_totals`。**缺**:`schedule=None`,只能手动触发 |
| 看板 / BI | ✅ | 集成验证 | 2026-08-30 | 组映射实测:`data-analysts` → `Alpha/Gamma/sql_lab`,未分组 → `Gamma`。**修之前所有人都是 Admin**(`AUTH_USER_REGISTRATION_ROLE="Admin"` + scope 里没有 groups) |
| 中文界面 | 🟡 | 集成验证 | 2026-08-28 | Superset 4054 条译文([ADR-077](../decisions/077-superset-chinese-ui.md))。**Airflow/Grafana/OpenMetadata 仍是英文** |

## 大数据开发

把外部数据接进来 → 加工 → 定时调度 → 出问题能查。

| 环节 | 状态 | 验证级别 | 最后验证 | 证据 |
|---|---|---|---|---|
| 批量数据接入 | ✅ | 集成验证 | 2026-08-21 | SeaTunnel:`addedRecords=20`,数据真的进了 Iceberg 表 |
| 流式数据接入 | ✅ | 集成验证 | 2026-08-22 | Producer → Kafka → Flink → Iceberg([ADR-062](../decisions/062-flink-streaming-pipeline.md)) |
| 湖仓存储 | ✅ | 集成验证 | 2026-08-18 | Iceberg on MinIO + Hive Metastore([ADR-002](../decisions/002-iceberg-lakehouse.md)) |
| 批处理引擎 | ✅ | 集成验证 | 2026-08-29 | Spark 4.1.3 + Iceberg 1.11:读 10 行 → 聚合 → 写新表 → 读回确认(`SPARK_ICEBERG_DEMO_OK`) |
| 流处理引擎 | ✅ | 集成验证 | 2026-08-22 | 判据是**用 Trino 独立查表的行数**(0 → 100),不是作业状态 |
| 调度 | ✅ | 集成验证 | 2026-08-21 | Airflow,DAG 单一源码 + CI 防漂移 |
| 作业可观测 | ✅ | 集成验证 | 2026-08-21 | Spark History Server `/api/v1/applications` 列得出刚跑完的作业 |
| 血缘 | ✅ | 集成验证 | 2026-08-22 | 直接查 lineage API 确认边存在。**Spark 血缘仍没落地**,而且 2026-08-30 核实发现 [ADR-014](../decisions/014-spark-lineage-official-agent.md) 选的那个 agent 只有 2024 年的 `1.0-beta`(Java 11 + OpenLineage 1.7),**不认 Spark 4** —— artifact 要重选 |
| 数据质量 / 契约 | ✅ | demo | 2026-08-25 | 质量 [ADR-065](../decisions/065-data-quality-on-openmetadata.md)/[070](../decisions/070-data-freshness-slo.md) + 契约 [ADR-068](../decisions/068-schema-registry.md)。**断言失败没有告警出口** |
| 作业发布(定时) | ✅ | 集成验证 | 2026-08-30 | `jobs/<名字>/job.yaml` 写一行 `schedule` → Argo CronWorkflow;支持多文件、依赖声明(和镜像清单对账,CI 拦)、参数化补数、按环境晋级([`jobs/README.md`](../../jobs/README.md))。**2026-08-30 实机验证四条**:多文件 `import jobkit` 成功;补数 `-p run_date=2026-08-01` 后**表里真的多出那一天的 4 行**;**定时那一跳也验了** —— 克隆一份改成两分钟后触发,CronWorkflow 自己起了 workflow 并跑成功(真实那条定在 UTC 01:30,云主机那个点基本关着,所以一直没被触发过) |
| 流作业发布 | ✅ | 集成验证 | 2026-08-29 | `streams/<名字>/stream.yaml` → FlinkDeployment;门户「流作业」一栏显示状态 |
| 内部包共享 | ✅ | 集成验证 | 2026-08-29 | [ADR-083](../decisions/083-internal-package-registry.md)。pod 里**不加任何参数** `pip install platform-helpers` 装得上并能用 |

## 算法工程师

探索数据 → 造特征 → 训练 → 记录实验 → 注册模型 → 上线推理 → 看效果。

| 环节 | 状态 | 验证级别 | 最后验证 | 证据 |
|---|---|---|---|---|
| 交互式开发(Notebook) | ✅ | 集成验证 | 2026-08-29 | JupyterHub + 平台镜像自带 `platform_sdk`([ADR-058](../decisions/058-lightweight-developer-experience.md))。**身份不再是共享服务账号**:notebook 里查到的 `current_user` 是登录者本人,列级脱敏对他生效 |
| 特征工程 | ✅ | 集成验证 | 2026-08-20 | Feast 全链路:Iceberg → Spark → materialize → Redis → 在线查询 |
| 训练执行 | ✅ | 集成验证 | 2026-08-21 | `ml-pipeline` WorkflowTemplate,三步按依赖顺序执行 |
| 实验跟踪 / 模型注册 | ✅ | 集成验证 | 2026-08-27 | MLflow。**这一格被探针证伪过一次**,见下面那节 |
| 模型部署 | ✅ | demo | 2026-08-20 | KServe V2 协议推理([ADR-027](../decisions/027-kserve-model-serving.md)) |
| 推理可观测 | ✅ | 集成验证 | 2026-08-29 | `platform-inference` 看板 6 panel,真集群出数(P95 9.7ms)。**特征漂移监控没有** |
| 模型审批 / 回滚 | ✅ | 集成验证 | 2026-08-28 | [ADR-080](../decisions/080-model-approval-and-rollback.md)。关键守卫验过:存在更新的未批准 v2 时,部署仍只用已批准的 v1 |
| 模型灰度 / A-B | ❌ | 计划中 | — | **实测确认这套架构不支持**:RawDeployment 模式下 `canaryTrafficPercent` 会被收下但完全不生效,`scripts/11` 现在显式拒绝它 —— 留一个不生效的参数比没有更糟 |

## 管理 / 数据治理

平台健不健康、谁在用、钱花在哪、数据资产覆盖多少、权限有没有风险。

| 环节 | 状态 | 验证级别 | 最后验证 | 证据 |
|---|---|---|---|---|
| 权限分级审批 | ✅ | demo | 2026-08-18 | [ADR-044](../decisions/044-tiered-approval-workflow.md) |
| 审批通知 / 超时升级 | ✅ | demo | 2026-08-18 | [ADR-045](../decisions/045-approval-backend-notifications-escalation.md),企微推送需真实凭据激活 |
| 审批落地不再假成功 | ✅ | 集成验证 | 2026-08-29 | 写 git 失败时状态是 `approved_pending_apply` 而不是 `approved`,配 `/internal/retry-pending-applies` |
| 权限交接 / 到期回收 | ✅ | 集成验证 | 2026-08-18 | [ADR-045](../decisions/045-approval-backend-notifications-escalation.md) / [ADR-050](../decisions/050-grant-expiry-reclamation.md) |
| 审计留痕 | 🟡 | 集成验证 | 2026-08-24 | Keycloak 事件进 Loki([ADR-024](../decisions/024-platform-audit-logging.md))+ 查询审计表。**没有汇总视图** |
| 组织架构同步 | ✅ | demo | 2026-08-18 | iam-sync([ADR-028](../decisions/028-iam-org-model.md)/[031](../decisions/031-iam-auto-sync-cronjob.md))。**职级数据是虚拟占位**,真实 HR 对接要 zhenghe 提供对接方 |
| 数据资产盘点 | ✅ | 集成验证 | 2026-08-23 | 采集自动发现,不是手动登记 |
| 平台管理组全权限 | ✅ | 集成验证 | 2026-08-26 | [ADR-078](../decisions/078-trino-group-provider.md)。**在这之前一直是摆设**:Trino 没配 group provider,传给 OPA 的 groups 永远是空的,而 `opa test` 全过(测试 input 是手写的、带着 groups) |
| 敏感字段列级脱敏 | ✅ | 集成验证 | 2026-08-23 | [ADR-063](../decisions/063-trino-column-row-level-security.md)。验的是**分级真的在起作用**:2 级 grant 下 phone/email 恢复明文而 id_card 仍打码 |
| 敏感字段行级过滤 | ✅ | 集成验证 | 2026-08-26 | 同上。判据不只是"行数变少",还要求看到的部门集合正好等于预期 |
| 管理驾驶舱 | 🟡 | 未验证 | — | `platform/grafana-overview-dashboard/`。看板里写明了它够不着什么 |
| 成本视图(管理视角) | ❌ | 计划中 | — | 现有成本面板是给运维看的。按月聚合的前提(Prometheus 15d 保留 + 持久卷)2026-08-28 才补上,数据要自己攒 |

---

## 这张表(以及验它的工具)被证伪过几次

留这一节不是自责,是因为**它证明这张表需要存在**,也解释了为什么"验证级别"
那一栏比"状态"重要。

**2026-08-21,一次跑通三处假 ✅。** 开跑前"接数据 → 批处理 → 作业历史"三步
全标着 ✅,真去跑,三处**全都**站不住:

- 接数据:Hive Metastore 的 NetworkPolicy 白名单漏了 `seatunnel` 命名空间;
  外加 DAG 里用了 Airflow 3.x 已经不存在的 `default_var` 参数名,每跑必挂。
- 批处理:demo 脚本从 2026-08-12 起就是坏的(引用了当天被删的 `spark-rbac.yaml`),
  **10 天没人发现**——因为没人跑过。
- 作业历史:History Server 从部署那天起列表就是空的,作业压根没开 `eventLog`。

共同点是同一个:**"部署了 + ArgoCD 绿了 + 能登录进去"被当成了"这个能力可用"。**

**2026-08-26,`opa test` 全过但功能是摆设。** Trino 没配 group provider,传给
OPA 的 groups 永远是空的,`is_platform_admin` 从来没触发过。测试全绿,是因为
测试的 input 是手写的、带着 groups —— **单元测试测不出"真实输入长什么样"。**

**2026-08-27,验证过的东西过期了。** MLflow 注册表那一格挂着 08-19 的 ✅,而
08-22 推倒重建之后模型早就没了。是黄金链路探针发现的,不是这张表。

**2026-08-29,自己写的功能验收不看渲染结果。** 门户"我的作业"一栏永远空白:
后端给的是 `phase`/`started`,模板读的是 `status`/`at`。Jinja 对未定义变量
渲染成空字符串,**不报错**,30 个测试全绿。

**2026-08-30,验收脚本自己不可信。** 开机跑 P1.5 的回归验收,脚本本身有
三个 bug,每个都是"检查跑了、结论不可信":

- `kubectl logs workflow/<name>` 取不到日志(kubectl 不认这个 kind),而
  `2>/dev/null || true` 把报错吞成空串 —— 两条检查都在拿**空字符串**判断:
  "有没有 ModuleNotFoundError"必然通过(假阳性),"有没有 2026-08-01"必然
  失败(假阴性)。两条同时错、方向相反。
- `echo ... | while read` 起子 shell,里面的 ✅/❌ 记账全丢 —— **失败会被
  静默吞掉**,一个漏报失败的验证脚本比没有它更危险。
- 断言 HTTP 302,而 `urlopen` 默认跟随重定向,拿到的永远是 200。

**同一天还差点把一个好功能报成坏的**:验 SQL Lab 的 impersonation 时用
`flask_login.login_user` 造身份,`current_user` 返回 `superset_service`。
实际是 Superset 读的不是那个地方(要用它自己的 `override_user`)——
**测试装置写错,结论方向完全相反。**

**这几次合起来给出的判据,现在是硬性的:**

1. 判据必须是业务结果(查到几行、拿到什么角色、表里有没有数),不是组件状态。
2. 单元测试的 input 是自己写的,证明不了真实输入长什么样 —— 至少要有一次
   端到端。
3. 状态有保质期。长期能力靠探针答"现在还成不成立",不靠这张表。
4. 没有真的跑过一次的,验证级别最多写"未验证",状态最多 🟡。
5. **验证工具本身也要被怀疑。** 一条检查"通过"之前,先问它的输入是不是
   真的拿到了东西 —— 空字符串、子 shell 里丢掉的记账、被自动跟随的重定向,
   都会让检查安静地给出一个和事实无关的结论。
