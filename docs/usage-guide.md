# 开发使用指南

这份文档给**用这个平台干活的人**看,不是给平台运维看的——运维相关的
文档在 `docs/operations/`。这里只讲"我要干某件事该怎么用",不讲这些组件
是怎么部署起来的。

**按角色找你要看的部分**(角色定义见 [`docs/roles.md`](roles.md)):

| 你是 | 看哪几节 |
|---|---|
| 数据分析师 | 查数据 / 查表权限 / 看板 BI |
| 大数据开发 | 批处理作业 / 流式作业 / 作业排障 |
| 算法工程师 | Notebook / 提交训练任务 / 模型上线 |
| 数据治理 | 数据目录与血缘 / 建表 |
| 平台运维 | 这份不适合你,看 [`docs/operations/`](operations/) |

看这份文档之前先确认:你要用的组件现在是不是"常驻"状态。这台机器按需
park/unpark 组件是常态(见 `README.md`),真实状态以
`http://portal.local-lite.test`(平台门户,登录后能看到每个工具现场探测
的在线状态)为准,不要相信这份文档里任何"现在是不是在跑"的静态描述——
这条本身也是这个项目吃过亏才写下的规矩(见
`docs/operations/troubleshooting.md`)。

## 你的账号

所有工具共用同一个 Keycloak 账号(realm: `platform`),登录一次,后面
打开别的工具不用重新输密码。你的账号所在的组(`platform/iam/
groups.yaml`/`memberships.csv`)决定你能用哪些工具、审批链路怎么走——
不知道自己在哪个组,找 `platform-team` 组的人确认。

## 查数据:Trino

湖仓用 Iceberg + Trino,这是这个平台目前**唯一**的交互式 SQL 入口。

- Web UI:`http://trino.local-lite.test`,浏览器打开会走 Keycloak 单点
  登录,登录身份就是你的 Keycloak 账号。
- CLI / JDBC:Trino 原生支持 OIDC 交互式登录(命令行工具会弹出浏览器
  完成一次授权),也支持用户名+密码的 Basic Auth(给脚本/BI 工具这类
  没法弹浏览器的场景用,账号是单独发的服务账号,不是你自己的 Keycloak
  账号)。JDBC URL 形如 `jdbc:trino://trino.local-lite.test:443`,证书是
  自签的,客户端要么信任这张自签证书,要么按各工具自己的"跳过证书校验"
  选项配(生产环境上正式证书后这条不再需要,现在是 local-lite 阶段的
  临时处理)。
- 权限:能查哪些表,由**表访问分级审批**控制(见下一节)。没申请过的表
  查不到,不是 bug。
- 常见报错排查:先看 `docs/operations/troubleshooting.md`,这台机器的
  Trino 有已知的资源争抢问题(colima 13GB/6vCPU 的限制下,JVM 启动期
  偶尔会 CrashLoopBackOff,等它自己退避重启几次通常能自愈,不是配置
  错误)。

## 查表权限:权限申请门户

`http://permission-request.local-lite.test`——想查一张之前没权限的表,
在这里发起申请,不要去找人手动改 `platform/iam/table-access-grants.csv`
(那份文件现在是这个门户自动写的,手动改容易和门户的记录对不上)。

- 按表的"安全等级"(OpenMetadata 里打的 tag)走不同的审批链:等级越高,
  审批链越长(直属上级 → 上级的上级 → 表负责人 → 指定管理员,按等级
  逐级叠加,不是每次都要走全部四层)。
- 审批通过后的授权记录**默认 180 天后自动过期**,到期会被自动回收
  (ADR-050),届时需要重新申请,不是一次批准永久有效——这条是最近才
  补上的行为,如果你发现权限"突然没了",先看是不是过期,不要当成 bug
  报。
- 这份门户目前只做"决策与留痕",**不做真正的 Trino 查询拦截**——批准
  记录写进 grants.csv,但现在没有任何东西读这份数据去真的拦住你的 SQL
  查询(Trino 层面的细粒度强制执行还没做,是明确的后续工作,ADR-028)。
  换句话说:现在"申请-审批"这条流程本身是真实、被使用的,但"不批准就
  真的查不到"这个技术保障还没有——目前的访问边界靠 Trino 自己的角色/
  catalog 权限,不是这套 OA 流程本身在拦。

## 建表:建表注册工具

`http://table-registration.local-lite.test`——需要新建 Iceberg 表时用
这个,不要直接手写 DDL 连 Trino 建表。它会同时把表的负责人、安全等级回写
进 OpenMetadata,保证目录信息和实际的表同步创建,不会出现"表建了但目录
里没有、没人知道该找谁"的情况。

## 看板 / BI:Superset

`http://superset.local-lite.test`,数据源接的就是 Trino,建看板前先确认
自己对要用的表有权限(上面"查表权限"那节)。

## 交互式开发 / Notebook

JupyterHub 已部署(2026-08-19 un-park 并真实 SSO 登录验证过,见
`apps/definitions/jupyterhub.yaml`),按组分配访问权限
(`docs/decisions/025-jupyterhub-sso.md`)。

**"打开 notebook 自动连好 Trino/MLflow"这个曾经记录在案的缺口已经补上**
(见 [ADR-058](decisions/058-lightweight-developer-experience.md)):
notebook 用的是平台统一镜像,已经带了 `platform_sdk`,不用自己装
Trino/MLflow client、不用自己填连接串:

```python
from platform_sdk import query, mlflow_setup

df = query("select * from iceberg.demo.orders limit 10")

mlflow = mlflow_setup("my-experiment")
with mlflow.start_run():
    mlflow.log_metric("acc", 0.9)
```

