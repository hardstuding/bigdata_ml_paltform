# 052. SeaTunnel 数据管道的表级血缘:从 job 配置结构提取,推 OpenMetadata

- 状态: 已实现,已部署到真实集群(DAG 确认能正常解析、无 import 错误);
  核心血缘 API 机制已用真实 OpenMetadata 实例验证;完整 DAG 触发跑一遍
  (受限于 SeaTunnel 当前是 park 状态)没有测。

## 背景

ADR-011 早就定了方向(表级血缘直接读 SeaTunnel job 配置结构提取,不解析
SQL,写入 OpenMetadata 官方 `PUT /api/v1/lineage` API),一直没有实现。
这次把 `apps/airflow/dags/seatunnel_device_events.py`(ADR-037 验证过的
"Airflow 调度 SeaTunnel 写 Iceberg"这条真实存在的管道)补上血缘推送这一步。

## 决策

### 用 Pipeline -> Table 边,不是 Table -> Table

这个 DAG 的 source 是 `FakeSource`(SeaTunnel 内置的合成数据生成器,不是
真实表),没有真实的"上游表"可以连。OpenMetadata 的血缘模型本来就支持
`Pipeline` 作为节点(不是只能 Table 连 Table)——用
`Pipeline(seatunnel_device_events) -> Table(trino.iceberg.demo.
device_events)` 这条边,准确表达"这张表是这个 Airflow DAG 产出的",不是
硬凑一个不存在的上游表。以后如果换成真实 Jdbc/Hive 类型的 source(读
真实上游表),`extract_sink_table_fqns()` 这个函数需要对应扩展出
`extract_source_table_fqns()`,这次没有实现(现在的 source 是合成数据,
没有真实场景可以验证这条路径,不假装做了)。

### FQN 拼接:固定用 `trino.iceberg.<namespace>.<table>`

SeaTunnel job 配置里 sink 块自己的 `catalog_name` 字段(这个 demo 里是
`"seatunnel"`)是它自己连 Hive Metastore 用的内部命名,和 OpenMetadata
里这张表实际注册用的服务名无关——这个平台所有 Iceberg 表都是通过同一个
`trino` database service 注册进 OpenMetadata 的(和
`apps/table-registration-app/src/app.py` 里 `databaseSchema: f"trino.
{catalog}.{schema}"` 是同一个约定)。`extract_sink_table_fqns()` 里硬编码
了这条映射规则,不是从 SeaTunnel 配置里能自动推出来的,写了清楚的注释
说明为什么。

### 幂等创建 Pipeline/PipelineService,凭据未配置就静默跳过

和这个项目其他自建组件同一套模式:`openmetadata_token`(Airflow
Variable,由 `scripts/14-configure-airflow-seatunnel-variable.sh` 从
`table-registration-app-openmetadata` 这个已有 bot token 复用写入,不新建
凭据面)没配置就跳过整个 `push_lineage` 任务,不让数据管道本身因为血缘
这个附加能力失败。`PipelineService`/`Pipeline` 实体先查再建(GET 
`/name/{fqn}` 404 才 POST),重复跑这个 DAG 不会重复创建。目标表如果还没
在 OpenMetadata 里注册(比如这次实测时 `device_events` 确实还没注册),
只记日志跳过这条边,不报错——这是"目录信息滞后于实际数据"的正常过渡态。

## 涉及的文件

- 改:`apps/airflow/dags/seatunnel_device_events.py`(新增
  `extract_sink_table_fqns`/`_om_request`/`_ensure_pipeline_service`/
  `_ensure_pipeline`/`_resolve_table_id`/`push_lineage` 任务,接进 DAG
  末尾 `wait_for_completion() >> push_lineage()`)+ 同步的
  `apps/airflow/manifests/dags-configmap.yaml`
- 改:`scripts/14-configure-airflow-seatunnel-variable.sh`(补
  `openmetadata_token` 这个 Variable,复用已有 bot token)

## 明确不做的

- 不做列级血缘——ADR-011 原本就把列级血缘留给用到 SQL transform 的场景
  (sqlglot 解析),这个 demo 管道没有 transform 步骤,不需要。
- 不做 source 侧血缘(见上面"决策"一节)——现在的 source 是合成数据,
  没有真实上游表。
- 不新建血缘专用的 OpenMetadata bot/凭据——复用
  `table-registration-app-openmetadata` 这一个已有 Admin 角色 bot,减少
  凭据面。

## 验证

### 已验证(2026-08-15,真实 OpenMetadata 实例,不是 mock)

