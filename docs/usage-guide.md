# 使用指南

给**用这个平台干活的人**看。运维视角的文档在 [`docs/operations/`](operations/)。

**每一节的结构都一样**:前置条件 → 操作 → 预期结果 → 常见失败。看到"预期
结果"里那句话,才算这一步真的成了 —— 这个平台被"命令没报错就以为成了"坑过
太多次。

## 先做这两件事

**1. 所有链接从门户拿,不要抄文档里的地址。** 门户会按当前环境拼出正确的
域名和端口,还会现场探测每个工具在不在线。这份文档**不写任何具体 URL**,
因为写了就会过期 —— 2026-08-16 真实发生过:文档和门户里的链接全部硬编码
成一套域名,换到另一档环境后**点哪个都是 404**。

**2. 确认自己在哪个组。** 所有工具共用同一个 Keycloak 账号(realm
`platform`),登录一次全平台通用;而**你在哪个组决定你能用什么**:

| 组 | 拿到什么 |
|---|---|
| `platform-team` | 平台管理权限;Trino 里能查审计表和全部行 |
| `data-analysts` | SQL 工作台 + BI 看板编辑 |
| `algorithm-team` | SQL 工作台 + 看板只读 |
| 不在任何组 | 只能看,不能查 |

不知道自己在哪个组,看 `platform/iam/memberships.csv`,或者找 `platform-team`
的人。

---

# 数据分析师

## 查数据

**前置条件**:你在 `data-analysts` 或 `algorithm-team` 组;要查的表已经
申请过权限(见下一节)。

**操作**:从门户点「SQL 工作台」。那是 Superset 的 SQL Lab —— 编辑器、执行、
历史、导出 CSV 都在里面([ADR-084](decisions/084-analyst-sql-workbench.md))。

> **不要去 Trino 的 Web UI 写 SQL,那里没有编辑器。** 它只能看查询在跑什么、
> 执行计划、耗时。门户上曾经把它介绍成 SQL 工作台,是错的,已经改了。

**预期结果**:能选到 Trino 数据源,`SELECT` 出行。

**常见失败**:

- **左边看不到「SQL Lab」菜单** —— 你的组没有 `sql_lab` 角色。检查
  `memberships.csv`,重新登录一次(角色在登录时同步)。
- **查得到表名但查不出数据** —— 没权限,不是 bug。走下面的申请流程。
- **某些列是 `138****5678` 或 `***MASKED***`** —— 列级脱敏在起作用
  ([ADR-063](decisions/063-trino-column-row-level-security.md)),按你的授权等级
  决定看到明文还是打码。**这是正常的,不要当故障报。**
- **同一张表别人比你多几行** —— 行级过滤,按你所在部门过滤。同上。

## 申请表权限

**前置条件**:知道要哪张表(在 OpenMetadata 里搜)。

**操作**:门户点「权限申请门户」发起申请。**不要手改
`platform/iam/table-access-grants.csv`** —— 那份文件是这个门户自动写的。

**表名要带 catalog**:`iceberg.demo.orders`(从 Superset 或 Trino 里看到的
那个写法),或者完整的 `trino.iceberg.demo.orders`。只写 `demo.orders` 会
拿到"数据目录里查不到这张表" —— 那句话听起来像表不存在,实际是少了一层。

**预期结果**:审批通过后能查到。审批链按表的安全等级逐级叠加(直属上级 →
上级的上级 → 表负责人 → 指定管理员),等级越高链越长
([ADR-044](decisions/044-tiered-approval-workflow.md))。

> **审批不只是留痕,是真的拦得住。** Trino 接了 OPA
> ([ADR-051](decisions/051-trino-opa-access-control.md)),没批准就是查不到 ——
> 2026-08-26 在真集群上用真实 SQL 验过。旧版指南里"只做决策与留痕、不做
> 真正的查询拦截"那句话是过期信息,**反了**。

**常见失败**:

- **申请当场被拒,理由是"查不到这张表的安全等级"** —— 这张表没登记过
  等级。**采集器自动发现的表都没有**(等级是建表注册工具在建表那一刻写的),
  接入一个已有数仓时会成片出现。管理员跑
  `./scripts/58-backfill-security-levels.sh <schema>` 补登记。
