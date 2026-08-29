# 能力表的旧版(存档,2026-08-29 前)

> **这不是权威文档。** 现在的权威入口是
> [`capability-matrix.md`](capability-matrix.md)。
>
> 这份留着只为一件事:**不丢证据**。旧版每一格里塞了大量叙述性内容
> ——某天验证时发现了什么 bug、哪一格曾经标着 ✅ 其实是假的、某个取舍
> 当时是怎么想的。这些内容有价值,但它们不该长在"我们做到哪了"这张
> 表里:表要能一眼扫完,叙述会把表撑到没人看得下去(旧版 247 行,
> 每格一段话)。
>
> 新表只留状态 / 验证级别 / 最后验证时间 / 证据链接;叙述归 ADR、
> `docs/journal/`、和新表底部那节「这张表被证伪过几次」。下面是原文,
> 一个字没改。

---

# 各角色今天真的能做什么

这份文件是"我们做到哪了"的**唯一权威入口**,取代 `docs/architecture.md`
里 Phase 0-4 表格的这个作用(那张表回答的是"部署了哪些组件",保留作为
历史记录,但它不是衡量进度的标尺——见
[ADR-057](../decisions/057-architecture-review-2026-08-19.md))。

衡量标准只有一条:**某个岗位的人,能不能不靠平台维护者在旁边帮忙,
独立完成一件他日常真实要做的工作。** 组件部署了、API 通了、demo 跑过,
都不算数。

- ✅ **可用**——真实验证过,该角色能自己用
- 🟡 **部分**——能跑通但有明显缺口(要人工帮忙 / 只验证过 demo 路径 / 体验不成立)
- ❌ **缺失**——今天做不到

> 状态基准:2026-08-21(部分行注明了各自的验证日期),以 cloud-full 环境为准。
> "没部署"指组件定义写好了但不在 `environments/cloud-full/config.yaml`
> 的 `enabled_components` 列表里,`apps-root` 不会同步它——**注意这不是
> 资源不够**(cloud-full 是 16 vCPU / 64 GiB)。2026-08-20 起"哪些组件
> 启用"已经改成这份声明式列表(ADR-057 第三批,`docs/project/roadmap.md` 1.1
> 已完成),不再靠人工在目录之间 `git mv`。

---

## 一句话总览

**2026-08-19 更新**:P1 排在最前的三个动作(部署 OpenMetadata / JupyterHub
+ MLflow / Spark Operator + SeaTunnel + Spark History Server)已经完成
并逐个用真实 curl+cookie-jar 端到端登录验证过(不是只看 Pod Running)。
过程中顺带发现并修了 8 个真实 bug(SSO 端口/scope/service 名写错、Keycloak
realm 从来没有 groups claim mapper、镜像拉取超时、内存 OOM 等)——这些
组件长期 park、从没真的走过一次登录,配置错误一直没暴露,详见
`docs/journal/2026-08.md`。**大数据开发和算法工程师从"结构性缺失"变成
"能开工",数据分析师的"找数据"卡点解除。**

Kafka 已部署并真实验证(2026-08-19,建 topic/发消息/收消息全链路跑通,
不只是 Pod Running)。**Argo Workflows 的 SSO+RBAC 也已修好并真实验证**
(登录 → 列 workflow → 建一个真实 workflow → 能查到,curl+cookie-jar
测试通过,详见下面"运维/平台工程"表格);管理角色仍未开始。

| 角色 | 能独立完成日常工作? | 主要卡点 |
|---|---|---|
| 运维 | ✅ 基本可以 | 六条黄金链路都有探针 + Runbook 已成型;告警送达路径验证过(终点先接集群内 echo sink,换真实渠道只改一个 Secret 的 url——按 zhenghe 要求,真实渠道等上生产再接) |
| 数据分析师 | ✅ 基本可以 | OpenMetadata 已部署验证,"找数据"卡点解除 |
| 大数据开发 | 🟢 可用 | **批和流两条链路都已真实端到端跑通**:批(接数据→批处理→作业历史→血缘,2026-08-22)+ 流(Producer→Kafka→Flink→Iceberg,2026-08-22 夜,ADR-062) |
| 算法工程师 | ✅ 基本可以 | 主链路 + 多步骤 DAG(特征物化→训练→模型门禁)+ **审批/回滚闭环**都已端到端跑通(ADR-080)。灰度做不了是**环境决定的、不是没做**:KServe 用 `deploymentMode: Standard`(不装 Knative),`canaryTrafficPercent` 会被接受但完全不生效——实测确认后改成显式拒绝,不留一个假开关 |
| 管理 | 🟡 有第一版 | 平台总览看板已上(E 线第一版),看板里写明了它够不着什么。按月聚合的前提(Prometheus 15d 保留 + 持久卷)2026-08-28 才补上,在那之前指标每次开机清零,数据压根不存在——所以月度那部分要等数据自己攒够 |

