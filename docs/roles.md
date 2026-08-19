# 各角色今天真的能做什么

这份文件是"我们做到哪了"的**唯一权威入口**,取代 `docs/architecture.md`
里 Phase 0-4 表格的这个作用(那张表回答的是"部署了哪些组件",保留作为
历史记录,但它不是衡量进度的标尺——见
[ADR-057](decisions/057-architecture-review-2026-08-19.md))。

衡量标准只有一条:**某个岗位的人,能不能不靠平台维护者在旁边帮忙,
独立完成一件他日常真实要做的工作。** 组件部署了、API 通了、demo 跑过,
都不算数。

- ✅ **可用**——真实验证过,该角色能自己用
- 🟡 **部分**——能跑通但有明显缺口(要人工帮忙 / 只验证过 demo 路径 / 体验不成立)
- ❌ **缺失**——今天做不到

> 状态基准:2026-08-19,以 cloud-full 环境为准。
> "没部署"指组件定义写好了但在 `environments/cloud-full/pending-definitions/`,
> `apps-root` 不会同步它——**注意这不是资源不够**(cloud-full 是
> 16 vCPU / 64 GiB),是"哪些组件启用"至今仍靠人工在目录之间 `git mv`,
> 见 ADR-057"环境抽象只做了一半"。

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

Kafka 仍未部署(不阻塞任何角色的最小可用路径,见 ADR-056 对引入顺序的
判断);Argo Workflows 的 SSO 登录本身已修好并验证,但登录后调用 API
仍是 403(RBAC 授权层的独立问题,见下面"运维/平台工程"表格);管理角色
仍未开始。

| 角色 | 能独立完成日常工作? | 主要卡点 |
|---|---|---|
| 运维 | ✅ 基本可以 | 缺统一控制面/Runbook;告警没有外部通知渠道 |
| 数据分析师 | ✅ 基本可以 | OpenMetadata 已部署验证,"找数据"卡点解除 |
| 大数据开发 | 🟡 能开工 | 引擎已部署验证,但 Kafka 仍未接入、血缘仍是部分 |
| 算法工程师 | 🟡 能开工 | JupyterHub+MLflow 已部署验证,但"一键连好 Trino"的体验仍是缺口 |
| 管理 | ❌ 不行 | 驾驶舱从未开始 |

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
| 告警送达 | ❌ | **没有配任何外部通知渠道**——现在只能"打开界面查",不会推送到人。邮件/企微/Slack 的配置模板已经预留在 `platform/apps/kube-prometheus-stack.yaml` 里注释着,需要真实凭据才能激活 |
| 备份 / 恢复 | ✅ | Postgres 每日备份,恢复演练验证过(ADR-033) |
| 资源治理 | ✅ | ResourceQuota / LimitRange / PriorityClass(ADR-041) |
| 网络隔离 | ✅ | NetworkPolicy 覆盖核心命名空间(ADR-035) |
| 破坏性操作防护 | ✅ | `scripts/confirm-destructive-kubectl.sh`(历史上真误删过 namespace,见 `docs/operations/incidents.md`) |
| 计费资源门禁 | ✅ | `cloud-full-preflight.sh` + 空闲自动关机看门狗(经济模式) |
| 排障知识 | 🟡 | `docs/operations/troubleshooting.md` 742 行、内容扎实,但是**一篇长文不是 Runbook**——没有按"症状 → 定位 → 处置"组织,出事时不好检索 |
| 统一服务目录 / 黄金链路告警 | ❌ | D 线产品主线,未开始 |
| 容量 / 成本看板 | ❌ | 未开始 |
| Argo Workflows 授权 | ❌ | **2026-08-19 新发现**:SSO 登录本身已修好(之前 CrashLoopBackOff 2 天多没人发现),但登录后调用 API 返回 403"not allowed"——`server.sso.rbac` 需要的 K8s RBAC 绑定没配。已单独立项跟踪,不阻塞其它角色 |
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
| SQL 数据转换 | 🟡 | dbt 最小骨架能在 Trino/Iceberg 上跑(ADR-053),但**没接 Airflow 编排(Cosmos)、没接 OpenMetadata 摄入**,等于"能手动跑 dbt",不是"分析师的生产工具" |
| 看板 / BI | ✅ | Superset 接 Keycloak SSO(本次会话修好 `api_base_url` 并端到端验证),连 Trino 用服务账号(ADR-021) |
| 中文界面 | ❌ | Superset 未汉化(zhenghe 2026-08-16 提出,不急) |

**结论:**OpenMetadata 部署验证完成后,这个角色的核心链路已经打通。
剩余缺口是打磨性质的(采集任务配置、目录联动验证、dbt 编排),不再是
结构性缺失。

---

## 大数据开发

**日常工作:**把外部数据接进来 → 加工 → 定时调度 → 出问题能查。