- **权限"突然没了"** —— 授权默认 180 天过期,到期自动回收
  ([ADR-050](decisions/050-grant-expiry-reclamation.md))。重新申请,不是 bug。
- **审批显示通过但还是查不到** —— 看申请单状态是不是
  `approved_pending_apply`:审批决定成了,但写进 git 那一步失败了。管理员
  跑一次 `/internal/retry-pending-applies` 补上。

## 做看板

**前置条件**:对要用的表有权限。

**操作**:门户点「Superset」。

**预期结果**:数据源已经接好 Trino,直接建图。

**要知道的一件事**:Superset 连 Trino 走 **impersonation**
([ADR-074](decisions/074-superset-impersonation.md)) —— 查询按**你本人**
的权限算,不是按一个共享服务账号。所以你做的看板,别人打开时看到的行数
可能和你不一样。**这是设计如此。**

## 把看板分享给指定的人

**先理解一件事:授权的单位是「角色」,不是一个个人。** 平台上的角色是从
Keycloak 的组同步过来的,登录时自动完成:

| Keycloak 组 | Superset 角色 | 能做什么 |
|---|---|---|
| `platform-team` | Admin | 改数据源、装插件,维护这套东西的人 |
| `data-analysts` / `algorithm-team` | **报表开发** | 建看板、建图表、用 SQL Lab、建告警 |
| `viewers` | **业务查看** | **只能看**别人分享给他的看板 |
| (没分组) | 业务查看 | 同上 —— 最小权限,不是管理员 |

「业务查看」和「报表开发」是这个平台自建的两个角色,不是 Superset 内置的
Alpha/Gamma。**内置角色里没有"只能看"这一档** —— Gamma 看着像只读,实际
带 `can_write|Chart` 和 `can_write|Dashboard`,是能建内容的。

**操作**:看板 →「编辑属性」→「访问」→ 在「角色」里选要分享的角色 → 保存。

**必须做,不是可选的收紧**:业务查看角色本身没有任何数据源权限,所以
**一个不指定角色的看板,业务方登录进来是看不到的**。要让谁看到,就得把
对应的角色加进去。

**语义**(容易配反,记住这两句):

- 看板**指定了角色** → 只有这些角色的人能看到
- 看板**没指定角色** → 回退到按数据源权限判断(报表开发/Admin 仍然看得到)

所以给老看板加角色不会误伤别人,但反过来,把一个看板从"没指定"改成"指定
了某个角色"之后,不在那个角色里的业务方就看不到了。

**要分享给"某几个具体的人"而不是一个组**,现在的做法是让平台组在
Keycloak 里建一个组、把这些人放进去,再在 Superset 里把看板分享给对应
角色。**不建议**在 Superset 里手工建角色 —— 那样它和 Keycloak 的组对不上,
下次同步会漂移。

验证脚本:`scripts/56-verify-dashboard-sharing.sh`(含反向证伪)。

## 让看板定时给我发消息

**前置条件**:看板已经建好。

**操作**:「告警和报表」→ 新建 →「告警」,选数据源、写一条判定 SQL、设
触发条件和频率,收件人选 Webhook 填企业微信机器人的地址。

**要知道的几件事**:

- **告警必须关联一个看板或图表**,这是必填项。
- 收件人类型选 **Webhook**,平台已经把它的消息体换成了企业微信机器人认的
  markdown 格式([ADR-090](decisions/090-customization-layers.md)),
  地址直接填群机器人的 Webhook URL 即可。
- **内容格式选「文本」**。选 PNG/PDF 的话告警会一直卡住不发 —— 截图需要
  无头浏览器,当前镜像里没有,见
  [`production-readiness-gaps.md`](project/production-readiness-gaps.md)。
- 最小频率是 1 分钟。

验证脚本:`scripts/55-verify-superset-alerting.sh`(核对收件端真正收到的
字节,不看"状态成功")。

---

# 大数据开发

## 发一个定时批作业

