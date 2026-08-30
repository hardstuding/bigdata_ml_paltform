# ADR-079:按"链路"探测,不按"组件"探测(D 线第一步)

日期:2026-08-27
状态:**已部署并实机验证**(2026-08-29:六条探针最近一次全部 Completed)

## 问题:所有东西都绿,但没人能回答"平台现在好不好"

到 2026-08-26 为止,这个平台有 8 条告警规则、3 个 Grafana 看板、ArgoCD 的
应用健康、还有 `docs/project/capability-matrix.md` 的能力表。但它们回答的都是**组件层面**的
问题:某个 Pod 活着吗、某个指标在不在。

而这个项目反复吃的亏恰恰是**组件全绿、链路是断的**:

- ArgoCD Synced 不等于生效(OPA 策略从没热加载过,[ADR-071 那次](071-platform-alert-rules.md))
- Pod Running 不等于健康(Flink JM/TM 都 Running 而作业已 FAILED,[ADR-062](062-flink-streaming-pipeline.md))
- Job Complete 不等于业务逻辑跑对(OpenMetadata 采集 `Running 0/1` 其实被配额拦住)
- 规则写了不等于会触发(`is_platform_admin` 因为没有 group provider 从未生效,[ADR-078](078-trino-group-provider.md))

**这四个都不是靠加一条组件告警能发现的。** 它们的共同点是:每一层单独看都
正常,只有"从头做一件事"才会失败。

## 决策:每条黄金链路一个 CronJob,做一件真实的小事

三条链路,一条一个 CronJob:

| 链路 | 探针实际做的事 | 一路经过 |
|---|---|---|
| `goldenpath-query` | `select count(*) from iceberg.demo.orders` ≥ 1 | Trino → Iceberg → MinIO / Hive Metastore → **OPA** |
| `goldenpath-streaming` | `demo.device_events_stream` 最新一条数据不超过 N 分钟 | Kafka → Flink → Iceberg |
| `goldenpath-catalog` | OpenMetadata 里 `trino.iceberg.demo.orders` 有字段 | Trino 元数据 → 采集 → 目录 |
| `goldenpath-authz` | 拿一张**没有 grant** 的表去查,**期望被拒** | OPA 策略 + grants 同步 |
| `goldenpath-model` | MLflow 注册表里 demo 模型有 READY 版本 | MLflow → Postgres(元数据)+ MinIO(产物) |

### `authz` 这条方向是反的,值得单独说

前四条探的都是"事情做得成";**`authz` 探的是"该拒的有没有被拒"**。

如果 OPA 悄悄不生效了(策略加载失败、有人加了一条放行所有的规则、grants
同步挂了),**前面几条探针全都还是绿的**,而平台已经门户大开。

这不是假想。2026-08-26/27 两天里这个控制悄悄坏过两次:

- `is_platform_admin` 因为 Trino 没配 group provider,**从来没触发过**
  ([ADR-078](078-trino-group-provider.md));
- Superset 用共享服务账号连 Trino,导致列级脱敏和行级过滤在 BI 这条路上
  **完全不生效**([ADR-074](074-superset-impersonation.md))。

**两次都是查别的东西时顺手发现的,不是被监控发现的。** 这条探针就是为了让
下一次不用靠运气。

实现上有个细节:探针遇到"别的错误"(表不存在、Trino 挂了)时**不能当成
授权生效**——那会让它在平台整体故障时反而变绿,是最坏的一种假阳性。所以
它明确要求错误里出现 `PERMISSION_DENIED`。

### `model` 一开始刻意不探 KServe 推理,2026-08-28 补上了

第一版的理由是:集群里一个 InferenceService 都没有,探它只会得到一条**永远
红**的告警——而一直红的告警比没有告警更糟。

[ADR-080](080-model-approval-and-rollback.md) 把那条链路真正跑通之后(训练→
注册→审批→部署→真实推理返回预测),这个理由不成立了,于是补上第六条
`goldenpath-inference`。

