# 037. 数据工程主线端到端验证:SeaTunnel → Iceberg → Airflow

- 状态: 已采纳,已验证(2026-08-12/13):SeaTunnel → Iceberg → Airflow 调度 → Superset 看板端到端跑通

## 背景

架构复盘时明确指出:`docs/architecture.md` Phase 2 的退出标准是"SeaTunnel →
Iceberg → Airflow 调度 → Superset 看板端到端跑通"。SeaTunnel/Kafka/Airflow
之前各自单独验证过健康(Kafka 建 topic 收发消息、Airflow 4 个组件 health
check 全绿、SeaTunnel REST 健康检查返回 ACTIVE),但从没有真的一起跑过,也
没有任何 SeaTunnel job 或 Airflow DAG 把它们接起来写数据到 Iceberg——只有
裸的基础设施,没有真正的管道(ADR-036 也提到过这一点)。Kafka 不在退出标准
的文字表述里,这次先不拉起,省资源。

## 决策

### 数据源用 FakeSource,不接真实外部系统

这次要验证的是"链路通不通",不是接一个真实业务数据源——SeaTunnel 2.3.13
内置的 `connector-fake` 生成合成数据,不需要任何外部依赖,和
`kserve-demo`/Spark demo 的"最小可验证"思路一致。真要接 CDC/数据库同步,
连接器已经在镜像里(`connector-cdc-*`/`connector-jdbc`),留到有真实源
系统接入需求时再选。

### SeaTunnel 写的是和 Trino/Spark 共用的同一个 `demo` 命名空间

一开始尝试用一个新命名空间(`seatunnel_demo`),实测发现 SeaTunnel 的
Iceberg sink 只会自动建表、不会自动建 namespace(`schema_save_mode` 那个
配置指的是表 schema 演进,不是 metastore 命名空间),报
`NoSuchNamespaceException`。改用已经由 `scripts/08-create-demo-data.sh`
建好的 `demo` 命名空间——这也更能体现"共享元数据"这个核心结论:同一个
Hive Metastore 下,SeaTunnel/Spark/Trino 三个引擎都在往同一个数据库写表。

### 调度用 SeaTunnel 自带的 REST API,不用 provider

`apache-airflow-providers-*` 里没有 SeaTunnel 的 provider,SeaTunnel 社区
也没有发布独立的 Airflow provider。改用 Zeta 引擎自带的 REST API
(`POST /hazelcast/rest/maps/submit-job`),DAG 里用标准库 `urllib` 直接调,
不额外装 `requests`,减少依赖面。

### MinIO 凭据用 Airflow Variable,不是 Secret 挂载

这个凭据只在 DAG 任务执行、拼 SeaTunnel job 请求体那一刻用得到,不是 pod
启动时就要用(区别于 `apps/spark-iceberg-demo` 那种 driver/executor 启动
就要注入的场景)。用 `scripts/14-configure-airflow-seatunnel-variable.sh`
通过 `airflow variables set`(走 kubectl exec,和
`scripts/05-configure-airflow.sh` 建管理员账号是同一个思路)写进 Airflow
自己的元数据库(Fernet key 加密),DAG 代码里 `Variable.get()` 读。

### DAG 文件用 ConfigMap 挂载,不接 gitSync

chart 自带 `dags.gitSync` 需要一个独立可访问的 git 仓库/凭据配置,这个
仓库统一的取舍是"没有额外基础设施依赖时优先 ConfigMap 挂载"(和
iam-sync、permission-request-app 是同一个模式)。DAG 要同时挂进
`scheduler` 和 `dagProcessor`——Airflow 3.x 把 DAG 解析从 scheduler 里拆
成了独立组件,两边都要能看到 `dags` 目录。

## 排查过程中发现的真实 bug

跑通之前踩了两个独立的真实 bug,都是"配置从文档片段拼的、从没真正跑过"
类型:

### bug 1:Airflow chart 自带的 migrateDatabaseJob 和 ArgoCD 死锁

