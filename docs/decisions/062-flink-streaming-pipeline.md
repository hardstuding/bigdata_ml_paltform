# 062. Flink 流式计算实现:Kafka -> Flink -> Iceberg

- 状态: 代码/manifest 完成,**没有在真实集群验证过**(这一轮 cloud-full
  云主机停机,只能做仓库级验证:`validate-charts.py`、
  `render-environment-config.py --check`、YAML 解析)。
- 承接 [ADR-056](056-flink-role-design.md) 的设计结论——那份 ADR 只回答了
  "要不要引入 Flink、Flink 该做什么、什么时候引入",明确说"真正动手装
  Flink 是后续独立的工作"。这份 ADR 就是那次实现。

## 背景

`docs/roles.md` 里"大数据开发"这个角色还剩两条缺口没解锁:"流处理引擎"
(Flink 只有设计没有实现)和"流式数据接入"(Kafka 部署了但从没接进真实
管道)。ADR-056 已经判断过这两条本质是同一条链路:先给 Kafka 接一个真实
的生产者/消费者,再验证"Kafka 流数据能落进 Iceberg"这条基础能力,并且
明确说 Flink 的角色定位是"对着流数据做实时计算",不是纯粹的数据搬运
(纯搬运是 SeaTunnel/未来的 Kafka Connect 该做的事)。

## 决策

### 整体链路

Producer(CronJob,confluent-kafka)→ Kafka topic `device-events` → Flink
流式作业(PyFlink Table API/SQL)→ Iceberg 两张表:
`demo.device_events_stream`(明细,逐条落地)+
`demo.device_events_stream_agg`(按 device_id 的 1 分钟滚动窗口计数)。

明细表的存在是为了验证"消费到的每条消息都真的进了 Iceberg";聚合表的
存在是为了兑现 ADR-056 的定位——不是简单透传,是"数据经过时顺便算点
什么",这是只有 Flink 这类流计算引擎才顺手做、SeaTunnel/Kafka Connect
做不到的事。两条 INSERT 用同一个 `StatementSet` 提交,共用一次 Kafka
消费。

事件 schema 复用 `apps/airflow/dags/seatunnel_device_events.py` 里
SeaTunnel FakeSource 已经在用的那套(event_id/device_id/event_type/
value/event_time),不是另发明一套——这个平台已经有"批量接入"的参照,
流式接入没有理由用不同的数据形状。

### 组件选型

**Flink Kubernetes Operator(Apache 官方,不是社区/Bitnami 套壳,见
ADR-008)**,装在自己的 `flink` 命名空间,`watchNamespaces: [flink]`
——只 watch 自己所在的这一个命名空间,和
`apps/components/kafka-operator.yaml`(Strimzi,`watchAnyNamespace:
false`)是同一个最小权限取舍。FlinkDeployment(作业本身)也部署在同一个
`flink` 命名空间,不是分开两个命名空间——原因见下面"命名空间边界"。

**Producer 用 CronJob,不是常驻 Deployment**——这是验证链路用的 demo
数据源,不是真实业务系统,常驻 Deployment 空转没有意义。每 5 分钟一批
(见 `apps/kafka-producer/manifests/`),装在 `kafka` 命名空间(和
`kafka-cluster` 同一个命名空间,概念上它是 Kafka 集群的数据源,不是
独立业务系统)。

## 版本选择和兼容性核对

这一段是这份 ADR 的核心——不是拍脑袋选的版本,逐项查过 Maven Central /
PyPI / 官方下载页确认过。

