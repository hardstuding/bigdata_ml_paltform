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

依据 [`docs/roles.md`](roles.md):平台今天只对运维完整、对分析师大体
可用,大数据开发和算法工程师是**结构性缺失**。这一批的目标是让每个角色
至少能开工。

### 1.1 环境抽象补上"组件选择"层 —— 一个动作解锁三个角色

**这是下一步唯一动作**(见 `CURRENT_WORK.md`)。cloud-full 上 8 个组件
没启用,不是资源不够(16 vCPU / 64 GiB),是"哪些组件在哪个环境启用"
至今靠人工在 `apps/definitions/` 和
`environments/cloud-full/pending-definitions/` 之间 `git mv`——这个机制
是 2026-08-08 为 6GB 的 colima 本机设计的,搬到云上没有重新评估过。

要做的:让"启用哪些组件"变成 `environments/<env>/config.yaml` 里的声明,
和已有的 `scripts/render-environment-config.py` 机制衔接(不是再造一个
新脚本),`pending-definitions/` 这个靠目录位置表达启用状态的机制退役。

同一层还缺"规格分档"(副本数/resources/持久化按环境取值),可以一起做,
优先级低于组件选择。

### 1.2 部署 OpenMetadata —— 解锁分析师"找数据"+ 治理"资产盘点"

分析师链路**第一步就断了**:不知道有哪些表。同时它还卡住 ADR-046 那个
"浏览目录勾选表"的申请体验(没有它就退化回手打完整表名)、以及治理角色
的数据资产盘点。**一个组件同时解锁三处**,是单项性价比最高的。

组件本身验证过(ADR-015,2026-08-13 连 CNPG 验证通过),等 1.1 做完
应该是改配置就能起。

### 1.3 部署 JupyterHub + MLflow —— 算法工程师从"零"到"能开工"

今天算法角色连第一步(打开 notebook)都做不到,唯一部署好的 KServe 是
流程最末端。这两个是最小解锁组合(ADR-025 / ADR-019 都验证过)。

注意:拉起来只是"能开工",**不等于体验成立**——`docs/usage-guide.md`
如实记录了"没有打开 notebook 就自动连好 Trino、自动带自己权限"这个
真实产品差距,那属于 P4 的 A 线。

### 1.4 部署 Spark Operator + SeaTunnel —— 大数据开发从"零"到"能开工"

Airflow(调度器)已经在跑,但它要调度的引擎全部没部署。这两个都单独
验证通过过(ADR-036 / ADR-037)。Kafka 可以稍后(它目前零真实消费者,
见 ADR-056 对引入顺序的判断)。

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

### 2.4 三个自建 Flask 工具的测试补完

**主体已完成**(2026-08-16,60 个测试接进 CI)。剩余缺口如实记录:
`GIT_TOKEN` 在测试环境始终不配置,所以真正执行 git clone/push 的分支
(`apply_to_git`/`apply_grant_to_git`/`reclaim_expired`/`transfer`)和
真实 POST 到外部 OA webhook 的网络路径,只测到"没配置时优雅降级",没有
测到真实调用本身。要补需要搭临时本地 git 仓库或更细的 mock。

### 2.5 扩大 CI

**已迈出几步**(chart 渲染校验、DAG 单一源码、app ConfigMap 单一源码、
环境配置渲染防漂移、3 个 Flask 应用的测试)。原评审 P1-3 清单里更大的
扩展(镜像构建 —— 见 2.1、集成测试)还没做。

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
  目录应该整个消失,届时一并清理。

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
