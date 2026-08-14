# 042. Feast 特征存储:技术选型与部署方式

- 状态: 已采纳,已验证

## 背景

`docs/architecture.md` 路线图 Phase 3.5("AI 闭环验证")一直空着,组件表里
Feast 之前标成"仅 prod"(local-lite/cloud-full 都是 `—`)——排表时的疏漏,
资源权重本来就标的"中",本机 4 vCPU/11GB 完全跑得动,没有理由等到 prod
才做。这次把 Feast 落地,顺带把这处表格错误改掉。

## 决策

### Registry:文件式,存 MinIO(S3 兼容)

不用 Feast 支持的 SQL registry(接 Postgres)。这个项目里 Postgres 是共享
实例,给 Feast 开一个新库是可以做但没必要的额外耦合——文件式 registry 直接
写 `s3://lakehouse/feast/registry.pb`,复用平台已经在用的对象存储,不引入
新依赖,和 Trino/Spark 的 warehouse 是同一个 MinIO 实例。

### 离线存储:Spark,不是 Trino 的 contrib 插件

Feast 官方文档对 Iceberg 表格式的完整支持是以 Spark data source 为主;
Trino offline store(`feast-trino`,原 Shopify 维护,后来并入
feast-dev/feast 仓库的 contrib 目录)成熟度更低、对 Iceberg 的支持也更弱。
这个平台已经有 Spark Operator 且 ADR-036 验证过 Spark 读写 Iceberg,复用
这条已验证链路,不用两条腿各走一半。

### 在线存储:Redis,但不用 Feast 官方 chart 自带的 redis 子 chart

官方 `feast-dev/feast` Helm chart(`infra/charts/feast`)本身是官方仓库
自带、持续维护的,符合本项目"只用官方支持的部署方式"的门槛(和
[ADR-028](028-iam-org-model.md)/[ADR-030](030-pluggable-external-infrastructure.md)
否决 Ranger 是同一条标准)。但实测 `requirements.yaml` 发现它内嵌的 redis
子 chart 依赖 `https://charts.helm.sh/stable`——这个仓库 2020-11 就被 Helm
官方弃用,index.yaml 虽然还能访问(冻结快照),里面的 `redis` chart 停在
10.5.6(`appVersion` 5.0.7,2020-10 打包),五年多没有安全更新。生态里最
常见的替代品 Bitnami Redis chart,2025-09 起大部分公开 OCI 包也收进
Broadcom 订阅版,公开可拉的只剩不再更新的 legacy 标签。两条路都不满足前面
那条门槛。

处理方式:`redis.enabled: false` 关掉 chart 自带的这条依赖,改用 Redis 官方
在 Docker Hub 维护的 `redis:7-alpine` 镜像写一份裸 manifest
(`apps/feast/manifests/redis.yaml`)——和 Hive Metastore 是同一类先例
(没有可信的 chart 时,裸 manifest 比硬套一个过时/收费的 chart 更诚实,见
[ADR-003](003-no-hdfs-on-k8s.md))。单副本、无密码、`emptyDir` 无持久化:
特征数据的权威来源是 Iceberg,Redis 只是物化后的在线缓存,重建或重启后重新
跑一次 `feast materialize` 就能补回来。

### 更根本的实测坑:已发布的 chart 版本和 GitHub 源码是两套不同的东西

一开始按 GitHub `master` 分支能看到的 `feast-feature-server` 模板设计
(支持 `feature_store_yaml_base64` 环境变量、Python 镜像、`feast_mode`
灵活切换)配了 `apps/definitions/feast.yaml`,线上部署后一直
`ImagePullBackOff`/`CrashLoopBackOff`。最后用 `helm pull feast/feast
--version 0.65.0 --untar` 把真正发布的 chart 下下来对着看,才发现
`charts/feature-server/templates/deployment.yaml` 是完全不同的旧版模板:
硬编码 `command: java -jar /opt/feast/feast-serving.jar ...`,读
`/etc/feast/application-*.yaml`,根本不认 `feature_store_yaml_base64`
这个变量;默认镜像还是前面说的、0.65.0 这个 tag 下没发布过的
`feature-server-java`。也就是说 GitHub 上的模板源码代表的是还没随
0.65.0 一起发布的开发中改动,**读 GitHub 源码不等于读到了已发布 chart
真正的行为**,这是这次踩得最深、最容易被忽略的一个坑。