| 组件 | 版本 | 依据 |
|---|---|---|
| Flink Kubernetes Operator | 1.15.0 | `downloads.apache.org/flink/` 当前最新稳定发布版(查过官方下载页目录列表,不是猜的)|
| Flink(被管理的作业本身) | 1.20.5 | Operator 1.15.0 自己内部跑在 Flink 1.20.1 上(查过它 release-1.15 分支的 `pom.xml`),但它管理的 FlinkDeployment 可以是任意受支持版本;选 1.20.x 分支是因为 `iceberg-flink-runtime` 和 `flink-connector-kafka` 在这条线上都还在正常发新版本,Flink 2.x(已发到 2.3.0)生态还在快速变动,这个仓库其它组件(Kafka 4.3.0、Iceberg 1.10.0)也都是稳定线,不跟进 2.x 这条更激进的线。1.20.5 是 1.20 分支当前最新патch(`downloads.apache.org/flink/` 目录列表确认,1.20.0~1.20.4 已被移出在线目录,只保留最新patch)|
| apache-flink(PyPI,PyFlink) | 1.20.5 | 和 Flink 核心版本严格对齐——PyFlink 的 Python API 版本必须和集群跑的 Flink 版本一致,这是硬性要求,不是建议。查过 PyPI 确认这个版本号确实存在（不是所有 1.20.x patch 都会发 PyPI 包，这次逐个确认过 1.20.0~1.20.5 全部存在）|
| iceberg-flink-runtime-1.20 | 1.10.0 | Iceberg 官方给 Flink 1.20 单独发了一份 runtime jar（Maven Central 确认存在）。版本号故意和 `apps/spark-iceberg-image`、`apps/argo-workflows-training-image`、`scripts/feast_feature_repo/feature_store.yaml` 用的 `iceberg-spark-runtime` 保持同一个 1.10.0，不是另挑一套——这个仓库现在所有引擎读写的是同一份 Iceberg 表格式，版本不一致会增加"某个引擎读不了这张表"的风险面 |
| flink-connector-kafka(SQL 版) | 3.4.0-1.20 | Maven Central 上 Flink 1.20 线最新一版，fat jar 自带 kafka-clients，不用额外管理 kafka-clients 版本 |
| Hadoop client（api/runtime/aws）+ aws-java-sdk-bundle | 3.3.4 / 3.3.4 / 3.3.4 / 1.12.262 | **和 `apps/spark-iceberg-image/Dockerfile` 完全一致**，不是巧合。官方 Flink 镜像从 1.11 起就是 hadoop-free 的（不像 apache/spark 镜像自带 Hadoop client），必须自己把整套 Hadoop client 补齐；版本选择照抄 Spark 那条已经在这个仓库真实验证过能连通同一个 Hive Metastore + MinIO 的组合，不是另配一套版本组合去猜兼容性 |

### 一个真实踩到的命名坑:`flink-python` 的 Maven 坐标改过名

写 FlinkDeployment 的 `job.jarURI` 时,最初照抄了 flink-kubernetes-
operator 仓库 `examples/flink-python-example` 里的写法
(`flink-python_2.12-1.16.1.jar`),想当然地把版本号换成 1.20.5 就完事。
查 Maven Central 时发现 `flink-python_2.12` 这个坐标最后一次发布是
1.15.4——从 Flink 1.16.0 起,这个模块改用不带 Scala 后缀的
`flink-python`(Maven Central 上 `flink-python` 坐标从 1.16.0 一路发到
2.3.0,和 `flink-python_2.12` 的发布区间正好衔接)。1.20.5 这一版正确的
文件名是 `flink-python-1.20.5.jar`,不是 `flink-python_2.12-1.20.5.jar`
——如果直接照抄官方 example 换版本号,提交作业时 `local://` 路径会指向
一个镜像里根本不存在的文件,作业连启动都启动不起来。这条记录下来是因为
网上(包括官方仓库本身)不少 1.16 之前版本的示例还在用旧命名,照抄示例
时命名规则本身也可能已经过期,不能只换版本号数字就当完事。

### Kafka 4.3.0 broker 兼容性怎么核对的