**前置条件**:作业是单个 Python 脚本(多文件项目还不支持,见
[roadmap](project/roadmap.md) P1.5)。

**操作**:在 `jobs/<名字>/` 下放 `job.yaml` + 脚本,`job.yaml` 里写一行
`schedule`,push。

```yaml
schedule: "30 1 * * *"      # UTC
```

**预期结果**:ArgoCD 同步后,`kubectl -n argo-workflows get cronworkflow`
里出现同名的 CronWorkflow。照抄 `jobs/daily-order-summary/`。

**常见失败**:

- **`render-jobs.py --check` 在 CI 红了** —— 改了 `jobs/` 没重新渲染。跑
  `python3 scripts/render-jobs.py` 再提交。
- **作业起来了,一调 `query()` 就报 `MissingCredential`** —— 凭据 Secret
  `platform-job-credentials` 不在。**注意 `envFrom` 写的是 `optional: true`,
  Secret 不存在 Pod 照样会起来**,一路跑到 SDK 才炸。
- **定时到点没跑** —— 云主机大部分时间是关的,撞不上就不会触发。手工提交
  验证用 `argo submit --from cronwf/<名字>`。

## 发一个流作业

**前置条件**:数据在 Kafka topic 里,schema 登记过。

**操作**:`streams/<名字>/` 下放 `stream.yaml` + PyFlink 脚本,push。
照抄 `streams/device-events-stream/`。

**预期结果**:门户**首页**的「流作业」那一栏里出现它,状态是 RUNNING。
(是首页上的一栏,不是单独的页面 —— 门户没有 `/streams` 这个地址。)

**常见失败**(都是这个平台实测踩过的,完整清单见
[ADR-062](decisions/062-flink-streaming-pipeline.md)):

- **表里查不到数据但作业是 RUNNING** —— Iceberg sink 靠 checkpoint 提交,
  没到间隔就不会出现。先看时间,不是作业坏了。
- **字段名报解析错误** —— `value` 是 Flink SQL 保留字,要加反引号。同一套
  schema 在 Spark/SeaTunnel 那边没事,到 Flink 就炸。
- **在两个算子之后炸出完全不相干的报错** —— 多半是开了
  `json.ignore-parse-errors`,解析失败静默变 null。**不要开它。**

## 改 Kafka 消息的字段

**前置条件**:无。

**操作**:直接改。不兼容的改动**会在发送那一侧就被拒**,不会等下游炸了
才发现([ADR-068](decisions/068-schema-registry.md))。

```
加一个带默认值的可选字段    → 放行
删一个字段                  → 放行
改字段类型(double→string)  → 409 拒绝
```

**预期结果**:兼容的改动照常发送;不兼容的收到 409。

**规则是 `BACKWARD`**:新版本的消费者要能读老数据。改动被拒了,先想想下游
读老数据会不会坏 —— 答案通常就在那里。

看当前登记了哪些 schema(registry 故意没有对外 Ingress:谁能往里写 schema,
谁就能决定下游怎么解析数据):

```bash
kubectl -n schema-registry port-forward svc/karapace 8081:8081
```

## 跑一个 Spark 批处理

**操作**:`./scripts/13-run-spark-iceberg-demo.sh`。这个脚本本身就是可以
照抄的样例,你自己的作业照着 `apps/spark-iceberg-demo/manifests/` 改。

**预期结果**:脚本输出 `SPARK_ICEBERG_DEMO_OK`。它**用 Trino 回查结果表**
确认数据真的落盘,不是只看作业状态。

**常见失败**:

- **作业卡住不动** —— 用 `apps/spark-iceberg-image/` 那个镜像,别用官方
  `apache/spark`。官方镜像不带 Iceberg/S3A 的 jar,运行时去 Maven 现拉在
  这片网络下必卡死([ADR-036](decisions/036-spark-iceberg-pipeline.md))。
- **History Server 列表是空的** —— 最常见的原因不是它坏了,是作业压根没开
  `spark.eventLog.enabled`。

## 作业排障:先看哪一层