- **端到端手动验证整个 API 链路**:直接用真实 bot token 调用
  `POST /api/v1/services/pipelineServices`(建 `airflow-platform` 服务)
  → `POST /api/v1/pipelines`(建 `seatunnel_device_events` Pipeline 实体)
  → `PUT /api/v1/lineage`(推 Pipeline -> Table 边,Table 用这个集群里
  已经真实注册的 `trino.iceberg.demo.orders_catalog_demo4`)→
  `GET /api/v1/lineage/table/{id}` 确认能查回这条边,`upstreamEdges` 里
  能看到刚推的 Pipeline 节点。这条测试路径用的是已存在的真实表(不是
  `device_events`,因为那张表现在 Trino 不稳定导致没法通过
  table-registration-app 注册,见下面"还没验证的"),但走的是和代码里
  完全一样的 API 调用序列,证明机制本身是对的。
- **DAG 里实际的函数逐个单测**:把 `extract_sink_table_fqns`/
  `_om_request`/`_ensure_pipeline_service`/`_ensure_pipeline`/
  `_resolve_table_id` 原样复制进一个独立脚本,在集群里一个真实 pod 里跑,
  用真实 token 打真实 OpenMetadata:
  - `extract_sink_table_fqns()` 从样例 job 配置正确提取出
    `trino.iceberg.demo.device_events`。
  - `_ensure_pipeline_service`/`_ensure_pipeline` 正确识别出上一步已经
    手动建过的实体(返回同一个 id,没有重复创建)——幂等性是真的验证过,
    不是猜的。
  - `_resolve_table_id` 对真实存在的表返回正确 id,对不存在的
    `device_events` 正确返回 `None`(不抛异常)。
- ConfigMap 同步:`apps/airflow/manifests/dags-configmap.yaml` 和
  `apps/airflow/dags/*.py` 两个 DAG 文件的内容逐字节比对一致。

### 也已经部署到真实集群并确认 DAG 能正常解析(2026-08-15,补充)

commit → push → 发现这份 ConfigMap 实际被 `airflow-db-init` 这个
Application 管理(不是看起来更顺理成章的 `airflow` 那个——这是这次一个
真实的认知纠正,靠 `kubectl get configmap ... -o jsonpath=
'{.metadata.annotations}'` 查 `tracking-id` 才确认的,不是猜的)→ sync →
重启 `airflow-dag-processor`/`airflow-scheduler`(ConfigMap 走 subPath
挂载,这个项目已知有更新延迟,重启是标准应对方式)。

第一次部署后 `airflow dags list-import-errors` 真的抓到一个 bug(见上面
"涉及的文件"之后新加的一次 commit):`push_lineage` 任务的
`@task(executor_config=POD_OVERRIDE)` 装饰器引用了 `POD_OVERRIDE`,但我
把这块代码插在了 `POD_OVERRIDE` 真正定义之前,`NameError`。修复方式是把
整块血缘代码挪到 `POD_OVERRIDE` 定义之后、`with DAG(...)` 之前——这是
`list-import-errors` 实测出来的,不是代码审查看出来的。修完之后:

- `airflow dags list-import-errors` 输出 `No data found`,两个 DAG
  (`seatunnel_device_events`/`feast_materialize`)都没有解析错误。
- `airflow dags list` 确认 `seatunnel_device_events` 正确注册进了
  Airflow 的元数据库。
- `airflow tasks list seatunnel_device_events` 报"Dag 找不到"——一开始
  怀疑是我的改动引入的新问题,交叉验证:对完全没改过的 `feast_materialize`
  跑同一个命令,**同样报错**,说明这是 `airflow tasks list` 这个 CLI 子
  命令本身在这套环境下的既有行为(它走的 DAG bagging 逻辑和 `dags list`/
  `list-import-errors` 不是同一条代码路径),和这次改动无关,不是我引入
  的回归。

### 还没验证的(诚实标注)

- **没有真的触发这个 DAG 跑一遍完整流程**——SeaTunnel 现在是 park 状态
  (`kubectl get pods -n seatunnel` 没有任何 pod),没法提交真实 job,
  `push_lineage` 任务本身也没有真的在一次 DAG Run 里被执行过(确认了它
  能被正确解析、注册,但没确认它被触发执行时的实际行为)。等 SeaTunnel
  unpark、这个 DAG 真的跑一次之后,应该确认 `device_events` 表本身有没有
  被别的地方注册进 OpenMetadata(如果一直没有,这条血缘边会一直被跳过,
  需要考虑要不要让 `push_lineage` 顺手把表也注册了,这次没有做这个决定,
  留到真的跑起来再看)。