`flink-connector-kafka:3.4.0-1.20` 内置的 `kafka-clients` 版本不是
4.3.0(这条连接器线还没跟进 Kafka 4.x 的 client 版本),但这不构成
兼容性问题——Kafka 的 client-broker 线协议是显式协商版本的(broker 会
告诉 client 自己支持的 API 版本范围,取交集),这条连接器走的是标准
Produce/Fetch API,没有用到任何 Kafka 4.x 独有的新协议特性(比如
KIP-848 的下一代消费组协议是可选的,client 不主动要求就不会用到)。
新版本 broker 对旧版本 client 的兼容窗口一贯很宽,这不是这个仓库自己的
判断,是 Kafka 项目自己的协议设计原则。真实验证仍然待补——见下面
"验证记录"。

## 命名空间边界:为什么 operator 和 job 共用一个命名空间

Flink Kubernetes Operator 的 Helm chart,当 `watchNamespaces` 非空时,
会在**每一个**被 watch 的命名空间里创建 Role/RoleBinding/`flink` 这个
jobServiceAccount(查过 chart 模板 `templates/flink/service_account.yaml`
/`templates/rbac/role_binding.yaml` 确认——这是官方设计,不是这个仓库
自己的用法)。

如果把 operator 装在 `flink-operator` 命名空间、job 装在单独的 `flink`
命名空间,operator 的 Helm Application 在渲染时就会尝试往 `flink` 这个
"别人的"命名空间里创建 RBAC 资源——但 ArgoCD 的 `CreateNamespace=true`
只保证**这个 Application 自己的** `destination.namespace` 存在,不保证
values 里手写的其它命名空间存在。在一个全新环境上,`flink` 这个命名空间
要靠 `flink-streaming-demo` 这个独立的 Application 才会被创建,两个
Application 之间没有 sync wave 保证顺序,会出现"operator 先同步、
`flink` 命名空间还不存在、RBAC 创建失败"的启动竞态。

解法是把 operator 自己的 `destination.namespace` 直接设成 `flink`,
`watchNamespaces: [flink]` 里的这一项就是 operator 自己的 release
命名空间——chart 模板对这种情况有专门处理(`.Release.Namespace` 已经在
`watchNamespaces` 里时不重复建),不存在跨 Application 的命名空间竞态。
这和 `kafka-operator`/`kafka-cluster` 两个 Application 共用 `kafka`
命名空间是同一个模式,不是新发明的。

## 已知的维护成本:S3A 配置要在两个命名空间各存一份

这是这个仓库第一次出现"同一份 S3A 配置需要跨命名空间复制"的情况——
Trino/Spark 都是把 `s3a.*`/`fs.s3a.*` 属性直接写进自己的组件配置里
(sparkConf / catalog properties),不需要一份独立的 `core-site.xml`
文件。只有 Flink 走 `HADOOP_CONF_DIR` 环境变量 + 挂载的 `core-site.xml`
这条路径(Flink 官方文档说明的机制:`HadoopUtils` 会自动从
`HADOOP_CONF_DIR` 指向的目录加载 Hadoop Configuration)。

`hive-metastore-core-site` 这个 ConfigMap 在 `data` 命名空间,Flink 的
JobManager/TaskManager 跑在 `flink` 命名空间,ConfigMap 是命名空间级
资源,没法跨命名空间挂载,只能在 `apps/flink-streaming-demo/manifests/
core-site-configmap.yaml` 里存一份内容完全一样的副本。MinIO endpoint/
访问方式变了,这份文件要跟着
`apps/hive-metastore/manifests/core-site-configmap.yaml` 一起改,不会
自动同步——这次没有引入 Kustomize 之类的机制去自动生成多份 ConfigMap,
因为现在只有一个消费者,提前做一个只有一个使用场景验证过的抽象没有
必要;以后如果这类需要 `HADOOP_CONF_DIR` 的组件继续增多,值得回头做。