这个平台上有个反复出现的规律:**报错出现的位置和真正的根因经常隔着一到
两层**。实测过的:

- Flink checkpoint 一直失败 → 真因是 TaskManager 因为一个不存在的 ConfigMap
  起不来,算子根本没调度上去。
- OpenMetadata 采集显示 `Running 0/1` 看着正常 → 其实被命名空间配额拦住,
  一个 Pod 都没建出来,只有 `kubectl describe job` 看得到。

**顺序:先看 Pod 层(`kubectl get pods` / `describe`),再看应用日志。**
不要一上来就扎进应用日志。按症状检索的完整 Runbook 在
[`troubleshooting.md`](operations/troubleshooting.md),顶部有 59 条症状索引。

---

# 算法工程师

## 在 Notebook 里查数

**前置条件**:你在 `algorithm-team` 组。

**操作**:门户点「JupyterHub」,新建 notebook:

```python
from platform_sdk import query
df = query("select * from iceberg.demo.orders limit 10")
```

不用装 client、不用拼连接串 —— singleuser 用的是平台统一镜像,自带
`platform_sdk`([ADR-058](decisions/058-lightweight-developer-experience.md))。

**预期结果**:直接出数。

**身份是你自己的,不是共享账号。** SDK 从 `JUPYTERHUB_USER` 取当前登录用户,
用服务账号认证、以你的身份发起会话(impersonation)。所以:

```sql
SELECT current_user        -- 返回你的用户名,不是 notebook_service
```

没有 grant 的表查不到,列级脱敏对你生效 —— **和你在 SQL 工作台里看到的
完全一致**,不会出现"notebook 里能看到明文身份证号"这种事(这正是 2026-08-29
修掉的问题)。

**常见失败**:

- **`MissingCredential: PLATFORM_TRINO_USER`** —— 环境变量没注进来,找
  `platform-team`。
- **`current_user` 返回 `notebook_service`** —— impersonation 没生效。
  **注意这不会报错**,只会安静地用服务账号的权限查,是个危险的静默失败。

## 用公司内部的 Python 包

**前置条件**:包已经放进 `packages/` 并 push 过。

**操作**:直接装,**不用加任何参数**:

```bash
pip install platform-helpers
```

**预期结果**:装上、能 import。索引地址已经配在镜像的 pip 配置里
([ADR-083](decisions/083-internal-package-registry.md))。

**发布自己的包**:在 `packages/<名字>/` 下放标准的 `pyproject.toml` + 源码,
push。集群里的 Job 会构建 wheel、传 MinIO、更新索引。

**常见失败**:

- **装上了但版本是 `0.0.0`** —— `pyproject.toml` 里 name/version 没写全。
  **空的 `pyproject.toml` 不会构建失败**,会静默产出一个 0.0.0 的包。
  现在发布脚本会显式拦住这种情况。
- **`Could not find a version that satisfies the requirement`** —— 索引还
  没更新,等下一轮发布 Job。

> **Java/Maven 包还不支持**,是明确的待办不是遗漏。

## 提交训练任务

**操作**:

```python
from platform_sdk import submit_job, run_workflow_template
submit_job(name="train-model", script="train.py")   # 把脚本丢到集群上跑
run_workflow_template("train-demo-model")            # 触发已部署的工作流模板
```

**预期结果**:返回 workflow 对象,Argo Workflows UI 里查得到。

多步骤流水线(特征物化 → 训练 → 模型门禁)的样例在
`apps/argo-workflows-training-image/manifests/workflow-template-ml-pipeline.yaml`。

## 记实验、存模型

**操作**:在 notebook 里直接用 MLflow,不用配地址。

```python
import mlflow
mlflow.set_experiment("我的实验")
with mlflow.start_run():
    mlflow.log_metric("auc", 0.87)
    mlflow.sklearn.log_model(model, "model")
```

**预期结果**:门户点「MLflow」能看到这次 run 和模型产物。

**要知道的一件事**:`MLFLOW_TRACKING_URI` 是 notebook 环境自带的
(还有存模型产物用的 MinIO 地址和凭据)。**不要自己 `set_tracking_uri`
指到别处** —— mlflow 的默认值是本地 `./mlruns` 目录,写错了不会报错、
不会警告,只是实验记录留在容器里,notebook 一回收就没了。

