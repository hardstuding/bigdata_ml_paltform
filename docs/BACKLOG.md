# Backlog

新想法/顺手发现的问题,默认进这里,不自动打断
[`docs/CURRENT_WORK.md`](CURRENT_WORK.md) 里的当前主线。这份文件只做索引
和优先级排序,不重复描述已经在别处写清楚的内容——每一项都指向权威来源
(ADR / architecture.md),不在这里复制一遍。

**排序依据(2026-08-19 起,见 [ADR-057](decisions/057-architecture-review-2026-08-19.md)):
按"阻塞哪个角色的哪条能力"排,不按"还有哪个组件没接"排。**
判断某件事该排多前,先看 [`docs/roles.md`](roles.md) 的角色能力表。

> 2026-08-19 重排说明:此前的 P1.5/P1.6/P1.7 不是优先级,是不同日期追加
> 的批次(带小数的层级本身就是"一直在追加、没有重排过"的证据)。这次
> 按角色影响重新排了序,已完成的条目收敛到底部"已完成(存档)",避免
> 一份 400 行的清单里一半是划掉的东西。

---

## P0(会阻断当前主线的,才有资格排这里)

当前没有。如果出现真实的数据风险/持续计费异常/安全问题,加在这里,
并在 `docs/CURRENT_WORK.md` 里注明"CURRENT 被 P0 阻断,原因是……"。

---

## P1:解锁角色能力(投入产出比最高,先做这一批)

依据 [`docs/roles.md`](roles.md)。**1.2/1.3/1.4 已于 2026-08-19 完成**
(OpenMetadata / JupyterHub+MLflow / Spark Operator+SeaTunnel+Spark
History Server 全部部署并逐个真实登录验证过,不是只看 Pod Running)。
过程中发现并修复了 8 个真实 bug——这些组件长期 park、从没真的走过一次
登录,配置错误一直没暴露:SSO 端口/scope/upstream Service 名字写错
(mlflow-oauth2-proxy 配的 Service 名根本不存在)、Argo Workflows
issuer 不匹配导致 CrashLoopBackOff 两天多没人发现、Keycloak realm 从
建立起就没配过 groups claim mapper(意味着 Grafana/JupyterHub 的按组
授权可能从来没真的生效过,这次一并修了)、OpenMetadata 镜像走自定义
域名代理导致 ImagePullBackOff、MLflow 内存限制太小 36 秒内 OOMKill、
Spark History Server 缺 S3A 连接器 jar。详见 `docs/journal/2026-08.md`
和对应 commit。

### 1.1 环境抽象补上"组件选择"层 —— 已完成(2026-08-20)

这次拉起 P1.2/1.3/1.4 全靠人工 `git mv` + 逐个手动排查 Keycloak
client/Secret/scope 缺口完成,过程中暴露的好几个 bug(client 已存在但
Secret 缺失、groups scope 从来没配过)本可以在"改配置就重新拉起"这套
机制的约束下被更早、更结构性地测试捕获,现在只能靠"当天有没有人手动
测登录"这种运气发现。cloud-full 是 16 vCPU / 64 GiB,不是资源不够,是
"哪些组件在哪个环境启用"当时(2026-08-19)靠人工在 `apps/definitions/`
和 `environments/cloud-full/pending-definitions/` 之间 `git mv`——这个
机制是 2026-08-08 为 6GB 的 colima 本机设计的,搬到云上没有重新评估过。

**已完成(2026-08-20,ADR-057 第三批)**:"启用哪些组件"已经变成
`environments/<env>/config.yaml` 里的 `enabled_components` 声明式列表,
和已有的 `scripts/render-environment-config.py` 机制衔接(没有再造一个
新脚本),`pending-definitions/` 这个靠目录位置表达启用状态的机制已退役、
目录已删除。

同一层还缺"规格分档"(副本数/resources/持久化按环境取值),可以一起做,
优先级低于组件选择。

### 1.5 Argo Workflows RBAC —— 已完成(2026-08-19)

SSO 登录 CrashLoopBackOff(issuer/issuerAlias 配置问题)和登录后调用
workflows API 403(`server.sso.rbac` 需要的 ServiceAccount + 长期
token Secret + Role/RoleBinding)都已修好,四个资源加进了
`templates/apps-definitions/argo-workflows.yaml` 的 `extraObjects`。
真实 curl+cookie-jar 验证过:登录→查询 workflows API 200→建一个真实
Workflow→能查到→删除清理。详见 `docs/CURRENT_WORK.md` 归档记录。

### 1.6 Kafka 部署 —— 已完成(2026-08-19)