同理,`minio-root` 这个 Secret 也要复制进 `flink` 命名空间(K8s Secret
同样是命名空间级资源),这次在 `scripts/00-generate-secrets.sh` 的
`MINIO_CONSUMER_NAMESPACES` 列表里加了 `flink`——和 `spark-operator`
当初的坑(ADR-036,SparkApplication driver pod 报 secret not found)
是同一个模式,这次提前加上,不等实测报错才发现。

## 没有做的事(明确的范围边界)

- **没有给 FlinkDeployment 配持久化 savepoint 存储**,`upgradeMode:
  stateless`——这个 demo 每次重新提交都是从 Kafka
  `scan.startup.mode=earliest-offset` 重新读,不依赖从 savepoint 恢复
  state,数据量很小,不需要为了一个 demo 引入 savepoint 的运维负担。
- **没有做真实的吞吐/背压测试**,并行度和资源规格(见
  `environments/resource-profiles.yaml` 的 `flink_*`/`kafka_producer_*`
  键)都是"能跑起来就行"的方向性起步值,prod 那一档更是明确标注"不是
  实测容量规划"。
- **没有把这两个新 Application 加进任何环境的 `enabled_components`**
  ——按这个仓库"分工边界"的约定(见 `CLAUDE.md`),架构级新增组件不由
  执行方自己拍板启用。需要往
  `environments/cloud-full/config.yaml`(以及以后的 prod)的
  `enabled_components` 列表里加:`flink-kubernetes-operator.yaml`、
  `flink-streaming-demo.yaml`、`kafka-producer-device-events.yaml`。

## 验证记录

**这一轮 cloud-full 云主机停机,完全没有在真实集群跑过**,只做了仓库级
验证:

- `python3 scripts/validate-charts.py`:0 失败(新组件没加进
  `enabled_components`,不会被这个脚本扫到——这是预期,不是漏检;
  Helm 部分额外用 `helm template` 手动跑过一遍 `flink-kubernetes-
  operator` 的 chart + values,确认能正常渲染,不依赖仓库自己的 CI
  机制)。
- `python3 scripts/render-environment-config.py cloud-full --check`:
  退出码 0。
- `python3 scripts/check-networkpolicy-consumers.py`:没有报警(`flink`
  命名空间已经加进 `minio.yaml`/`postgres.yaml` 的白名单)。
- 所有新增 YAML 用 `yaml.safe_load_all` 逐个解析过,没有语法错误。

**真正没有验证过、需要真实集群才能验证的**:镜像能不能真的 build 成功
(GitHub Actions 还没跑过这两个新镜像)、PyFlink 作业能不能真的连上
Kafka/Hive Metastore/MinIO、`HADOOP_CONF_DIR` 这条路径在 Flink 里是否
真的按预期加载了 `core-site.xml`、checkpoint 间隔下 Iceberg sink 能不能
真的按预期提交数据、Kafka 4.3.0 broker 和这个版本的连接器组合是否真的
如预期兼容。`scripts/31-run-flink-streaming-demo.sh` 是给这些问题准备的
验证脚本,下次云主机开机后应该第一时间跑。


## 2026-08-22 补:第一次 CI 构建失败,根因是 PyFlink 没有 arm64 wheel

第一次跑 `build-images.yml`,`flink-iceberg` 这个镜像**构建失败**(同一次
run 里另外 9 个镜像都成功)。

**根因(查过 PyPI 确认,不是推测)**:`apache-flink==1.20.5` 在 PyPI 上
只发了 x86_64 的 manylinux wheel 和 macOS 的 arm64 wheel,**没有
linux/aarch64 wheel**。这个仓库的镜像流水线默认建
`linux/amd64,linux/arm64` 两个平台,arm64 那条腿拿不到 wheel 只能从
sdist 现编译,在 GitHub runner 的 QEMU 模拟下必然挂。

**处理**:给 `build-images.yml` 的 matrix 加了一个可选的 `platforms`
覆盖字段(默认仍然是两个平台),`flink-iceberg` 单独设成
`linux/amd64`。