chart 自带的 `migrateDatabaseJob` 是 Helm 原生 `post-install,post-upgrade`
hook。ArgoCD 要等主 Sync 阶段所有资源 Healthy 才会触发 PostSync,但
Deployment 的 `wait-for-airflow-migrations` initContainer 又要等这个
migrate job 跑完才能启动——死锁,`kubectl get jobs -n airflow` 里这个 Job
从始至终没被创建过(ArgoCD 的 syncResult 里也只有它的 ServiceAccount)。
这个仓库已经在 `apps/airflow/manifests/create-db-job.yaml` 里明确记录过
"不要给 Job 加 hook 注解"的教训,这次是 chart 自带的 hook 触发了同一类
问题。关掉 `migrateDatabaseJob.enabled`,改用手写的普通 Job
(`apps/airflow/manifests/migrate-db-job.yaml`,不带任何 hook 注解)。
`wait-for-airflow-migrations` 这个 initContainer 是独立开关
(`waitForMigrations.enabled`),不受影响,照常会等 migration 跑完。

### bug 2:SeaTunnel REST API 没开 DATA 这个 endpoint group

`apps/seatunnel/manifests/configmap.yaml` 里的 `hazelcast.yaml` 是照官方
文档片段拼的(文档没给完整配置示例,见该文件顶部说明),一开始只开了
`CLUSTER_WRITE` 这个 REST endpoint group,真正提交作业时 pod 日志报
`REST endpoint group is not enabled - DATA`——作业提交/查询接口
(`/hazelcast/rest/maps/submit-job` 等)实际走的是 `DATA` 组。补上之后
REST API 才真正可用,这个 API 之前只验证过集群健康(GET /overview 之类
不需要 DATA 组的接口),从没真的提交过作业。

## 验证记录

### DAG 从裸逻辑到真正跑通,又踩了 5 个真实 bug

先手动调用 SeaTunnel REST API 验证了 job 配置本身没问题(`jobStatus:
FINISHED`,MinIO 里确认了真实的 parquet + Iceberg metadata),把同样的逻辑
包成 Airflow DAG 之后,又是一路踩坑才真正跑通(和 ADR-036 的 Spark+Iceberg
demo 是同一种"每一层都要真跑一遍才会暴露"的情况):

1. **KubernetesExecutor 的任务 pod 用的是单独一份 pod 模板**:一开始只把
   DAG 文件挂进了 `scheduler`/`dagProcessor`,任务 pod(`workers.kubernetes`
   这份模板)里没挂,报 `Dag not found during start up`。用
   `airflow tasks test` 在 scheduler pod 里直接跑过 DAG 逻辑本身完全正常,
   问题只在任务 pod 缺这个挂载——这个排查步骤本身也值得记一下:遇到"逻辑
   看起来对但真实调度失败"时,`tasks test` 能快速把"代码逻辑"和"调度/
   pod 环境"这两类问题分开。
2. **pod_override 必须是真正的 `kubernetes.client.models.V1Pod` 对象**:
   第一次直接传了个普通 dict 想给任务 pod 加 resources request(为了下面
   第 3 点的 OOM 问题),KubernetesExecutor 内部 `PodGenerator.from_obj` 会
   做 `isinstance(k8s.V1Pod)` 检查,不通过就整个 `executor_config` 判定
   invalid,任务直接 fail,pod 都不会起。
3. **这台机器当时确实在被反复 OOM**:第一次真实调度时任务 pod 被 SIGKILL
   (exit_code=-9)。查 `journalctl -k` 确认节点当时在被反复触发内核 OOM
   killer(连 `argocd-application-controller` 都被连带杀了几次,尽管它自己
   配了 2Gi 限制——是节点级压力,不是它自己碰到 cgroup 上限)。
   KubernetesExecutor 起的任务 pod 默认不带任何 resources(BestEffort
   QoS),天然是 OOM killer 第一批目标,加了最小 resources request/limit。
4. **`context['ts_nodash']` 在手动触发的 DAG 里不存在**:这个 DAG 是
   `schedule=None`,没有真正的 `logical_date`/`data_interval`。查了
   Airflow 3.x 的 context 构建源码(`execution_time/task_runner.py`)确认
   `ts_nodash` 这类从 `logical_date` 派生的键在这种情况下压根不会塞进
   context,只有 `run_id` 是无条件总在的——改用 `run_id` 拼作业名。