它探的是**发一次真实请求拿到预测**,不是 Pod Ready。这个区分在这条链路上格外
重要——08-28 实测撞到过两种"Pod 健康而服务不可用":Ready=True 但模型根本没
拉下来(NetworkPolicy 挡住 MinIO);模型拉下来了但特征维度对不上,请求直接
报错。**两种状态下 Pod 都是绿的。**

而且它还检查返回体里**真的有预测值**,不只看状态码 200:模型加载了却没算出
东西是一种"半通"状态,只有看内容才发现。

### 为什么是"三个 CronJob"而不是"一个 CronJob 跑三条"

这是**为了拿到指标**,不是拆得太碎。每条链路独立成 CronJob 之后,
kube-state-metrics 天然给出
`kube_cronjob_status_last_successful_time{cronjob="goldenpath-query"}`
——看板和告警直接用它,**不用引入 Pushgateway 这类新组件**。

告警判据用 `last_successful_time` 而不是"最近有没有失败的 Job",也是想清楚
的:**探针卡住不结束的时候不会产生 Failed Job**,但也不会有新的成功。这两种
情况都该告警,而只有前者能被"有失败 Job"抓到。

### 探针账号刻意不走豁免

`goldenpath_probe` **不在 OPA 的 `service_accounts` 豁免名单里**,它的权限
来自 `platform/iam/table-access-grants.csv` 里两条真实的 grant。这样探针走的
是和真实用户一模一样的授权路径——**用豁免账号探测的话,OPA 的 grants 同步挂
了探针也照样绿**,那就白探了。

### streaming 的阈值按环境分档

`goldenpath_streaming_max_age_min`:local-lite 120 分钟 / cloud-full 60 /
prod 30。local-lite 那一档 Flink/Kafka 常是 park 状态,给短了只会一直红,
而**一直红的告警比没有告警更糟**(这个判断在 ADR-071 和
`check-image-arch.py` 不进 CI 那次都用过)。

## 实现上的两个细节

1. **只用 Python 标准库**,不 pip install,不引新镜像——和
   `openmetadata-quality-alerts` 那个 CronJob 同一个模式(`python:3.12-slim`
   + 内联脚本)。查 Trino 用它的 REST 协议(`POST /v1/statement` 之后跟着
   `nextUri` 翻页)。
2. **必须跟着 `nextUri` 跟到底**。Trino 是流式返回的,第一个响应通常还没有
   数据;不跟到底就判断"没查到数据"是错的——这是用裸 REST 调 Trino 最容易
   踩的坑,写在脚本注释里了。

## 实机验证(cloud-full,2026-08-27)

五条探针都在真集群上跑过,**其中两条第一次跑就抓到了东西**:

| 链路 | 结果 |
|---|---|
| `query` | ✅ 0.2s,10 行 |
| `streaming` | ✅ 最新数据 5 分钟前(**开机前它报的是"1327 分钟前",停机 22 小时后自己恢复的**) |
| `catalog` | ✅ 6 个字段(第一版报 401——我以为表实体匿名可读,错了,改成复用 bot token) |
| `authz` | ✅ 没有 grant 的表确实被拒 |
| `model` | ❌→✅ **第一次跑报 404:MLflow 注册表是空的** |

### `model` 这条第一次跑就抓到一个真问题

MLflow 里**一个注册模型都没有**,实验只有 `Default`。也就是说 2026-08-22
那次推倒重建之后,`scripts/09-train-demo-model.sh` 再没跑过,**算法链路的
产物一直是空的**。

而 `docs/project/capability-matrix.md` 里"实验跟踪 / 模型注册"和"训练执行"两格都写着 ✅,依据是
2026-08-19 那次真实验证——**那次验证是真的,但它验证的东西后来没了,而没有
任何机制发现这件事**。

