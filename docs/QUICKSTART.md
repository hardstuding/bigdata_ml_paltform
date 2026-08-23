# 快速上手:半小时走完一条完整链路

这份文档面向**第一次接触这个平台的人**,目标只有一个:让你在半小时内
亲手跑通一条"数据进来 → 能查 → 能看 → 能训练 → 能上线"的完整链路,
对这套东西是什么、各个组件在链路的哪个位置**有一个整体的感觉**。

它不是参考手册。每个组件怎么用,官方文档写得比我们详细得多,下面各节都
直接给了链接——**这里只负责带路,不重复造文档**。深入使用看
[`docs/usage-guide.md`](usage-guide.md),运维看
[`docs/operations/`](operations/),每个技术选择为什么这么定看
[`docs/decisions/`](decisions/)。

前提:平台已经部署好了(还没部署看 [`README.md`](../README.md) 的
"从零拉起整套服务",一条命令是 `./scripts/bootstrap-all.sh`)。

---

## 先有个整体概念

```
                   ┌──────────── Keycloak(SSO,所有工具共用一个账号)
                   │
  数据进来          数据存下来           数据查出来          数据用起来
  ─────────        ──────────          ──────────         ──────────
  Kafka  ──┐                          ┌── Trino ──┬── Superset(BI 看板)
           ├── Flink ──┐              │           └── OpenMetadata(找表/血缘)
  数据库   ─┴─ SeaTunnel┼─► Iceberg ───┤
  /文件    ── Spark ────┘  (MinIO 对象   └── JupyterHub(notebook)
                            存储 +          │
  Airflow(定时调度这一切)   Hive Metastore)  └── MLflow ── KServe(模型上线)
```

一句话概括各层:**存储只有一份**(Iceberg 表存在 MinIO 上,Hive Metastore
记元数据),上面所有引擎(Trino / Spark / Flink)读写的是**同一批表**——
这是整套设计的核心,也是它和"一个组件一个数据副本"那种堆砌式平台最大的
区别。ArgoCD 管所有组件的部署,Keycloak 管所有工具的登录。

完整的架构说明和取舍理由在 [`docs/architecture.md`](architecture.md)。

---

## 第 1 步:登录门户,确认自己能进去

打开平台门户(local-lite 是 `http://portal.local-lite.test`),用你的
Keycloak 账号登录。门户列出所有工具的入口和**现场探测的在线状态**——
这个平台上组件按需启停是常态,任何文档里写的"现在跑着什么"都不可信,
以门户实时探测为准。

登录一次,后面打开 Superset / JupyterHub / MLflow 都不用再输密码。

> 进不去先看 [`docs/operations/troubleshooting.md`](operations/troubleshooting.md)
> 顶部的症状索引,不要凭猜测调配置。

## 第 2 步:造一批 demo 数据

```bash
./scripts/08-create-demo-data.sh
```

它建一张 Iceberg 表 `iceberg.demo.orders` 并灌进示例数据。**这一步跑通
意味着**:对象存储、Hive Metastore、Trino 三者之间的连接是通的——平台
最底下那层地基是好的。

## 第 3 步:查一下(Trino)

```sql
select * from iceberg.demo.orders limit 10;
```

从门户进 Trino,或者在 notebook 里 `platform_sdk.query(...)`。

Trino 是这个平台**唯一的统一查询入口**:一个 SQL 接口,底下可以是 Iceberg
表、也可以是外部数据库。它上面挂了 OPA 做权限(哪个组能看哪张表、哪些
字段要脱敏),所以"给某人开一张表的权限"是改 `platform/iam/` 里的
配置 + `git push`,不是登录到某个系统里点几下。

> Trino SQL 语法:https://trino.io/docs/current/

## 第 4 步:做个看板(Superset)

从门户进 Superset,数据源已经配好了(`scripts/06`),直接建 chart。

> Superset 文档:https://superset.apache.org/docs/intro

## 第 5 步:在 notebook 里干活(JupyterHub)

从门户进 JupyterHub。镜像里已经预装了 `platform_sdk`,**不用自己填任何
连接串**:

```python
from platform_sdk import query, mlflow_setup, submit_job

df = query("select * from iceberg.demo.orders limit 10")   # 连 Trino
mlflow = mlflow_setup("my-experiment")                      # 连 MLflow
wf = submit_job("my-job", "train.py")                       # 丢到集群上跑
```

`submit_job` 把你的本地脚本变成一个集群作业,你不用写 Kubernetes YAML,
也不用懂 Argo Workflows。它跑在**和 notebook 完全相同的镜像**里——
"本地能跑、上集群就挂"这类问题从设计上就避掉了。

> 设计取舍见 [ADR-058](decisions/058-lightweight-developer-experience.md)。

## 第 6 步:训练一个模型并上线

```bash
./scripts/09-train-demo-model.sh          # 训练,产物记进 MLflow
./scripts/11-deploy-demo-inference-service.sh   # 从 MLflow 拉模型,用 KServe 起在线推理服务
```

**这一步跑通意味着**:算法工程师从"在 notebook 里试出一个模型"到"这个
模型有一个能被调用的 HTTP 接口",全程不需要找运维。

> MLflow:https://mlflow.org/docs/latest/ ·
> KServe:https://kserve.github.io/website/

## 第 7 步(可选):批处理和流处理各跑一个

```bash
./scripts/13-run-spark-iceberg-demo.sh    # Spark 批作业读写 Iceberg
./scripts/31-run-flink-streaming-demo.sh  # Kafka → Flink → Iceberg 实时链路
```

两个脚本都**自己回查结果表确认数据真的落盘了**,不是只看作业状态显示
Success 就算完——这是这个平台反复吃过亏之后形成的习惯(Job Complete
不等于业务逻辑跑对)。它们同时也是你写自己作业时可以照抄的样例。

> Spark:https://spark.apache.org/docs/latest/ ·
> Flink:https://nightlies.apache.org/flink/flink-docs-stable/ ·
> Iceberg:https://iceberg.apache.org/docs/latest/

## 第 8 步:让它每天自己跑(Airflow)

前面都是手动触发。真实场景里这些步骤要定时跑,这是 Airflow 的活,
DAG 样例在 `apps/airflow/dags/`。

> Airflow:https://airflow.apache.org/docs/

---

## 跑完之后

你已经用过了这个平台的主干。接下来按你的角色看
[`docs/usage-guide.md`](usage-guide.md)(开头有角色→章节对照表),
它讲的是"你自己的活怎么在这上面干",以及**这个平台特有的那些坑**——
那部分官方文档不会有,因为它们是这套组合特有的。

想知道"这个平台今天到底能支撑哪些岗位独立干活",看
[`docs/roles.md`](roles.md):它按能力而不是按组件列,是这个项目
"做到哪了"的唯一权威入口。