5. **第 3 点加的内存 limit(256Mi)本身也不够**:修完第 4 点之后任务 pod
   还是被 SIGKILL,这次没有任何 Python 异常堆栈(容器自己的 cgroup 内存
   上限被打到,不是节点级 OOM)。Airflow 3.x 任务运行时本身(SDK
   supervisor 进程 + 解析整个 DAG 文件)占用不小,调到 512Mi。

排查过程中还确认了一个和这个仓库其他地方一致的环境特性:ArgoCD 的
Application 显示 `Synced`/`Succeeded` 不代表活的资源已经真的更新(好几次
"改完代码重新跑还是报同一个旧 bug",查下去发现是 ConfigMap 的 subPath
挂载有 kubelet 本地缓存延迟,或者是 sync 状态和实际子资源 spec 不同步)。

### 最终验证结果

`seatunnel_device_events` DAG 触发,两个任务(`submit_seatunnel_job`、
`wait_for_completion`)都是 `success`。SeaTunnel 侧确认
`jobName=device-events-manual__2026-08-12T1551086004610000`、
`jobStatus: FINISHED`;MinIO 里确认这次运行对应时间戳(15:51:19)的新
parquet 文件真实落到了 `demo.device_events` 表下(和 ADR-036 那次手动
提交 Spark 作业写的表在同一个 `demo` 数据库,进一步印证共享元数据这个
结论)。

调试期间为了能抓到失败任务的日志,给 Airflow 加了
`AIRFLOW__KUBERNETES_EXECUTOR__DELETE_WORKER_PODS_ON_FAILURE=False`(只保留
失败的任务 pod,成功的照常自动清理)——链路跑通后保留这个设置,不只是
调试期间的临时开关,因为这类"最后一步失败、pod 秒删、日志抓不到"的情况
在这个环境里反复出现,值得作为长期配置。

### 最后一步:Superset 看板

DAG 验证通过之后,SeaTunnel 本身按惯例收回(`environments/cloud-full/
pending-definitions/`)释放资源,拉起 Trino + Superset 补上退出标准里最后
一段。`scripts/15-create-device-events-dashboard.sh` 在 `demo.device_events`
表上建了 Dataset + Chart + Dashboard,走 Superset 真实的查询执行链路验证
(`QueryContextFactory`,不是只存了个连接串),返回 60 行(三次成功写入各
20 行累加,`data_save_mode` 默认是 `APPEND_DATA`,数字对得上)。图表故意用
Table 视图,不用按 `event_type`/`device_id` 分组的柱状图——这两个字段是
FakeSource 默认随机字符串模式,没有真实分类语义,分组图没有意义。

验证完之后 Trino/Superset 也按惯例收回,只有 Hive Metastore/MinIO 这两个
存储层组件继续跑——`demo.device_events` 表和数据留在 Iceberg 里,不受
影响。Airflow DAG 本身也已经确认能正常调度/触发,收回不影响这个结论,
下次要跑数据工程相关验证时再按需拉起。

至此,`docs/architecture.md` Phase 2 退出标准("SeaTunnel → Iceberg →
Airflow 调度 → Superset 看板端到端跑通")完整验证通过。

## 后果

- 只验证了 SeaTunnel → Iceberg 这条链路加上 Airflow 的调度触发,Superset
  看板这一步等最后临时拉起 Trino/Superset 时一起做。
- Kafka 不在这次验证范围内——退出标准的文字表述本身没有它,且这台机器
  资源有限,优先级更低。真要验证"消息队列"这一段(比如 Kafka → SeaTunnel
  → Iceberg),需要另外设计。
- 数据量很小(20 行合成数据),没有测试真实数据量级下 SeaTunnel 的
  checkpoint/容错行为(`apps/seatunnel/manifests/configmap.yaml` 里提到
  checkpoint 的 S3 存储配置还没写)。
- DAG 目前是手动触发(`schedule=None`),没有配置成定时任务——这是验证
  链路用的 demo,不是要立即投产的常驻调度。
