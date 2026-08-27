# ADR-079:按"链路"探测,不按"组件"探测(D 线第一步)

日期:2026-08-27
状态:已实现,**未部署验证**

## 问题:所有东西都绿,但没人能回答"平台现在好不好"

到 2026-08-26 为止,这个平台有 8 条告警规则、3 个 Grafana 看板、ArgoCD 的
应用健康、还有 `docs/roles.md` 的能力表。但它们回答的都是**组件层面**的
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

### `model` 刻意不探 KServe 推理

集群里现在一个 InferenceService 都没有(`kubectl get inferenceservice -A`
是空的),探它只会得到一条**永远红**的告警——而一直红的告警比没有告警更糟。
等真有常驻推理服务了再加。

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

而 `docs/roles.md` 里"实验跟踪 / 模型注册"和"训练执行"两格都写着 ✅,依据是
2026-08-19 那次真实验证——**那次验证是真的,但它验证的东西后来没了,而没有
任何机制发现这件事**。

这正是这套探针要解决的问题:**能力表记录的是"某天验证过",探针回答的是
"现在还成不成立"**。两者会分叉,而分叉的时候只有后者会告诉你。

重跑 `scripts/09` 之后探针转绿(`demo-rf-classifier 有 1 个 READY 版本`)。

## 还没做的

1. **没部署验证。** 三条探针都还没在集群上跑过一次。
2. **算法链路只探到 MLflow 为止。** 完整的 notebook → submit_job → Argo →
   MLflow → KServe 要真跑一次训练,成本高得多;现在探的是"训练的产物还
   取得出来",覆盖了这条链上最容易悄悄坏的那一段(元数据在 Postgres、
   产物在 MinIO,断一个就取不到而 MLflow 首页照样打得开)。
3. 审计链路故意没做探针:它已经有 `AuditSinkJobNotRunning`(作业级),而要
   探它就得读 `audit.query_events`,那张表[刚刚才收紧](066-trino-query-audit.md)
   成只有 platform-team 能读——为了探针去开一个口子不划算。