**为什么可以只建 amd64**:唯一的 arm64 环境是 local-lite(colima / Mac
M2),而 Flink 相关的三个组件**没有出现在
`environments/local-lite/config.yaml` 的 `enabled_components` 里**——那
一档只有 17 个轻量组件,连 Kafka 都没启用,Flink 更不可能在笔记本上跑。

**⚠️ 留给以后的约束**:如果哪天要在 local-lite 上启用 Flink,不能直接
往 `enabled_components` 里加了事。这个仓库踩过"只建 amd64 的镜像在 arm64
节点上靠 QEMU 跑,触发 client-go 并发 bug"的坑(iam-sync 那次)。届时要么
等上游出 aarch64 wheel,要么用原生 arm64 runner 构建。

顺带一条:这次也顺手核实了 Dockerfile 里全部 6 个 jar 下载 URL 都是
HTTP 200(ADR 正文里那个 `flink-python` 坐标改名的坑修对了),失败**不是**
jar 地址问题。


## 2026-08-22 第二个补充:真实部署抓到的两个问题

### 1. TaskManager 内存 768m,Flink 直接拒绝启动

第一次真正部署到 cloud-full,FlinkDeployment 卡在 `UPGRADING` /
`jobManagerDeploymentStatus: MISSING`,operator 日志里的根因:

```
IllegalConfigurationException: TaskManager memory configuration failed:
Sum of configured Framework Heap Memory (128.000mb), Framework Off-Heap
Memory (128.000mb), Task Off-Heap Memory (0 bytes), Managed Memory
(128.000mb) and Network Memory (64.000mb) exceed configured Total Flink
Memory (320.000mb).
```

**这是"照着别的组件的规格分档习惯给小值"踩的坑**:Flink 的
`taskManager.resource.memory` 是**进程总内存**,要先扣掉 JVM overhead
(10%,下限 192m)和 metaspace(默认 256m),剩下的才叫 "Total Flink
Memory";而框架自己固定要占 128+128+128+64 = 448m。768m 扣完只剩 320m,
连框架都不够,更别说跑任务。

实际下限大约 900m 出头。local-lite/cloud-full 改成 jobmanager 1024m /
taskmanager 1792m(留出真正的 task heap),prod 的 taskmanager 提到
4096m。

**这个 bug 只有真部署才会暴露**——`helm template`、`validate-charts.py`、
YAML 解析全都查不出来,它是 Flink 运行时的语义校验。

### 2. 自建镜像在境内拉不动,digest 固定和镜像站加速互斥

`flink-iceberg` 压缩后 **1244MB**。境内云主机直连 `ghcr.io` 实测约
80KB/s,要 4 个多小时。而已有的加速手段
`scripts/23-pull-images-remote-via-mirror.sh` 用的是"经国内镜像站拉 →
`docker tag` 打回原名",**digest 引用没法 `docker tag`**,所以 digest
固定的镜像用不了这条路;docker 的 `registry-mirrors` 又只对 Docker Hub
生效,对 ghcr 无效。

更麻烦的是:DaoCloud 镜像站有**白名单**,只代理知名上游镜像,我们自己的
GHCR 包直接被拒(`this image is not in the allowlist`)。

临时处理:这两个自建镜像改用 CI 同时推的 **commit-SHA 标签**(事实不可变,
满足 ADR-010 的"不用浮动 tag",而且能 `docker tag`)。

**但这只是绕过,不是解决。** 探测到 `ghcr.nju.edu.cn` / `ghcr.linkos.org`
这类无白名单的公开代理对我们的仓库返回 200,可以作为候选,但引入第三方
代理需要核对 digest 一致性,而且可靠性不由我们控制。真正的生产解法应该是
**把自建镜像推一份到境内 registry(比如阿里云 ACR)**,这需要用户提供
凭据,已记进 `docs/BACKLOG.md` 2.10 等确认。

**这条是多节点演练的硬前置**:3 台新机器 × 全量镜像,按当前速度不可行。
