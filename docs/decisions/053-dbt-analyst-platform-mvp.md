# 053. 分析师开发平台 MVP:dbt build 在 Trino/Iceberg 上跑起来,先不接 Cosmos

- 状态: 已实现最小骨架,大部分环节已用真实工具/真实服务本地验证过;完整
  在集群里跑一遍受限于 Trino 当前不稳定,没有端到端测过。

## 背景

ADR-012 定了分析师开发平台的方向(dbt + Cosmos + OpenMetadata,经 MinIO
中转血缘文件),"还未实施,Phase 4 之后落地"。用户明确要求继续推进
("对呀,也继续做")。这次做一个真正能跑、有真实验证的最小骨架,不是
只写设计文档。

## 决策

### 范围收窄:这次不接 Cosmos,原因写清楚,不是漏做

ADR-012 的完整设计是 Cosmos 把 dbt 项目拆成 Airflow 里逐模型可见的任务。
Cosmos(`astronomer-cosmos`)要在 **DAG 解析阶段**被导入,也就是要装进
`scheduler`/`dagProcessor` 这两个组件自己的 Python 环境,不是像
KubernetesPodOperator 那样"运行时才在一个临时 pod 里装"。这个项目现在的
Airflow 部署是官方 chart,没有走自定义镜像也没有接 gitSync,唯一能加 Python
包的办法是官方 chart 提供的 `_PIP_ADDITIONAL_REQUIREMENTS` 环境变量——但
Airflow 官方文档明确写这个变量"仅适合快速试验,不建议用于生产",而且加了
之后每次这两个核心组件重启都要重新 pip install,拖慢启动、增加不稳定性。

这次没有为了赶 ADR-012 的完整设计而贸然改动 scheduler/dagProcessor 这两个
**所有** DAG 都依赖的核心组件的运行时环境。先做的是:一个用
`KubernetesPodOperator`(和 `feast_materialize.py`/
`seatunnel_device_events.py` 同一个已经验证过的模式)跑 `dbt build` 的
最小骨架,证明"dbt 模型真的能建在这个平台的 Iceberg 表上"这条核心链路走
得通。Cosmos 这层"逐模型可见/可重试"的体验留作有意的下一步,需要先决定
好要不要给 Airflow 换自定义镜像(这本身是个不小的决定,不该顺带做了)。

### demo 项目:真实存在的表,不是编的 schema

`apps/dbt-demo/project/` 建在 `iceberg.demo.orders` 之上——这张表的真实
列结构(`order_id`/`customer_name`/`region`/`product`/`amount`/
`order_date`)是从 `scripts/08-create-demo-data.sh` 的建表 DDL 里查出来的,
不是猜的。两个模型:`stg_orders`(轻量清洗层,view)、
`daily_order_totals`(按日期/地区聚合,table),用 `ref()` 连起来,足够
证明 DAG 依赖解析是对的,不需要更复杂的模型才能验证这条链路。

### 独立的 Trino 服务账号,独立的 K8s 命名空间

新增 `dbt_demo_service`(`ensure_trino_service_account`,和
`table_registration_service`/`superset_service` 同一套幂等创建逻辑,
ADR-021 的"各组件各自独立账号"原则)。dbt 的 KubernetesPodOperator 目标
pod 起在独立的 `dbt` 命名空间(和 `feast` 命名空间同一个处境——这类命名空间
不是哪个 ArgoCD Application 常驻管理的,是运行时才现起 pod,这次在
`scripts/00-generate-secrets.sh` 里补了 `ensure_ns dbt`,不再依赖"之前
手动建过"这种没有记录的隐藏前置条件,`feast` 命名空间当初就是这么悄悄
建出来的,这次不重复这个不透明的做法)。

### build 和上传 MinIO 合并成一个任务,不是两个

第一版写成了两个独立的 `KubernetesPodOperator`(`dbt_build` +
`upload_dbt_artifacts`),自己核对时发现一个真实设计错误:两个 pod 之间
没有共享文件系统,`target/manifest.json` 是第一个 pod 本地生成的,第二个
pod 里根本不存在,上传任务必然会因为文件找不到而失败。合并成一个任务,
`dbt build` 成功之后在同一个 pod 里直接用 `boto3` 上传,不引入共享 PVC
这类更重的机制。

### 不接 OpenMetadata 的 dbt 摄入连接器

这次只做到"manifest.json/catalog.json 上传到约定好的 MinIO 路径
(`s3://lakehouse/dbt-artifacts/platform_demo/`)"。配置 OpenMetadata 的
dbt connector 去真正读取并建立血缘,是下一步——ADR-014 记录过 OpenMetadata
读 MinIO/S3 兼容存储有已知的兼容性问题(`open-metadata/OpenMetadata#22843`),
接这条路径之前应该专门验证,不能想当然搬过来就好使。

## 涉及的文件

- 新增 `apps/dbt-demo/project/`(dbt 项目本身:`dbt_project.yml`/
  `profiles.yml`/`models/staging/`/`models/marts/`)