---

## 运维 / 平台工程

**日常工作:**保证平台活着,出问题能定位,变更能安全上线和回滚。

| 环节 | 状态 | 说明 |
|---|---|---|
| 声明式部署 / 变更 | ✅ | ArgoCD GitOps,改 values → push → 自动同步(ADR-005,推倒重建验证过 ADR-039) |
| 从空集群拉起 | ✅ | `scripts/bootstrap-all.sh` 一键,14 步幂等,真实跑通过 |
| 指标 | ✅ | kube-prometheus-stack,Grafana 接 Keycloak SSO(本次会话端到端验证过登录) |
| 日志 | ✅ | Loki + Alloy 采集全部 pod stdout,和指标同一个 Grafana 界面(ADR-020) |
| 告警产生 | ✅ | Alertmanager 已开,chart 自带规则生效,抓到过真实问题(ADR-034) |
| 告警送达 | ✅ | **2026-08-28 端到端验证通过**(ADR-081):告警从 Alertmanager 出去、POST 到外部终点、payload 完整可见。在这之前**这条路径一次都没有跑过**——规则、路由、receiver 全都在,而「能不能送出去」是未知的。现在终点是集群内的 alert-echo-sink,**换真实渠道只要改一个 Secret 的 url**,机制那一半一直有真实告警流量在验证。`GET /alerts` 能直接看到企业微信那边会收到什么 payload |
| 数据质量断言 | ✅ | OpenMetadata 自带 Data Quality(ADR-065/070),三条断言 + 一条新鲜度断言实机跑通;用一条故意失败的探针验证过「绿灯能变红」。新鲜度实测 `insertedRows=1640`。局限:覆盖两张表,断言失败时还没有告警通道 |
| 备份 / 恢复 | ✅ | Postgres 每日备份,恢复演练验证过(ADR-033) |
| 资源治理(组件级上限) | ✅ | ResourceQuota / LimitRange / PriorityClass(ADR-041)——防「单个组件失控拖死机器」 |
| 资源治理(按组分配 + 空闲互借) | ✅ | Kueue,按 `platform/iam/` 已有的组建队列,同一 cohort 内可借(ADR-064)。**实测有数字**:platform-team 标称 2 CPU,提一个要 3 CPU 的作业被 admit、队列状态里 `borrowed: 1`;提要 6 CPU 的被挡住 `insufficient quota`。队列标签由 SDK 按提交者的组自动打,不是用户手填 |
| 查询审计留痕 | ✅ | Trino → Kafka → Flink → Iceberg 全链路实机跑通(ADR-066)。`audit.query_events` 一次查询一行、`audit.query_table_access` 一次表访问一行,实测 3 条查询 → 5 行事件 / 4 行表访问(`select 1` 正确地不出现在表访问里)。审计表有 OPA 保护:服务账号读不到,只有 platform-team 和采元数据的 openmetadata_service 例外。**还差**「审计流断了」的告警 |
| Schema 契约 | ✅ | Karapace(ADR-068)实机验证:加带默认值的可选字段放行、`double`→`string` 被 409 拦住(`compatibility_mode=BACKWARD`)。**Flink 作业还没接**,现在 schema 仍写死在 SQL 里 |
| 成本可见性 | ✅ | OpenCost(ADR-069)+ Grafana 看板 7 个 panel(`platform/grafana-cost-dashboard/`)。实机出数:29 个命名空间合计 ¥0.808/小时,整机标价 ¥4.04——**被工作负载占用的只有五分之一**,这个差额本身就是信号。「按组的成本」那个 panel 的 PromQL 在真集群上验过:给一个 request 1 CPU、打 `algorithm-team` 队列标签的作业算出 ¥0.168,正好是 1 核 × 单价,数字对得上 |
| 网络隔离 | ✅ | NetworkPolicy 覆盖核心命名空间(ADR-035) |
| 破坏性操作防护 | ✅ | `scripts/confirm-destructive-kubectl.sh`(历史上真误删过 namespace,见 `docs/operations/incidents.md`) |
| 计费资源门禁 | ✅ | `cloud-full-preflight.sh` + 空闲自动关机看门狗(经济模式) |
| 排障知识 | ✅ | 2026-08-22 改造成 Runbook:顶部 59 条**症状索引**(按人实际观察到的现象组织),下面按层次分 9 节,每条统一成"症状 → 定位 → 处置"。条目 40 → 66(新增 26 条是从 journal/ADR 搬进来的真实故障),行数 864 → 1632。内容保全做过机械核对:旧版 292 个技术片段(报错原文/命令/版本号/路径)逐个查过,没有丢 |
| 统一服务目录 / 黄金链路告警 | 🟡 | **黄金链路那半做完并实机验证**(ADR-079):**六条**链路各一个探针 CronJob(query / streaming / catalog / authz / model / inference),做的是真实的小事而不是探进程——比如 authz 那条验证的是**拒绝**(拿不到 `PERMISSION_DENIED` 就算失败),不是能不能连上;配套 `GoldenPathBroken` 告警 + 「黄金链路」看板。实测三条全通(query 0.2s/10 行、catalog 6 个字段、streaming 5 分钟前),而且**停机 22 小时后 streaming 从「1327 分钟前」自己恢复到「5 分钟前」**。2026-08-28 实测六条全通(catalog 那条在开机瞬间失败过一次,是 OpenMetadata 还没起来,下一轮自己恢复)。门户首页顶部也直接显示 N/M。**「统一服务目录」2026-08-29 已做**:`platform/service-catalog.yaml`(34 个服务 + 37 项支撑资源,覆盖三个环境全部 71 个启用组件),生成 [`docs/reference/service-catalog.md`](../reference/service-catalog.md)。**关键不是这份清单本身,是 `scripts/check-service-catalog.py`**:新组件没登记归属、owner 写了不存在的组、依赖指向不存在的服务、生成的 md 和源码漂移,四种情况 CI 都会红——没有这层约束,手写清单三个月后就会变成一份过期文档,比没有更糟 |
| 容量 / 成本看板 | 🟡 | **两半都有面板了**:成本 `platform/grafana-cost-dashboard/`(7 panel,PromQL 在真集群验过);容量 `platform/grafana-capacity-dashboard/`(6 panel:各组配额用量 vs 标称、排队作业区分「等资源」和「永远排不上」、节点剩余可分配)。**容量那份还没部署验证过**,所以整格还是 🟡 |
| Argo Workflows 授权 | ✅ | **2026-08-19 已修复并验证**:之前 CrashLoopBackOff 2 天多没人发现(issuer/issuerAlias),修好登录后又发现登录成功但调 API 403——`server.sso.rbac.enabled` 不会自动建授权资源,读官方源码(`gatekeeper.go`)确认要手动建 ServiceAccount(挂 rbac-rule 注解)+ 长期 token Secret + Role/RoleBinding,四个都补上了。真实 curl+cookie-jar 验证:登录→列 workflow→建一个真实 workflow→能查到→删除清理 |
| idle-shutdown-watchdog 开机自愈 | ✅ | **2026-08-19 修复**:停机几天后重新开机,看门狗第一次检查会用几天前的旧时间戳误判"已空闲超过阈值"立刻自动关机——机器刚开机 2-3 分钟就被自己关掉,来不及做任何事。已加开机时重置状态的机制(这个脚本本身按既定政策不进 git,细节记在 `docs/journal/2026-08.md`） |

**结论:**这个角色是目前完成度最高的,这次又补上两个真实发现的问题
(Argo Workflows RBAC、看门狗开机自杀)。剩下的真实缺口是"告警送不到人"
和"排障知识没有 Runbook 化"——两个都不需要新组件,是打磨问题。

---

## 数据分析师

**日常工作:**知道有哪些数据 → 拿到权限 → 查出来 → 做成看板给别人看。

| 环节 | 状态 | 说明 |
|---|---|---|
| 登录 | ✅ | Keycloak SSO,一次登录全平台通用 |
| 统一入口 | ✅ | platform-portal,列出所有工具和入口(ADR-047,本次会话验证过登录) |
| **找数据(有哪些表)** | ✅ | **2026-08-19 已部署并验证**——真实 OAuth2 登录测试通过(拿到真实 access_token/id_token,issuer 匹配)。目录浏览本身可用,采集任务(pipelineServiceClientConfig,把 Trino/Spark 等实际表结构同步进来)还没配置,现在能查到的资产取决于手动录入了多少 |
| 申请表权限 | 🟡 | permission-request-app 已部署,分级审批链跑通(ADR-044/045)。OpenMetadata 已部署,ADR-046 的"浏览目录勾选"理论上能工作,但还没有实际验证这两个系统之间的联动(OpenMetadata 目录数据 → 申请表单可选项) |
| 权限真正生效 | ✅ | Trino 接 OPA 细粒度访问控制,已正式上线并验证(未授权拒绝/授权放行,ADR-051) |
| 权限到期回收 | ✅ | 自动回收(ADR-050) |
| 查询 | ✅ | Trino Web UI 走 Keycloak SSO(本次会话修好并端到端验证);Iceberg 表读写正常 |
| 建表 | ✅ | table-registration-app,建表 + 回写负责人/安全等级(ADR-043) |
| SQL 数据转换 | 🟡 | dbt 最小骨架能在 Trino/Iceberg 上跑(ADR-053),**已经接进 Airflow**(`dbt_demo` DAG,`dbt build` + 把 `manifest.json`/`catalog.json` 上传到 `s3://lakehouse/dbt-artifacts/`)。*2026-08-21 修正:这一行之前写的"没接 Airflow 编排"不准确。* **没用 Cosmos 是刻意的设计取舍不是缺口**(Cosmos 要在 DAG 解析阶段跑 dbt,得改 scheduler/dag-processor 的 Python 运行时,理由见 DAG 文件顶部注释)。**②(dbt 摄入)2026-08-29 已完成并实机验证**(ADR-082):OpenMetadata 上挂了 `trino_dbt` 采集管道,血缘接口查得到 `orders -> stg_orders -> daily_order_totals` 两条真实的边。过程中修掉两个真问题:MinIO 的 NetworkPolicy 漏了 `openmetadata`(和 kserve-demo 同一个盲区——运行时建的消费关系静态检查器扫不到);以及采集顺序有硬依赖,元数据采集必须先跑,否则 dbt 采集会报 `Success 100%` 而血缘一条都没建(表还没进目录,边无处可挂)。**真正还缺的一件**:`schedule=None`,dbt 只能手动触发,不是常驻定时任务,所以血缘会停在最后一次手动跑的状态 |
| 看板 / BI | ✅ | Superset 接 Keycloak SSO;连 Trino 走 impersonation,**数据权限按登录用户判**(ADR-074)。**2026-08-29 修掉一个产品层权限漏洞并实机验证**:此前 `AUTH_USER_REGISTRATION_ROLE="Admin"` 且 OAuth scope 里没有 `groups`,任何能登录的人在 Superset 里都是管理员。现按 Keycloak 组映射,**在跑着的实例上走了一遍真实登录逻辑**:`analyst001`(data-analysts)拿到 `['Alpha','Gamma','sql_lab']`,不在任何组的人拿到 `['Gamma']`(修之前是 Admin) |
| 中文界面 | ✅ | Superset **2026-08-28 实机验证**:language pack 加载成功、4054 条(`Dashboards→看板`、`Save→保存`),`.mo` 22 个 + `.json` 23 个都在镜像里(ADR-077)。过程走了两步才对:只配 `BABEL_DEFAULT_LOCALE` 界面还是英文(`.mo` 没编译),编译完 React 主界面仍是英文(前端读的是另一套 `messages.json`)。**其它组件(Airflow/Grafana/OpenMetadata)还是英文** |

**结论:**OpenMetadata 部署验证完成后,这个角色的核心链路已经打通。
剩余缺口是打磨性质的(采集任务配置、目录联动验证、dbt 编排),不再是
结构性缺失。

---

## 大数据开发

**日常工作:**把外部数据接进来 → 加工 → 定时调度 → 出问题能查。

| 环节 | 状态 | 说明 |
|---|---|---|
| 批量数据接入 | ✅ | **2026-08-21 第一次真实端到端验证通过**:触发 `seatunnel_device_events` DAG,SeaTunnel 日志确认 `Committed snapshot ... addedRecords=20`,20 条数据真的写进了 `seatunnel.demo.device_events` 这张 Iceberg 表,DAG 三个任务(提交/等待/推血缘)全绿。**此前这一行写的 ✅ 是假的**——依据只是"已部署 + ArgoCD Synced/Healthy",而真实数据路径其实一直不通(`seatunnel` 命名空间不在 Hive Metastore 的 NetworkPolicy 白名单里),因为那个 DAG 长期暂停、没人触发过所以一直没暴露。教训见 `docs/project/roadmap.md` 2.8 |
| 流式数据接入 | ✅ | **2026-08-22 夜真实端到端验证通过**:Producer CronJob 灌 device event 进 `device-events` topic(schema 和 SeaTunnel 那条批量链路一致,不是另发明一套),Flink 消费后写进 Iceberg,Trino 查得到。这是这个平台**第一条端到端验证过的流式管道**——此前 Kafka 只验证过"能生产/消费一条消息",从没接进真实管道。剩余:SeaTunnel/Kafka Connect 这类现成接入组件还没接进流式链路(现在的 Producer 是自建 demo 应用) |
| 湖仓存储 | ✅ | Iceberg on MinIO + Hive Metastore(ADR-002) |
| 批处理引擎 | ✅ | **2026-08-21 真实跑通**:`spark-iceberg-demo` 作业 COMPLETED——读到 Trino 建的 `iceberg.demo.orders` 10 行、聚合后写出 `orders_by_region_spark` 新表、再读回确认真的落盘(`SPARK_ICEBERG_DEMO_OK`)。修好之前它坏了 10 天没人发现:`scripts/13` 引用的 `spark-rbac.yaml` 在 commit a7f2833 就被删了、`serviceAccount: spark` 指向不存在的 SA、运行时拉 Maven jar 在云主机上必卡死(改成构建期打进 `apps/spark-iceberg-image/`) |
| 流处理引擎 | ✅ | **2026-08-22 夜真实端到端验证通过**:Flink Kubernetes Operator 1.15.0 + PyFlink,Kafka → Iceberg。`scripts/31-run-flink-streaming-demo.sh` 输出 `FLINK_STREAMING_DEMO_OK: 明细表行数从 0 增加到 100,聚合表 25 行`——判定依据是**用 Trino 独立查 Iceberg 表的实际行数**,不是看作业状态。从"代码写完"到"真的跑通"一共修了 9 个 bug,全部只有真部署才会暴露,清单见 ADR-062 |
| 调度 | ✅ | Airflow 已部署,DAG 单一源码 + CI 防漂移 |
| 作业可观测 | ✅ | **2026-08-21 真实跑通**:History Server 的 `/api/v1/applications` 列出了刚跑完的作业(`name=spark-iceberg-demo, completed=True`),不再是空数组。修好之前**从部署那天起就是空的**——作业压根没开 `eventLog`,`s3a://spark-logs/` 一直没有任何内容;补上后又发现不能指 bucket 根路径(S3Guard 报 `path must be absolute`),改用 `events/` 子目录,并把这个前缀的创建做成声明式(MinIO chart 的 `customCommands`,验证过 hook 真的会重建它) |
| 血缘 | ✅ | **2026-08-22 真实端到端验证通过**:此前一直卡在"bot token 要人工去 OpenMetadata UI 建"这一步,`scripts/27-configure-openmetadata-bot.sh` 解除了这个卡点——OpenMetadata 安装时已经自动生成一个 unlimited 有效期的 ingestion-bot JWT(存在 Postgres `user_entity` 表,Fernet 加密),脚本直接从数据库读出解密,不用登录 UI、不用等人工建 bot,幂等地建成 `table-registration-app-openmetadata` / `permission-request-app-openmetadata` 两个 Secret。验证链路:配好 token 后触发 `seatunnel_device_events` DAG,`push_lineage` 任务 success,直接查 `GET /api/v1/lineage/pipeline/name/airflow-platform.seatunnel_device_events` 确认真实存在 `pipeline -> trino.iceberg.demo.device_events` 这条血缘边(不是只看任务状态)。Spark 血缘(ADR-014)仍仅设计,未验证 |
| 数据质量 / 契约 | ✅ | **两半都做完并实机验过**:质量用 OpenMetadata 自带的 Data Quality(ADR-065/070,4 条断言 + 新鲜度,都有真实结果);契约用 Karapace Schema Registry(ADR-068),真实 producer 改字段类型被 409 拦在发送侧。**还差**:断言失败没有告警出口(见平台运维那节的「告警送达」) |
| 作业模板 / CI-CD | ✅ | 模板 4 个(`examples/`)+ `platform-submit --new` 脚手架 + CI 校验模板一直可用。**CI-CD 那半 2026-08-29 补上**:`jobs/<名字>/job.yaml` 里写一行 `schedule`,push,ArgoCD 同步成 Argo CronWorkflow——单步作业不用为了定时去写 Airflow DAG。实机验证:`daily-order-summary` 查 Trino、建表、幂等写回 Iceberg,用另一个独立作业查出表里真有 4 行。过程中发现 `platform-job-credentials` 这个 Secret **从来就没存在过**,导致「从 notebook/CI 提交一个查数作业」这条路径一直是断的(`optional: true` 让 pod 照常起来,一调 query() 才抛 MissingCredential),已补进 `scripts/00` |

**结论(2026-08-21,已全部修通):****"接数据→批处理→查看作业历史"这条
完整链路第一次真实端到端跑通**,三步都有可复核的证据(不是"组件 Running")。
但值得记住的是**开跑之前这三步里有三处标着 ✅ 其实都是假的**,全靠真的
跑一遍才暴露:

- **接数据 ✅ 真通了**:20 条数据真实落进 Iceberg。但修之前它是不通的
  ——NetworkPolicy 白名单漏了 `seatunnel` 命名空间,加上 DAG 里
  `Variable.get(..., default_var=)` 用了 Airflow 3.x 已经不存在的参数名,
  每跑必 TypeError。两个 bug 都是"存在很久但没人触发过所以没暴露"。
- **批处理 ✅ 现在真通了**:但 demo 脚本从 2026-08-12 起就是坏的(引用了
  当天被删除的 spark-rbac.yaml),整整 10 天没人发现,因为没人跑过。
- **作业可观测 ✅ 现在真通了**:此前 History Server 从部署那天起列表就是
  空的——作业压根没配 `eventLog`,没有任何东西写进 `s3a://spark-logs/`。
  "能登录进去"被当成了"这个能力可用"。

**这段经历本身就是这份文档存在的理由**:开跑前这三步全都标着 ✅,真去跑
才发现三处**全都**站不住,共同点都是"部署了 + ArgoCD 绿了"被当成了"能用"。
凡是没有真实跑过一次的能力,状态最多只能标 🟡。

---

## 算法工程师

**日常工作:**探索数据 → 造特征 → 训练 → 记录实验 → 注册模型 → 上线推理 → 看效果。

| 环节 | 状态 | 说明 |
|---|---|---|
| **交互式开发(Notebook)** | ✅ | JupyterHub 已部署并真实登录验证通过(2026-08-19)。**"打开就自动连好 Trino"这个差距 2026-08-19 晚已由 ADR-058 补上**:singleuser 用的是平台统一镜像(`apps/platform-image/`),自带 `platform_sdk`,notebook 里 `from platform_sdk import query` 直接可用,`query()`/`mlflow_setup()` 都在真实 notebook pod 里验证过成功——不用自己装 client、不用自己拼连接串(`docs/usage-guide.md` 已同步更新)。*2026-08-21 修正:这一行之前还写着"仍然没有这个体验",是过期信息。* |
| 特征工程 | ✅ | Feast 全链路真实重新验证(2026-08-19):Iceberg → Spark 离线读取 → feast apply → materialize → Redis 在线存储 → Feature Server 在线查询,Alice/Bob 的 region/product/amount 都查出正确值。过程中修了两个真实 bug(见下面"当前最高性价比"那段),不只是组件都在 |
| 训练执行 | ✅ | **Argo Workflows 编排训练已实现并真实验证**(2026-08-19,`apps/argo-workflows-training-image/`)——不是照抄参考项目 ysb/algo 的 notebook+papermill 模式,评估后改用纯 Python 脚本 + 专门镜像,复用 `scripts/train_demo_model.py`。真实提交 Workflow 跑通,Model Registry 查询确认 version READY。目前只有训练一步,没有 Spark 特征工程步骤 |
| **实验跟踪 / 模型注册** | ✅ | MLflow 已部署并真实登录验证通过(2026-08-19,含按组授权)。部署时修了两个真实 bug:内存限制太小导致启动 36 秒内 OOMKill、oauth2-proxy 配置的后端 Service 名字写错(chart 生成的真实名字是 `mlflow-mlflow` 不是 `mlflow`) 。**2026-08-27 补记**:黄金链路探针(ADR-079)第一次跑就发现 **MLflow 注册表是空的**——08-22 推倒重建后训练脚本再没跑过,这一格的 ✅ 依据是 08-19 那次验证,而验证的产物早没了。重跑 `scripts/09` 恢复。**能力表记的是「某天验证过」,探针答的是「现在还成不成立」** |
| 模型部署 | ✅ | KServe 已部署(CRD + ServingRuntime),V2 协议推理验证过(ADR-027) |
| 推理可观测 | ✅ | **2026-08-29 做完并实机验证**:PodMonitor 抓 predictor 的 MLServer 指标,`platform-inference` 看板 6 个 panel(QPS、错误率、端到端 P50/P95/P99、模型层平均耗时、按状态码、以及一个「有没有在抓」的自检面板)。每条查询都在真集群上确认返回了数(实测 P95 9.7ms、模型层平均 4.4ms)。**特征漂移监控仍然没有**——那需要先把推理输入留痕,是另一件事 |
| 灰度 / A-B | ❌ | KServe 原生 canary 没配置(设计判断见 architecture.md) |
| 模型审批 / 回滚 | ✅ | ADR-080,**完整链路实机验证**:训练→注册→审批→部署→真实推理返回预测→回滚守卫。还验过一条关键守卫:**存在更新的未批准版本(v2)时,部署仍然只用已批准的 v1**。上线单位从「MinIO 里最新的目录」换成「注册表里被批准过的版本」——在这之前连版本概念都没有 |
| 模型灰度 | ❌ | **实测确认这套架构不支持**(ADR-080):KServe 是 RawDeployment 模式(刻意不装 Knative),而 `canaryTrafficPercent` 依赖 Knative 流量切分——字段会被收下但**完全不生效**,新版本直接拿 100%。`scripts/11` 现在**明确拒绝**这个参数:留一个不生效的参数比没有更糟。两条可行路径写在 ADR 里,都没做 |

**结论:**JupyterHub + MLflow 已部署验证,这个角色从"连第一步都做不到"
变成"能开工"。**"训练 → MLflow 记录"这一段已经真实验证过**
(2026-08-19,`scripts/09-train-demo-model.sh`:真实训练一个 sklearn
模型、accuracy 0.855、注册进 Model Registry、API 查询确认
status=READY)。**Feast 特征这一段也重新验证过**(2026-08-19,全链路
真实跑通,见上面"特征工程"一行)。**"Argo Workflows 编排训练"这段
之前的空白也已经补上**(2026-08-19 晚些时候,见上面"训练执行"一行,
设计取舍详见 `docs/project/current-work.md`)。

**2026-08-20:整条链路已经串起来真实跑通,不再是"每段分别验证过"。**
两件事补齐了最后的空白:①`platform_sdk.run_workflow_template()`——
notebook 里一行代码触发已部署的 WorkflowTemplate,不用 `kubectl create`,
云端真实触发验证过;②新增 `train-from-feast-features` 这个
WorkflowTemplate,用 `FeatureStore.get_historical_features()` 取
point-in-time 正确的历史特征来训练(不是合成数据),云端跑通、MLflow
Model Registry 查询确认 `demo-region-classifier` v1 `status=READY`。
过程中挖出并修好 4 个真实 bug(feast/pyspark 版本冲突、Feast 初始化
eagerly import redis、Spark 依赖直连 Maven Central 卡死改成构建期打包
jar、Hive Metastore NetworkPolicy 漏了 argo-workflows 命名空间)。

**2026-08-21:多步骤 DAG 也补上并验证了**——新增 `ml-pipeline` 这个
WorkflowTemplate,用 Argo 的 `dag`+`dependencies` 把
特征物化 → 训练 → 模型门禁 三步串起来,云端跑通(三步时间戳确认真的按
依赖顺序执行,MLflow 独立确认 v2 是训练那一步产出的、门禁在它之后通过)。
门禁**刻意不卡 accuracy 阈值**:demo 数据 10 行、测试集 1 条,这个规模下
accuracy 只可能是 0 或 1,拿它当阈值是自欺欺人;改成校验"模型能不能被
加载、能不能做出形状正确的预测"——也就是"到底能不能部署",这才是 KServe
上线的真实前置条件。有生产规模数据集之后再补指标阈值。

**还真的没做的**:模型灰度/审批/回滚。

---

## 管理 / 数据治理

**日常工作:**平台健不健康、谁在用、钱花在哪、数据资产覆盖多少、权限有没有风险。

| 环节 | 状态 | 说明 |
|---|---|---|
| 权限分级审批 | ✅ | 按安全等级路由的多级审批链(ADR-044) |
| 审批通知 / 超时升级 | ✅ | 可插拔审批后端 + 超时升级(ADR-045),企微推送需真实凭据激活 |
| 权限交接 | ✅ | 人员变动时的权限转移(ADR-045) |
| 权限到期回收 | ✅ | 自动回收(ADR-050) |
| 审计留痕 | 🟡 | Keycloak 事件进 Loki(ADR-024);审批系统自带审计页。但**没有汇总视图** |
| 组织架构同步 | ✅ | iam-sync 从 HR 表同步组织/角色进 Keycloak(ADR-028/031),职级数据目前是虚拟占位 |
| 数据资产盘点 | ✅ | **2026-08-23 真实端到端验证通过**:`scripts/29` 配好 Trino DatabaseService + 每 6 小时的采集任务,`scripts/30` 输出 `OPENMETADATA_TRINO_INGESTION_OK`。判据是**直接查 OpenMetadata API 确认 `trino.iceberg.demo.orders` 在目录里、六个字段和 Trino 真实表结构一致**——这张表从没人手动登记过,是采集自动发现的。实测目录里已有 100+ 张表(system/tpcds/tpch/iceberg 各 catalog)。过程中修了 4 个只有真跑才暴露的前置,清单见 ADR |
| 平台管理组全权限 | ✅ | **2026-08-26 之前一直是摆设**(ADR-078):Trino 没配 group provider,传给 OPA 的 groups 永远是空的,`is_platform_admin` 从来没触发过——而 `opa test` 全过,因为测试的 input 是手写的、带着 groups。配上 file group provider(数据来自 `memberships.csv`)后实机验证:platform-team 的人能查只有该组能读的审计表,查带行级过滤的表拿到**全部 6 行**(普通用户只有 2 行) |
| 敏感字段列级脱敏 | ✅ | ADR-063,2026-08-23 **在真集群上用真实 SQL 验过**(`scripts/36-verify-trino-masking.sh`,可重复跑):1 级 grant 查到的是 `138****5678` / `al***@example.com` / `***MASKED***`;**2 级 grant 上 phone/email 恢复明文而 id_card 仍然打码**——验的是分级真的在起作用,不是「一律打码」也能过的那种测法。Trino 侧实测每条查询会调 18 次 `columnMask` + 2 次 `rowFilters` |
| 敏感字段行级过滤 | ✅ | ADR-063,2026-08-26 **在真集群上用真实 SQL 验过**(和列级同一个脚本):`demo.regional_sales` 里 3 个部门各 2 行,analyst001(employees.csv 里是「数据分析组」)只查到 2 行、且正好是华东/华南那两行。判定不只看「行数变少」——还要求看到的部门集合正好等于 {数据分析组},否则「谁都看不到」(部门查不到时的 `1 = 0` 分支误触发)也会蒙混过关 |
| **管理驾驶舱** | 🟡 | **第一版做了**(`platform/grafana-overview-dashboard/`):一页看完「五条黄金链路通几条 / 当前每小时花多少 / 整机标价 / 利用率 / 各组占比 / 各组配额用了多少」。**看板里明写了它不回答什么**——按月汇总和预算对比、数据质量通过率、数据资产规模,这三样才是管理真正要的,而数据在 OpenMetadata 里不在 Prometheus。**未部署验证** |
| 成本视图 | 🟡 | 数据和 Grafana 面板都有了(ADR-069),但那是**给平台运维看的**。管理视角要的是「这个月各部门花了多少、趋势如何、值不值」——需要按月聚合 + 和预算对比,现在没有 |

**结论:**治理的"流程"部分意外地完整(审批/回收/交接这套 OA 能力是真实
做出来的),但治理的"视图"部分完全空白——管理者今天没有任何一个页面可以
回答"平台现在什么情况"。

---

## 这份表怎么用

1. **决定下一步做什么时,先看这份表,不要看组件清单。**优先级排序的
   依据是"解锁哪个角色的哪条能力",不是"还有哪个开源组件没接"。
2. **2026-08-19 已完成**:OpenMetadata / JupyterHub+MLflow / Spark
   Operator+SeaTunnel+Spark History Server 全部部署并逐个真实登录验证
   过。当前最高性价比的下一批动作:
   - **ADR-057 第三批(环境抽象的组件选择层)**——这次仍然是手工
     `git mv` + 逐个排查 Keycloak client/Secret/scope 缺口做完的,过程中
     暴露的 bug(client-exists-但-Secret-缺失、groups scope 从来没配过、
     Service 名字写错)本可以在"改配置就重新拉起"这套机制下更早被结构性
     测试捕获,现在只能靠"当天有没有人手动测登录"这种运气发现。
   - **Kafka 已部署验证**(2026-08-19,真实建 topic/生产/消费一条消息),
     大数据开发角色补齐最后一块,但还没接进真实数据管道。
   - **算法链路"训练→MLflow"+"Feast 特征"这两段都已验证**(2026-08-19,
     真实训练模型注册进 Model Registry;Feast 全链路 Iceberg→Spark→
     Redis→在线查询真实跑通,过程中修了 DAG 默认暂停、本地构建镜像在
     远程节点拉不到这两个真实 bug)。**"Argo Workflows 编排训练"这段
     空白也已经补上**(2026-08-19 晚些时候,新写了 WorkflowTemplate,
     不是照抄 ysb/algo 的 notebook+papermill,过程中修了 4 个真实 bug:
     mlflow-skinny 缺 pandas、MinIO NetworkPolicy 漏了 argo-workflows
     命名空间、WorkflowTemplate 没指定 serviceAccountName、chart 建了
     RoleBinding 但没建对应 ServiceAccount)。剩下真正的空白是"notebook
     里触发训练"(需要真实浏览器交互验证)和多步骤 DAG(现在只有训练
     一步,没有特征工程/评估)。
3. **更新纪律:**任何一次让某个角色多/少一项能力的改动,必须同步改这份
   表。这份表过时了,项目就又回到"不知道自己做到哪了"的状态。