这正是这套探针要解决的问题:**能力表记录的是"某天验证过",探针回答的是
"现在还成不成立"**。两者会分叉,而分叉的时候只有后者会告诉你。

重跑 `scripts/09` 之后探针转绿(`demo-rf-classifier 有 1 个 READY 版本`)。

## 一个副作用:探针失败会把 ArgoCD 应用标成 Degraded

五条全绿之后,`golden-path-probes` 这个 Application 在 ArgoCD 里仍然显示
**Degraded** —— 因为 `failedJobsHistoryLimit: 3` 保留着之前失败的 Job,而
ArgoCD 看到 Failed 的 Job 就判 Degraded。

**这个信号是错位的**:探针失败说明"它抓到了东西",不说明"探针这个组件坏了"。
放着不管的后果很具体——ArgoCD 上会常年挂一个 Degraded,而这个项目已经花了
力气去消除常年黄灯(比如 flink CRD 那次),就是因为**常年黄灯会训练所有人
忽略黄灯**。

**已修**(2026-08-27):给 ArgoCD 加了 `batch/Job` 的自定义健康检查(Lua),
只对带 `platform/golden-path` 标签的 Job 返回 Healthy,**其余 Job 保持
ArgoCD 原本的语义**——db-init 这类 Job 失败仍然要变 Degraded。

另一个方案是把 `failedJobsHistoryLimit` 调到 0,否掉了:那样就没法
`kubectl logs` 看失败原因,得不偿失。

两个实现细节:

- **标签要打在 `jobTemplate.metadata.labels` 上,不能只打在 CronJob 上**
  ——CronJob 自己的 labels 不会传给它生出来的 Job,而健康检查认的是 Job 上
  的标签。
- **这段 Lua 有单元测试**(`tests/test_argocd_health_lua.py`,用 lupa 真跑
  一遍,不是数括号,已进 CI)。给 25 行 Lua 写测试看着夸张,但它跑在 ArgoCD
  控制器里,**写错的表现不是报错,是所有 Job 的健康判断都变形**:漏了
  `Failed` 分支的话,db-init 失败就再也不会让 Application 变红,而这段代码
  平时没人会去看。测试里"普通 Job 失败仍然变红"那条比"探针失败不变红"更
  重要——前者写错是少一个告警,后者写错是**丢掉一整类真实故障的信号**。

原则记下来:**失败信息应该只从告警和看板出去,不该混进部署状态里**——这两
套信号回答的是不同的问题(部署对不对 vs 平台好不好),混在一起两边都变钝。

## 探针给一键部署路径提了新要求

加完探针之后才意识到:**它们依赖 demo 数据存在**。`query`/`authz` 查
`iceberg.demo.orders` 和 `demo.access_test_l1`,`model` 查 MLflow 里的
`demo-rf-classifier`。而在这之前:

- `scripts/08`(建 demo 数据)和 `scripts/09`(训练模型)**都不在
  `bootstrap-all.sh` 里**;
- `access_test_l1/l2` 和 `regional_sales` 三张表更离谱——它们是
  `scripts/36`(一个**验证脚本**)顺手建的,而 `table-access-grants.csv` 里
  analyst001 的 grant 正指向它们。也就是说不跑那个验证脚本,那几条授权就
  指向空气。

后果很具体:**全新集群装完,五条探针里有三条从第一天起就红**。而
「一直红的告警比没有告警更糟」——人第一反应是"这套监控不准",然后就不看了。

处理:三张表的定义挪进 `scripts/08`(demo 数据该由 demo 数据脚本建),
`scripts/08` 和 `09` 加进 `bootstrap-all.sh`。`scripts/36` 里保留
`CREATE IF NOT EXISTS` 作为单独运行时的兜底。

**这一条值得单独记的地方**:加监控本身给部署路径提了新的完整性要求,而
这个要求不会自己冒出来——是"探针依赖什么"这个问题逼出来的。加探针的时候
顺手想一遍"它在全新集群上是什么颜色",能省掉一次"上线就一片红"。