- 新增 `apps/dbt-demo/manifests/project-configmap.yaml` + 对应的
  `apps/definitions/dbt-demo.yaml`(ArgoCD Application)
- 新增 `apps/airflow/dags/dbt_demo.py` + 同步进
  `apps/airflow/manifests/dags-configmap.yaml`
- 改:`apps/definitions/airflow.yaml`(scheduler/dagProcessor/
  workers.kubernetes 三处都要挂 `dbt_demo.py` 这个新 DAG 文件,和
  `seatunnel_device_events.py`/`feast_materialize.py` 同样的 subPath
  挂法,绕开 Airflow 3.x 的 ConfigMap 目录递归遍历坑)
- 改:`scripts/00-generate-secrets.sh`(新增 `dbt_demo_service` 这个
  Trino 服务账号、`ensure_ns dbt`、复制 `minio-root`/
  `trino-service-account` 到 `dbt` 命名空间)

## 明确不做的

- 不接 Cosmos(见上面"决策"一节的详细理由)。
- 不接 OpenMetadata dbt 摄入连接器(见上面"决策"一节)。
- 不做分析师能碰的模型目录 vs 工程师维护的 DAG/Cosmos 配置这层职责分离
  规范——ADR-012 本来就说这个"现在还没细化",这次的 demo 项目本身也没有
  真实分析师在用,规范化留给真的有人要往这个项目里加模型的时候。

## 验证

### 已验证(2026-08-15,真实工具/真实网络请求,不是纸面设计)

- **dbt 工具链本身**:本地 `docker pull python:3.12-slim` +
  `pip install dbt-core dbt-trino`,确认能装、`dbt --version` 正常输出
  (dbt-core 1.12.2 + dbt-trino 1.10.3)。
- **profiles.yml 的认证方式**:一开始写的是 `method: none`,核实
  dbt-trino 的 `sample_profiles.yml`(装在本地 pip 包里,不是网上找的
  二手资料)才发现密码认证要用 `method: ldap`(即使 Trino 那边配的是
  file 类型的 PASSWORD 认证器,不是真的 LDAP,两者在协议层面都是发 HTTP
  Basic Auth——查了 `dbt/adapters/trino/connections.py` 源码确认这个
  映射关系,不是猜的),提前改对,不是等报错才发现。
- **dbt 项目结构**:真实项目(不是片段)用 `dbt parse --profiles-dir .`
  验证——`dbt_project.yml`/`profiles.yml`/`sources.yml`/两个模型的
  Jinja(`source()`/`ref()`)全部正确解析,0 错误。`dbt parse` 不需要
  真的连上 Trino,`dbt compile`/`dbt build` 会尝试连接,本机连不到集群内
  DNS(`trino.trino.svc.cluster.local` 解析不了),这条路径没法从本机
  测,只能在集群里测(见下面"还没验证的")。
- **DAG 里那条 shell 命令的拼接是对的**:把 `arguments=[...]` 里那个
  长字符串在本地原样拼出来,`docker run` 实测跑一遍——正确依次执行
  `pip install` → `cd /project` → `dbt build`,因为连不上 Trino 在
  `dbt build` 这一步失败(&&链正确短路,后面的 boto3 上传没有执行)——
  证明这条命令本身在语法/执行顺序上是对的,失败点完全符合预期(网络不通,
  不是命令写错)。
- **boto3 上传逻辑单独验证**:本地起一个真实的 `minio/minio` 容器
  (不是 mock),用和 DAG 里完全一样的 `boto3.client(...).upload_file(...)`
  代码上传两个测试文件,`list_objects_v2` 确认文件真的落到了
  `dbt-artifacts/platform_demo/` 这个路径下,大小正确——这部分完全独立
  于 Trino 是否健康,已经充分验证。
- **Secret/命名空间**:`scripts/00-generate-secrets.sh` 实际跑过,确认
  `dbt` 命名空间被创建、`trino-service-account`(含
  `password-dbt_demo_service` 这个 key)和 `minio-root` 都被正确复制
  进 `dbt` 命名空间。

### 还没验证的(诚实标注)

- **没有在真实集群里触发这个 DAG 跑一次完整流程**——Trino 今天反复
  CrashLoopBackOff(资源争抢,和这次改动无关,`kubectl top node` 实测
  内存常态 82-89%),没有在一个 Trino 健康的窗口里触发过。等 Trino
  稳定下来,应该跑一次真实的 `dbt_demo` DAG,确认:①`dbt build` 真的
  在 Trino 里跑出 `stg_orders`/`daily_order_totals` 这两个对象;②boto3
  上传那步在真实的集群网络环境(不是本机)下同样工作;③
  `ConfigMapVolumeSource` 用 `items` 做路径映射这种挂法本身在真实 K8s
  里没有踩到新坑(本地只验证了文件内容对不对,没有验证 K8s 卷映射机制
  本身)。