部署 + 真实建 topic/生产/消费一条消息验证通过,大数据开发角色最后一块
拼图补上。**还没接进真实数据管道**(没有真实 Producer/Consumer 应用,
目前零真实消费者,这条不算数据管道本身完成,只是组件可用性)。

### 1.7 算法链路端到端重新验证

JupyterHub/MLflow/Spark Operator/Feast/Argo Workflows 都已部署验证。
**2026-08-19 晚些时候:"Argo Workflows 编排训练"本身已经真正实现并
端到端验证**(不只是组件部署,是真的写了 WorkflowTemplate、提交跑通、
Model Registry 查询确认 READY——见 `docs/CURRENT_WORK.md`、
`apps/argo-workflows-training-image/`)。但"notebook 里触发训练"
(在 JupyterHub 里点一下就能拉起这个 Workflow,不是 `kubectl create`)
和"notebook → Feast 特征 → Argo Workflows 训练 → MLflow 记录"这条完整
链路串成一次真实调用,仍然是真实空白——现在每一段是分别验证的,不是
连起来的一条链路,也没有多步骤 DAG(特征工程→训练→评估,现在
WorkflowTemplate 只有训练一步)。

---

## P2:交付方式的可靠性(角色能开工之后,立刻做这一批)

ADR-057 认定的结构性债务。不做的话,上面 P1 拉起来的东西会以同样脆弱的
方式继续堆叠。

### 2.1 引入镜像构建流程,停止用运行时 `pip install`(最大的一条)

全仓库只有 1 个 Dockerfile,CI 没有任何镜像构建,而 **8 个地方在容器
启动时现装 Python 依赖**。仅 2026-08-16 一晚就因此产生四次真实故障
(Superset 被 SIGKILL 循环、platform-portal 卡 pip 导致流量一直走旧 pod、
换镜像源导致 `ModuleNotFoundError`、同一 manifest 不同时间部署得到不同
运行时)。离线/内网环境根本装不起来,这对"能原样上生产"是硬伤。