处理方式:`feature-server` 子 chart 也放弃,和 Redis 一样改裸 manifest
(`apps/feast/manifests/feature-server.yaml`),直接用官方
`quay.io/feastdev/feature-server`(Python 实现)镜像跑 `feast serve
-h 0.0.0.0`——这是 Feast CLI 自己文档化的标准命令,`FEATURE_STORE_YAML_BASE64`
这个环境变量注入方式也是 Feast 自己代码里认的(不依赖那个已经证明不可信的
chart),已经用 `docker run` 直接对官方镜像验证过环境变量能生效。`apps/
definitions/feast.yaml` 不再引用 `https://feast-helm-charts.storage.
googleapis.com` 这个 Helm 仓库,只剩一个指向 `apps/feast/manifests/` 的
Application。

### 特征服务:官方 chart 只开 `feature-server` 子 chart

`transformation-service` 子 chart(on-demand transform)这次关掉——demo
不需要,local-lite 资源紧张,不为用不到的组件占内存。以后真的需要 on-demand
feature transform 再开。

### 物化任务:Airflow DAG,不新起一套 CronJob 机制

`feast materialize` 通过 Airflow DAG(`apps/airflow/dags/feast_materialize.py`)
定时跑,复用 [ADR-037](037-data-engineering-pipeline.md) 已验证的
SeaTunnel→Iceberg→Airflow 模式,和现有数据工程任务用同一套调度/可观测性,
不另建一套独立的 CronJob 体系。

### Demo 特征仓库:复用已有的 demo 数据,不新起一套

特征定义(`scripts/feast_feature_repo/`)直接建在
`scripts/08-create-demo-data.sh` 已经建好的 `iceberg.demo.orders` 表上——
把每笔订单当一个"事件"(entity 是下单客户,features 是这笔订单的地区/
品类/金额),用来演示 point-in-time 正确的特征检索。这不是一个刻意设计的
最佳实践特征 schema,只是复用现成数据、少造一套无关的 demo 数据。

## 已知限制

- `feature_store_yaml_base64` 是官方 chart 唯一支持注入 `feature_store.yaml`
  的方式——只能塞一整段 base64,不能引用外部文件。这意味着
  `apps/definitions/feast.yaml` 里的 base64 blob 和
  `scripts/feast_feature_repo/feature_store.yaml` 是两份需要手动保持同步的
  内容,改一处要记得跟着改另一处(重新生成:
  `base64 -i scripts/feast_feature_repo/feature_store.yaml | tr -d '\n'`)。
  这是 chart 设计本身的限制,不是这次图省事的选择,以后如果这处漂移带来
  实际麻烦,可以考虑写个校验脚本在 CI 里比对两份内容是否一致。
- Redis 无持久化、无密码,只适合 local-lite。cloud-full/prod 阶段如果要
  真正上生产,需要重新评估——要么加 PVC + AUTH,要么这时候再重新看一遍
  Redis 官方 chart 生态是否有变化(比如 Bitnami 订阅版、或者出现新的
  官方维护选项)。
- `feast_materialize` DAG 里的 `feast apply`/`materialize-incremental`
  目标 pod 用 `security_context: run_as_user: 0`(root)跑——`apps/feast/
  feature-server-image` 这个自建镜像继承官方镜像的 `USER 1001`(数字
  UID,`/etc/passwd` 没有对应条目),Spark/Hadoop 启动时
  `UserGroupInformation` 走 JVM 的 `UnixLoginModule` 查不到用户名会直接
  崩(`KerberosAuthException`,`JAVA_GATEWAY_EXITED`),`HADOOP_USER_NAME`
  这个环境变量不够用(崩溃点在它生效之前)。用 root 跑是本机 local-lite
  阶段的务实选择,不是长期方案——更干净的修法是重新 build 这个镜像,在
  entrypoint 里给 UID 1001 动态补一条 `/etc/passwd`(常见的"任意 UID"
  镜像 nss_wrapper 套路),留作后续课题。

## 后果

- `docs/architecture.md` 组件表 Feast 一行:local-lite 列从 `—` 改成 `✅`。
- 新增命名空间 `feast`,包含 `feature-server`(官方 chart)+ 裸 manifest
  的 Redis。
- 只做了离线特征(Spark 读 Iceberg)→ 物化 → 在线查询(Redis)这条链路的
  验证,"训练出来的模型 + KServe 推理请求读取在线特征"这一步没有一起做,
  留作后续单独立项时优先做,才算真正闭环到 KServe。

## 2026-08-14:端到端验证真正跑通,完整记录踩过的坑

`feast_materialize` 这条 DAG(`feast_apply` + `feast_materialize_incremental`
两个任务)从第一次触发到最终成功,一共踩了 9 个真实的、各自独立的坑,
每一个都在活集群上实测确认、修复、重新验证过,不是猜的。按遇到的顺序
记录,方便以后排查同类问题时对照:

1. **Airflow DAG Run 内部状态冲突**(`409 Unique constraint violation`)——
   之前 scheduler/ArgoCD 控制面反复崩溃留下的脏数据,清掉历史记录重新
   触发就好,不是这次改动引入的。
2. **KubernetesPodOperator 目标 pod 被抢占杀掉**——没设
   `priorityClassName`,这台机器 CPU 常年紧张,补上 ADR-041 已经建好的
   `batch` 优先级(executor pod 和目标 pod 两层都要加)。
3. **feast 命名空间 RBAC 缺失**(`403 Forbidden: cannot list pods`)——
   Airflow chart 默认的 `airflow-pod-launcher-role` 只在自己命名空间生效,
   没开 `multiNamespaceMode`;按最小权限原则单独在 `feast` 命名空间建同
   权限的 Role,不是开集群级权限。
4. **ConfigMap 整目录挂载导致 Python relative import 报错**——和 Airflow
   自己挂 DAG 目录踩过的坑同一个原因(`..data` 软链 + 带时间戳的隐藏
   目录),改用 subPath 分别挂 `feature_store.yaml`/`definitions.py`。
5. **Registry 的 S3 客户端缺凭据**(`NoCredentialsError`)——registry 走
   boto3/pyarrow,认标准 `AWS_*` 环境变量,不是给 Spark 用的
   `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`(Hadoop S3A 客户端专用),和
   Feast Serving 已经踩过、修过的同一个坑,DAG 这边的 env 是独立配置的,
   没有共享,单独补一份。
6. **本机代理拦截 kubelet 日志流,把还在正常跑的任务判定失败**——
   `get_logs=True` 内部走 kubelet containerLogs,被本机代理软件拦截
   (`docs/operations/troubleshooting.md` 已经记过的 "Internal Privoxy
   Error"),反复重试两分半后放弃、直接删 pod。关掉 `get_logs`,靠 Loki/
   Alloy 兜底日志。
7. **容器 UID 在 `/etc/passwd` 里没有条目,JVM 启动崩溃**——继承官方
   `quay.io/feastdev/feature-server` 镜像的 `USER 1001`(OpenShift 风格
   "任意 UID" 镜像惯例),Spark/Hadoop 的 `UserGroupInformation` 走
   `UnixLoginModule` 查用户名查不到直接崩(`KerberosAuthException`,
   `JAVA_GATEWAY_EXITED`)。`HADOOP_USER_NAME` 环境变量不够用(崩溃点在
   它生效之前),改用 `security_context: run_as_user: 0`(root)——local-lite
   阶段的务实选择,不是长期方案,见"已知限制"那条。
8. **demo 表在某次重置里丢了**——`iceberg.demo.orders` 不在了(大概率是
   CNPG 迁移或推倒重建测试其中一次清空的,没细查是哪一次),用
   `scripts/08-create-demo-data.sh` 里原样的 DDL/数据重建。这条提醒:
   跨多个真实事故(ADR-038/039)之后,demo 数据本身也可能悄悄丢失,不能
   假设它一直都在。
9. **`{{ ts }}` 这个 Airflow 模板变量在手动触发、`schedule=None` 的 DAG
   上是未定义的**(`UndefinedError`)——没有 data_interval,基于调度时间
   的宏都取不到值,不是 Airflow 3.x 废弃了这个宏。改成 shell 里直接取
   当前 UTC 时间,不依赖 Airflow 模板渲染上下文。

**最终验证结果**(2026-08-14,`scripts/19-feast-feature-pipeline.sh`):
`feast_apply` 和 `feast_materialize_incremental` 都 `success`,直接查
Redis(`DBSIZE` = 10,对应 demo 数据的 10 个客户)、直接调 Feature Server
的 `/get-online-features` 接口,Alice 查出 `region=East, product=Widget,
amount=120.5`,和插入的原始数据完全一致——不是只看 Airflow 状态,是真的
查了活数据。

**Why:** 这条 DAG 暴露的问题横跨好几个层面(Airflow 平台本身的资源/RBAC/
模板机制、K8s 的 ConfigMap 挂载语义、容器镜像的 UID 惯例、Feast/Spark/
Hadoop 各自的凭据体系),没有一个是 Feast 独有的坑,以后任何"在这个平台
上新增一个用 KubernetesPodOperator 跑 Spark 作业的 DAG"大概率会重新撞上
第 2/3/6/7/9 条(资源优先级、跨命名空间 RBAC、日志流、容器 UID、模板
宏),值得当成这个平台的通用经验,不只是 Feast 这一个组件的收尾细节。