## 上线一个模型

**前置条件**:模型已经注册进 MLflow Model Registry,**并且被批准过**
(`./scripts/41-approve-model.sh <模型名> <版本>`)。

**操作**:

```bash
./scripts/11-deploy-demo-inference-service.sh
```

**预期结果**:KServe InferenceService 就绪,V2 协议推理返回预测。

**发一次推理**(照抄能跑,别自己猜格式):

```bash
kubectl -n kserve-demo port-forward svc/demo-rf-classifier-predictor 8081:80 &

python3 - <<'PY' | curl -s -X POST -H "Content-Type: application/json" -d @- \
  http://127.0.0.1:8081/v2/models/demo-rf-classifier/infer
import json
n = 20   # demo 模型是 20 维,取决于训练用的数据集
print(json.dumps({
    "inputs": [{"name": "input-0", "shape": [1, n], "datatype": "FP32",
                "data": [0.1] * n, "parameters": {"content_type": "np"}}],
    "parameters": {"content_type": "np"},
}))
PY
```

**格式上有三个地方容易错,而且错了之后的报错完全指不到原因** —— 三种错法
拿到的都是同一句 `builtins.TypeError: float() argument must be a string or
a real number, not 'InferenceRequest'`:

- `data` 是**一维数组**(`[0.1, 0.1, ...]`),不是按 shape 嵌套的 `[[...]]`
- **顶层也要有** `"parameters": {"content_type": "np"}`,不只是 inputs 里那个
- 维度要和模型对上(这个 demo 是 20,不是 iris 的 4)


**要知道的两件事**([ADR-080](decisions/080-model-approval-and-rollback.md)):

- **上线单位是"注册表里被批准过的版本"**,不是"MinIO 里最新的目录"。存在
  更新的未批准 v2 时,部署仍然只用已批准的 v1 —— 这条守卫验证过。
- **回滚是 `./scripts/42-rollback-model.sh`**,不是手改 InferenceService ——
  手改的话注册表里"当前上线的是哪个版本"和实际就对不上了。
- **灰度做不了,而且脚本会显式拒绝 `canaryTrafficPercent`**。KServe 在这里
  是 RawDeployment 模式(刻意不装 Knative),那个字段会被收下但**完全不
  生效**,新版本直接拿 100% 流量。**留一个不生效的参数比没有更糟**,所以
  改成明确报错。

---

# 从模板起步,不要从空文件起步

```bash
platform-submit --list-templates
platform-submit --new batch-etl --into my-first-etl
```

| 模板 | 什么时候用 |
|---|---|
| `hello-job` | 第一次跑,验证连接都通了 |
| `batch-etl` | 从 Iceberg 读、算、写回 Iceberg —— 最常见的一类 |
| `train-model` | 取数 → 训练 → 记实验 → **注册**模型(注册 ≠ 上线) |
| `data-quality-check` | 业务规则断言,**不合格就让作业失败** |

**模板里已经写进去、你可能想不到要加的两件事**:

- `batch-etl` 写完会**回查一次**再退出。这个平台反复吃过"作业显示成功但
  数据没落盘"的亏(Iceberg 靠 commit 提交),模板不把这步留给你想起来加。
- `data-quality-check` 不通过时**非零退出**,不是打印警告。打印的警告没人
  看,作业挂了才有人管。**数据质量的价值在于阻断,不在于记录。**

---

# 遇到问题去哪查

| 想知道 | 去哪 |
|---|---|
| 某个组件现在到底在不在跑 | **门户**(现场探测,不是文档里的静态描述) |
| 部署/网络类的坑 | [`troubleshooting.md`](operations/troubleshooting.md),顶部症状索引 |
| 某个角色今天能做什么 | [`capability-matrix.md`](project/capability-matrix.md) |
| 某个设计为什么这么做 | [`docs/decisions/`](decisions/) |
| 架构全貌 | [`architecture.md`](architecture.md) |