范围按收益排序:3 个自建 Flask 应用(同时受"启动慢/不可复现/ConfigMap
塞源码"三个问题影响)→ iam-sync → Superset 这类官方 chart 的
bootstrapScript(改动面最大,最后处理)。

配套:依赖锁定(lock 文件)、GitHub Actions 构建推 registry、manifest
引用带 digest 的镜像。**明确不做**:不引入 Kaniko/Tekton 这类集群内
构建体系,对单人维护的项目过重。

做完这一条,`sync-app-configmaps.py` 这类"源码塞 ConfigMap"的脚本可以
退役,顺带消解下面 2.2。

### 2.2 "生成式单一源码"脚本的增殖

现在有 3 个脚本在实现同一个模式(`sync-app-configmaps.py` /
`sync-airflow-dags-configmap.py` / `render-environment-config.py`)。
各自都对、都接了 CI,但这个模式一直增殖本身就是"缺一个构建步骤"的症状。
2.1 做完后重新评估哪些可以退役,不要单独动手。

### 2.3 Trino livenessProbe 的人工补丁要么固化要么消除

`scripts/07-fix-trino-liveness-probe.sh` 必须在**每次** Trino pod
template 变更后重跑,否则回退到 chart 的坏默认值(本次会话就重跑了 3 次)。
这不是 GitOps,是"GitOps 加一个没人会记得的手工步骤"。可选解法:ArgoCD
的 postSync hook、或者给上游 chart 提 issue/PR。

### 2.4 三个自建 Flask 工具的测试补完 —— 已完成(2026-08-20)

主体 2026-08-16 完成(60 个测试接进 CI)。**剩余缺口 2026-08-20 补完**:
`apply_to_git`/`apply_grant_to_git`/`reclaim_expired`/`transfer` 里真正
执行 git clone/commit/push 的分支,现在用 `local_git_repo` fixture(起
一个本地裸仓库当 `REPO_URL`,不连真实 GitHub)覆盖;外部 OA webhook 的
真实 POST(成功标记 `pending_external`、失败退化回本地 `pending` 两条
分支)用 `monkeypatch.setattr(perm.requests, "post", ...)` 覆盖,不发
真实网络请求。三个应用现在合计 106 个测试(platform-portal 17 +
table-registration-app 29 + permission-request-app 60,后者这次从 52
补到 60)。

### 2.5 扩大 CI

**已迈出几步**(chart 渲染校验、DAG 单一源码、app ConfigMap 单一源码、
环境配置渲染防漂移、3 个 Flask 应用的测试)。原评审 P1-3 清单里更大的
扩展(镜像构建 —— 见 2.1、集成测试)还没做。

### 2.6 notebook 里直接调 `submit_job()` 被 singleuser NetworkPolicy 挡住

2026-08-19(ADR-058 第一批验证)发现:`platform_sdk.submit_job()` 这个
函数本身完全正确——从不受限制的环境(本机 kubeconfig)调用端到端成功过
(建 ConfigMap→建 Workflow→pod 里跑成功→查 Trino→记 MLflow,全部
Succeeded)。但**直接从 JupyterHub 的 notebook pod 里调这个函数**会被
chart 默认的 `singleuser` NetworkPolicy 挡住连 K8s API server——试过
ipBlock 指向节点 IP(172.22.9.16,配 443 和 6443 两个端口)、指向 API
server 的 ClusterIP(10.43.0.1,配 /32 和 /16 两种 CIDR),**全部还是
ConnectionRefused**,没有查清根因。K8s API server 在 k3s 里不是普通
pod(是 k3s 进程自己在节点上起的,`kubectl get endpoints kubernetes
-n default` 能看到 Endpoints 指向的是节点 IP 不是 pod IP)——
namespaceSelector/podSelector 这两种 NetworkPolicy 选择器天然匹配不到,
必须用 ipBlock,但 ipBlock 也没通,不排除是这台 k3s 内置 netpol 控制器
(kube-router)对这类流量的已知限制,需要上节点直接查 iptables/ipset
才能查清根因,当时判断这个排查成本(云主机按小时计费)不该继续投入。

**影响范围窄**:只影响"直接从 notebook pod 里调 submit_job()"这一种
使用方式,`query()`/`mlflow_setup()` 这两个更常用的函数都已经在
notebook pod 里验证过完全正常。

**当前权宜做法**:用 `platform-submit job.yaml` 从终端/CI 这类不受这条
NetworkPolicy 限制的环境提交,不是从 notebook 里直接调。真要修,下次
有专门时间时,从 SSH 到节点、直接查 kube-router 生成的 iptables/ipset
规则开始查,不要重复这次试过的几种 ipBlock 组合。

---

## P3:打磨已有角色能力(不需要新组件,是体验问题)

- **告警送不到人**:Alertmanager 已开、规则生效、抓到过真实问题,但
  **没有配任何外部通知渠道**,现在只能"打开界面查"。邮件/企微/Slack 的
  配置模板已预留在 `platform/apps/kube-prometheus-stack.yaml` 注释里,
  需要真实凭据才能激活(**这一步需要 zhenghe 提供,不是 Claude 能自己
  造的**)。
- **排障知识 Runbook 化**:`docs/operations/troubleshooting.md` 742 行、
  内容扎实,但一篇长文不是 Runbook——没有按"症状 → 定位 → 处置"组织,
  出事时不好检索。
- **Superset 汉化**:zhenghe 2026-08-16 提出,自己说了"不急"。Superset
  原生带中文语言包(`LANGUAGES`/`babel`),预期加几行 configOverrides
  就够,真做时先确认 6.1.0 的中文翻译完整度。
- **dbt 接 Airflow 编排 + OpenMetadata 摄入**:ADR-053 的最小骨架能跑
  `dbt build`,但没接 Cosmos(需要改 Airflow scheduler/dagProcessor 的
  Python 运行时)、没接 OpenMetadata dbt 连接器,等于"能手动跑 dbt",
  不是分析师的生产工具。
- **公网域名 + TLS 接入**:zhenghe 2026-08-16 明确的方向——"域名走配置化
  生效,配置 test 起来就是可临时访问的;未来配置 prod,就强制需要配置
  一个域名"。`test` 类环境允许没有真实域名(继续 NodePort + `/etc/hosts`);
  `prod` 应**强制**要求真实域名 + TLS,是校验层面的硬要求,不是建议。
  scheme(http/https)的配置化 2026-08-16 已经做完(`external_scheme`),
  剩下的是域名注册 + ICP 备案(中国大陆服务器强制,1-20+ 工作日,
  **需要 zhenghe 亲自做身份核验**)和 ingress-nginx/cert-manager 按环境
  分叉的设计,真正做时单独出一份 ADR。

---

## P4:五条面向角色的产品主线(长期,都还没开始)

完整方案见 `docs/architecture.md` "Phase 4 之后"一节和原始评审
`docs/claude-improvement-recommendations-2026-08-15.md`。**这五条现在都
还没有开始实现**,不要误以为在做。

- **A. 统一开发工作台**:项目模型 → SQL/Notebook 黄金路径 → 作业模板
  + CI/CD → 训练黄金路径。原则是"先做薄控制面,复用成熟组件,不重新
  自研查询引擎/调度器"。
- **B. 数据资产与治理闭环**:权限真正执行(✅ 已完成,ADR-051)+ 审计
  闭环 → 数据契约/质量规则 → 端到端血缘 + 变更影响分析 → 敏感字段行列级
  策略(OPA 原生支持,没配置)。
- **C. 完整 MLOps**:标准镜像 + 可复现训练 → 模型审批/灰度/回滚 → 推理
  可观测性 → 特征漂移监控。
- **D. 统一运维控制面**:服务目录 + 黄金链路告警 → 统一 Runbook → 容量/
  成本看板 → 多节点故障/备份恢复/升级回滚演练。
- **E. 管理驾驶舱**:不新建数据源,汇总现有系统指标;第一版只回答"平台
  健不健康、谁在用、资源花在哪、数据资产覆盖率、权限风险、模型健康度"。

排序建议(评审原文,已认可):可靠底座 → 统一项目模型 → 分析师黄金路径
→ 大数据开发黄金路径 → 算法黄金路径 → 运维控制面 + 管理驾驶舱(从前三条
产生的真实指标构建,不先造空看板)。

### 确认要做、还没设计的引擎/组件

- **Flink**:2026-08-15 zhenghe 明确"作为新的大数据平台有它的必要性",
  从"Phase 4 按需"改成"确认要做"。**设计已完成**([ADR-056](decisions/056-flink-role-design.md)):
  定位成"流式计算引擎"(实时聚合/join/特征计算),不做"数据搬运"。
  引入顺序上 Kafka 现在零真实消费者、Kafka Connect + Iceberg Sink
  这条轻量路径也没搭过,这两步应先于 Flink。**只是设计,没部署任何东西。**
- **Spark 4.x 评估**:仓库固定 Spark 3.5.9,官方已到 4.x。核实结论
  "方向对,但 Codex 支撑这个方向的一条关键理由是错的"(Spark 4.0 的
  SPARK-45265 是 Spark 内置 Hive 客户端支持 Hive 4.0 metastore,和我们
  独立部署的 Hive Metastore 版本锁定不是一回事,升 Spark 4 并不能解除
  那个锁定)。3.5.9 不是废弃版本,但确实是旧的大版本线。升级要一起动
  Scala 2.13 / Java 17 / `iceberg-spark-runtime-4.x_2.13`。
- **KServe runtime 矩阵设计**:TF Serving(2.6.2)/ TorchServe(0.9.0)
  要不要精简或换掉,是故意没做的更大设计判断,不是遗漏(7 个浮动
  `latest` 的固定版本 + digest 已经在 2026-08-15 做完)。
- **Stackable(Spark/Trino/Hive 统一 Operator 平台)**:2026-08-15 Codex
  新提出,**目前完全没评估过**。值得单独做 PoC(非生产、对比部署/升级/
  故障恢复成本、保留绕开它直接用官方 Operator 的能力),**不迁移任何
  现有组件**。已知代价:版本支持明显滞后社区(Spark 长期支持线停在
  3.5.8),CRD/Operator/镜像形成中等到较高的平台绑定。

---

## P5:项目瘦身审计(zhenghe 2026-08-16 明确提出)

原话:"现在我怀疑项目臃肿,最后还需要瘦身,作为开源项目,里面很多应该
是没有用的。"排在这里不是因为不重要,是因为**在 P1/P2 落地之前做瘦身,
会把还没定型的东西提前删掉**——等交付方式稳定了再审计更准。

审计范围:

- **demo/验证脚本是不是还都有价值**:`scripts/08/09/11/13/15/18/19`
  这类,哪些是"证明平台能力的可复现示例"(该留),哪些是"当时验证完
  就没用了"(该删)。
- **`scripts/` 的编号约定已经撑不住**:51 个文件,00-26 带编号(但执行
  顺序其实由 `bootstrap-all.sh` 负责)+ 24 个没编号,混着部署步骤/演示/
  本机便利工具/云主机生命周期/临时补丁五类东西。
- **试错留下的死代码/废弃方案痕迹**。
- **历史踩坑记录该不该继续散在代码注释里**:像 SSO 四层故障链这种,
  manifest 注释里现在动辄二三十行。对维护者有用,但对第一次读的人是
  噪音——考虑保留结论、细节移到 ADR/journal。
- **`environments/cloud-full/pending-definitions/`**:P1.1 做完之后这个
  目录整个消失了(2026-08-20 完成)。

---

## 曾经提出、明确决定不做/暂缓的

- **需求追踪矩阵**(`docs/requirements.md`,给每条用户需求分配 ID 逐条
  追踪):讨论过,判断是对当前规模过重的流程负担,ADR + BACKLOG + roles.md
  已经覆盖"决策留痕"和"待办不丢"这两个真实需求。
- **自建 `scripts/task-runner.sh`**(start/status/logs/stop/resume 的
  后台任务管理器):当前后台任务的规模用不上,`nohup` + 日志文件够用。
- **正式的多工作流并行调度表**(workstream ID/资源预算/依赖表):当前
  是单人 + 单个 CURRENT 的工作方式,不需要这一层。
- **PostHog 或同类产品分析工具**:算法层面的 A-B 实验应该落在 KServe
  canary + 现有 MLflow/湖仓链路,不是消费端产品分析工具。见
  `docs/architecture.md` "还没定的事"里 2026-08-11 那条。
- **Backstage / Kubeflow 整套部署**:只借鉴实体模型/能力划分,不部署
  整套 UI。见 ADR-028/032 和 architecture.md 的判断。
- **Ranger**:官方不维护 Helm chart,不满足"只用官方支持的部署方式"
  门槛;改用 OPA(已上线,ADR-051)。

---

## 已完成(存档,按完成时间倒序)

保留一行索引,细节在对应 ADR / `docs/journal/`。

- **环境配置渲染机制 + `external_scheme`**(2026-08-16/19):
  `environments/<env>/config.yaml` + `templates/` +
  `render-environment-config.py`,域名/端口/协议三类环境差异值单一源码,
  `--check` 接进 CI。**只覆盖"取值"这一层**,组件选择和规格分档见 P1.1。
- **cloud-full SSO 全链路修复**(2026-08-16):四层故障链(redirect_url
  丢端口 → Keycloak hostname 推断丢端口 → issuer/backchannel 前后端地址
  冲突 → nginx 响应头缓冲区太小),外加 Superset 的 FAB keycloak provider
  需要显式 `api_base_url`。全部用 curl + cookie jar 端到端验证过真实登录。
  详见 `docs/journal/2026-08.md`。
- **Trino OPA 细粒度访问控制正式上线**(2026-08-16,ADR-051)。
- **破坏性操作防护补全**(2026-08-16):`confirm-destructive-kubectl.sh`
  + namespace 允许清单,背景见 `docs/operations/incidents.md`。
- **镜像缓存 digest 校验**(2026-08-16):`verify-image-digests.sh`。
- **iam-sync / opa-grants-sync 在 cloud-full 上修复并恢复**(2026-08-16)。
- **`alloy`/`loki`/`kube-prometheus-stack` 的 ArgoCD 同步超时**
  (2026-08-16):`controller.repo.server.timeout.seconds: 180`。
- **一键部署 `scripts/bootstrap-all.sh`**(2026-08-16):14 步幂等,
  真实跑通过。
- **版本审计与升级**(2026-08-15/16):PostgreSQL 16.6→16.15;Feast Redis
  `7-alpine`(浮动、且落在 Redis 非开源许可证区间 7.4.x)→ 固定 8.4.5
  (同时修 RESTORE 命令的 use-after-free RCE 和许可证问题);Triton
  23.05→26.07-py3;KServe 7 个浮动 `latest` 全部固定版本 + digest;
  Python 依赖锁定 5 处。**刻意没升的**:Kafka 4.3.0→4.3.1(Strimzi 1.1.0
  的 `kafka-versions.yaml` 还没收录 4.3.1,升上去会被拒绝);Trino
  480→483(483 对 `http-server.http.port` 收紧校验,和我们关闭 HTTP
  监听器的配置冲突,拉过 chart 源码确认根因,483 无 CVE 动机,不值得为
  版本号打开一个明文内部端点——见 `apps/definitions/trino.yaml` 注释)。
- **Airflow DAG 单一源码校验**(2026-08-16):`sync-airflow-dags-configmap.py`。
- **权限治理全链路**:分级审批(ADR-044)、可插拔审批后端 + 通知 + 超时
  升级 + 权限交接(ADR-045)、到期自动回收(ADR-050)、浏览目录申请
  (ADR-046,**体验依赖 OpenMetadata**,见 P1.2)。
- **CloudNativePG 迁移**(2026-08-13,ADR-038/039):含真实数据迁移、
  切流量,老实例已下线。真正的多副本 HA 要等 prod 多节点。
- **推倒重建验证**(2026-08-13,ADR-039):从空集群完整跑通一次。