Trino 的账号密码仍然要通过环境变量提供(`PLATFORM_TRINO_USER`/
`PLATFORM_TRINO_PASSWORD`)——**这条明确没做**:notebook 里怎么免密拿到
"当前登录用户"对应的 Trino 凭据,还是每个用户自己在 profile 里配一份
服务账号凭据,这件事没有定论,现阶段用的是显式环境变量。

要把本地脚本提交到集群跑(训练任务这类),用 `submit_job()`,或者写一份
`job.yaml` 配 `platform-submit job.yaml`——参考 `examples/hello-job/`,
细节见 ADR-058 和 `platform-sdk/README.md`。**已知限制**:直接从
notebook pod 里调 `submit_job()` 目前连不上 K8s API server(NetworkPolicy
问题,`docs/BACKLOG.md` 2.6),要从终端/CI 提交,不是从 notebook 里。

如果用 Claude Code 之类的 AI 编程工具在这个仓库里干活,`.claude/skills/`
下有 `query-data`/`submit-job`/`debug-job` 三个 skill,分别覆盖"怎么
查数据"/"怎么提交作业"/"作业失败了怎么查"——写完还没经过真实使用验证,
触发不准或者内容不够用的话直接回来改,不用假设它们已经调好了。

## 批处理作业:Spark

跑一个读写 Iceberg 表的 Spark 作业,不用自己写 SparkApplication YAML:

```bash
./scripts/13-run-spark-iceberg-demo.sh
```

这个脚本本身就是一份可以照抄的样例——它提交一个真实的 SparkApplication,
等作业跑完,然后**用 Trino 回查结果表确认数据真的落盘了**,不是只看作业
状态。你自己的作业照着 `apps/spark-iceberg-demo/manifests/` 改。

要点:
- **用 `apps/spark-iceberg-image/` 那个镜像**,别用官方 `apache/spark`。
  官方镜像不带 Iceberg / S3A 的 jar,运行时去 Maven 现拉在这个网络环境下
  会卡死(ADR-061 记过实测数据)。
- 作业历史看 Spark History Server。**如果列表是空的**,先确认作业开了
  `spark.eventLog.enabled` —— 这个平台上"History Server 是空的"最常见的
  原因不是它坏了,是作业压根没写 event log。

## 流式作业:Flink

```bash
./scripts/31-run-flink-streaming-demo.sh
```

现成的链路是 Kafka topic → Flink → Iceberg(明细表 + 1 分钟滚动窗口聚合表),
定义在 `apps/flink-streaming-demo/`。改成你自己的作业时,有几个坑是这个
平台上实测踩过的(完整清单见 ADR-062):

- **`value` 是 Flink SQL 的保留字**,字段名撞上保留字要加反引号。同一套
  schema 在 Spark/SeaTunnel 那边没问题,换到 Flink 就报解析错误。
- **Iceberg sink 靠 checkpoint 提交**。没到 checkpoint 间隔,数据不会出现
  在表里——查不到数据先看是不是还没到点,不是作业坏了。
- **时间字段格式要和 Flink 的 `json.timestamp-format.standard` 对上**,
  而且**不要开 `json.ignore-parse-errors`**:开着的话解析失败会静默变成
  null,然后在两个算子之后以完全不相干的报错炸出来。

## 提交训练任务 / 跑在集群上

在 notebook 里一行提交,不用写 Argo YAML:

```python
from platform_sdk import submit_job, run_workflow_template
submit_job("train.py")                       # 把脚本丢到集群上跑
run_workflow_template("train-demo-model")    # 触发已部署的工作流模板
```

多步骤流水线(特征物化 → 训练 → 模型门禁)的样例在
`apps/argo-workflows-training-image/manifests/workflow-template-ml-pipeline.yaml`。

## 数据目录与血缘:OpenMetadata

Trino 里的表**会被自动采集进目录**(每 6 小时一次),不需要手动登记——
打开 OpenMetadata 直接搜表名就行,能看到字段、类型和血缘。

如果你新建的表在目录里搜不到,先确认是不是还没到下一次采集;要立刻同步
可以手动触发一次(见 `scripts/29-configure-openmetadata-trino-ingestion.sh`
的说明)。

## 作业排障:先看哪一层

这个平台上排作业问题有个反复出现的规律:**报错出现的位置和真正的根因
经常隔着一到两层**。实测过的例子:

- Flink 的 checkpoint 一直失败 → 真正原因是 TaskManager 因为一个不存在的
  ConfigMap 起不来,算子根本没调度上去。
- OpenMetadata 采集任务显示 `Running 0/1` 看着正常 → 其实是被命名空间配额
  拦住,一个 Pod 都没建出来,只有 `kubectl describe job` 才看得到。

所以顺序建议是:**先看 Pod 层(`kubectl get pods` / `describe`),再看
应用日志**,不要一上来就扎进应用日志里找。按症状检索的完整 Runbook 在
[`docs/operations/troubleshooting.md`](operations/troubleshooting.md),
顶部有症状索引。

## 遇到问题去哪查

- 部署/网络类的坑:`docs/operations/troubleshooting.md`
- 想知道某个组件现在到底有没有在跑:`http://portal.local-lite.test`
  (现场探测,不是文档里的静态描述)
- 这个平台的架构全貌和每个组件的定位:`docs/architecture.md`
- 具体某个设计为什么这么做:`docs/decisions/`(按编号找对应的 ADR)