| 环节 | 状态 | 说明 |
|---|---|---|
| 批量数据接入 | ✅ | SeaTunnel 已部署(2026-08-19),ArgoCD Synced/Healthy |
| 流式数据接入 | ❌ | **Kafka 没部署**(Strimzi KRaft,单独验证过健康,从没接进端到端管道) |
| 湖仓存储 | ✅ | Iceberg on MinIO + Hive Metastore(ADR-002) |
| 批处理引擎 | ✅ | Spark Operator 已部署(2026-08-19),ArgoCD Synced/Healthy(ADR-036 验证过真实读写 Iceberg,这次是重新拉起,没有重跑那次真实作业验证) |
| 流处理引擎 | ❌ | Flink 只有角色设计(ADR-056),没有实现 |
| 调度 | ✅ | Airflow 已部署,DAG 单一源码 + CI 防漂移 |
| 作业可观测 | ✅ | Spark History Server 已部署并真实登录验证通过(2026-08-19)。过程中修了两个真实 bug:官方镜像不带 S3A 连接器 jar(补 initContainer)、oauth2-proxy 缺 scope 配置 |
| 血缘 | ❌ | OpenMetadata 没部署;SeaTunnel 血缘(ADR-052)只验证过 API 机制;Spark 血缘(ADR-014)仅设计 |
| 数据质量 / 契约 | ❌ | 未开始(B 线) |
| 作业模板 / CI-CD | ❌ | 未开始(A 线) |

**结论:**Spark Operator/SeaTunnel/Spark History Server 已部署,调度器
现在有真正的引擎可以调度了。**Kafka 仍未部署**(ADR-056 判断它现在零
真实消费者,不阻塞最小可用路径,可以稍后补)。这个角色从"基本什么都
做不了"变成"能开工",但还没有真实跑通一条完整的"接数据→批处理→查看
作业历史"链路——组件都在,联调验证还没做。

---

## 算法工程师

**日常工作:**探索数据 → 造特征 → 训练 → 记录实验 → 注册模型 → 上线推理 → 看效果。

| 环节 | 状态 | 说明 |
|---|---|---|
| **交互式开发(Notebook)** | 🟡 | JupyterHub 已部署并真实登录验证通过(2026-08-19,SSO 登录成功跳转 `/hub/spawn`)。但仍然没有"打开就自动连好 Trino、自动带自己权限"的体验——要自己装 client、自己填连接串(`docs/usage-guide.md` 已如实记录这个差距,这次部署没有改变它) |
| 特征工程 | 🟡 | Feast 已部署,离线(Spark 读 Iceberg)/ 在线(Redis)打通过(ADR-042)。但**离线特征依赖 Spark Operator,而它没部署** |
| 训练执行 | 🟡 | Argo Workflows 已部署;但 Spark 训练依赖没部署的 Spark Operator |
| **实验跟踪 / 模型注册** | ✅ | MLflow 已部署并真实登录验证通过(2026-08-19,含按组授权)。部署时修了两个真实 bug:内存限制太小导致启动 36 秒内 OOMKill、oauth2-proxy 配置的后端 Service 名字写错(chart 生成的真实名字是 `mlflow-mlflow` 不是 `mlflow`) |
| 模型部署 | ✅ | KServe 已部署(CRD + ServingRuntime),V2 协议推理验证过(ADR-027) |
| 推理可观测 | 🟡 | Grafana 能看到基础指标,没有面向模型的监控(延迟/漂移/调用量) |
| 灰度 / A-B | ❌ | KServe 原生 canary 没配置(设计判断见 architecture.md) |
| 模型审批 / 回滚 | ❌ | 未开始(C 线) |

**结论:**链路呈现"**两头有、中间空**"的形态——最末端的 KServe 部署好了,
最前端什么都没有。算法工程师今天连第一步(打开一个 notebook)都做不到。
**结论:**JupyterHub + MLflow 已部署验证,这个角色从"连第一步都做不到"
变成"能开工"。特征工程(Feast)和训练执行(Argo Workflows)依赖的
Spark Operator 现在也已部署,理论上应该打通了,但这条链路(notebook →
特征 → 训练 → MLflow 记录)还没有重新跑一次真实的端到端验证。

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
| 数据资产盘点 | 🟡 | OpenMetadata 已部署验证(2026-08-19),但采集任务(把 Trino/Spark 等实际元数据同步进来)还没配置,现在能查到的资产取决于手动录入 |
| 敏感字段行列级策略 | ❌ | OPA 原生支持,没配置 |
| **管理驾驶舱** | ❌ | **从未开始**(E 线) |
| 成本视图 | ❌ | 未开始 |

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
   - **Argo Workflows RBAC**(已单独立项,见 task 记录)——登录能成功,
     调用 API 还是 403。
   - **跑一次真实的算法链路端到端验证**(notebook → Feast 特征 →
     Argo Workflows 训练 → MLflow 记录)——组件都部署验证过了,但这条
     完整链路本身还没有重新连起来跑一次。
   - **Kafka 部署**——大数据开发角色补齐最后一块。
3. **更新纪律:**任何一次让某个角色多/少一项能力的改动,必须同步改这份
   表。这份表过时了,项目就又回到"不知道自己做到哪了"的状态。