## 还没做的

1. **没部署验证。** 三条探针都还没在集群上跑过一次。
2. **算法链路的「训练」那一段仍然没探。** 现在 `model` 探"产物取得出来"、
   `inference` 探"在线服务算得出来",覆盖了注册表之后的全部;但
   notebook → submit_job → Argo 这一段要真跑一次训练,成本高得多,还没做。
3. 审计链路故意没做探针:它已经有 `AuditSinkJobNotRunning`(作业级),而要
   探它就得读 `audit.query_events`,那张表[刚刚才收紧](066-trino-query-audit.md)
   成只有 platform-team 能读——为了探针去开一个口子不划算。

---

## 2026-08-29 更正:那段 `batch/Job` 的 Lua 其实没解决它想解决的问题

这条 ADR 里加了 `resource.customizations.health.batch_Job`,理由是"探针
失败说明它抓到了东西,不说明探针这个组件坏了,不该让 Application 变红"。
**当时以为生效了,实际上挂错了对象。**

Trino 因为 startupProbe 预算不够重启了 9 次(见 `apps/components/trino.yaml`
里的说明),期间 query/authz/inference/model 几条探针全部失败。Trino 修好
之后:

- `golden-path-probes` 这个 Application 一直是 `Degraded`
- `.status.resources` 里 7 个资源(1 个 ConfigMap + 6 个 CronJob)的
  health **全是空的**,一条 Degraded 都没有——从这里完全看不出是谁的问题
- 把所有失败的 Job 和 Pod **全删干净,依然是 Degraded**
- `argocd app get golden-path-probes` 才看到真相:6 个 CronJob 里
  `goldenpath-inference` 和 `goldenpath-model` 是 Degraded
- 手工触发一次成功的 inference/model 探针之后,**整个 Application 立刻
  回到 Healthy**

也就是说:变黄的是 **CronJob**,而 Lua 加在 `batch/Job` 上。这段 Lua 在
过去一直"看起来是对的",只是因为那阵子探针一直是通的。

### 试过的两条修法,都撤回了

1. **给 `Pod` 加自定义健康检查**(先按"是 Pod 冒上来的"这个错误判断做的)
   —— 那等于用一段简化的 Lua 重写 ArgoCD 内置的 Pod 判定,
   `CrashLoopBackOff` 这类会被误判成 Healthy。为了修一个黄灯把整个平台
   "部署好没好"的主要依据变钝,不划算。
2. **给 `batch/CronJob` 加**,对带标签的返回 Healthy、其余返回空 status
   想"交回内置判定" —— **空 status 不是 ArgoCD 的 fallback 约定**。实测
   结果是 `iam-sync` / `opa` / `openmetadata` / `postgres-backup` /
   `trino-liveness-fix` 等 **8 个带 CronJob 的 Application 全部变成
   `Unknown`**。ArgoCD 的自定义健康检查一旦为某个 kind 定义,就完全接管
   那个 kind,没有"这一个我不管"的写法。

### 现在的结论:维持现状,不修

要做对得把 ArgoCD 内置的 CronJob 判定完整重写一遍。而这个黄灯的实际影响
是:某条探针失败之后,`golden-path-probes` 会黄一段时间,**下一次探针跑通
就自己好了**(实测确认)。为了消掉一个会自愈的黄灯去重写平台健康判断的
一部分,代价和收益不成比例。

`batch/Job` 那段 Lua 保留 —— 它本身没有害处,而且 Job 层的语义是对的,
只是不够。

**这条更正本身比结论重要**:一个"加了就不再管它"的健康检查定制,在
真正需要它的那次故障里没起作用,而且从 Application 的 `.status.resources`
里根本看不出来。以后再判断"某个 Application 为什么是 Degraded",
`kubectl get app -o json` 不够,要用 `argocd app get`。
