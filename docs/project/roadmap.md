# Backlog

新想法/顺手发现的问题,默认进这里,不自动打断
[`docs/project/current-work.md`](../project/current-work.md) 里的当前主线。这份文件只做索引
和优先级排序,不重复描述已经在别处写清楚的内容——每一项都指向权威来源
(ADR / architecture.md),不在这里复制一遍。

**排序依据(2026-08-19 起,见 [ADR-057](../decisions/057-architecture-review-2026-08-19.md)):
按"阻塞哪个角色的哪条能力"排,不按"还有哪个组件没接"排。**
判断某件事该排多前,先看 [`docs/project/capability-matrix.md`](../project/capability-matrix.md) 的角色能力表。

> 2026-08-19 重排说明:此前的 P1.5/P1.6/P1.7 不是优先级,是不同日期追加
> 的批次(带小数的层级本身就是"一直在追加、没有重排过"的证据)。这次
> 按角色影响重新排了序,已完成的条目收敛到底部"已完成(存档)",避免
> 一份 400 行的清单里一半是划掉的东西。

---


## 一件半完成的事:platform-runtime 镜像接进 CI(2026-08-29)

**做完的**:`apps/platform-image` 进了 `build-images.yml` 的构建矩阵。在这
之前它是**唯一一个靠手工在云主机上 `docker build` 的镜像** —— 一个横在
"一键部署"路径上的手工步骤,而且手工构建出来的东西没有任何地方记录它是从
哪个 commit 来的。

它比别的镜像多两处特殊,都写在工作流的注释里:build context 必须是仓库根
(它 COPY `platform-sdk/`),以及构建时的 pip 源要覆盖成默认 PyPI(GitHub
runner 在境外)。`platform-sdk/**` 也进了触发路径 —— SDK 改了镜像就该重建,
不然 notebook 和定时作业用的还是老 SDK,**而且不会有任何提示**。

**2026-08-30 做完了第 2 步(切引用),第 3 步(集群验证)待下次开机。**

原计划的三步:

1. ~~先让 CI 真的构建成功一次,拿到一个**确实存在**的 SHA 标签~~ 已完成
2. ~~再把这几处切过去~~ **已完成,而且做法比原计划更进一步** —— 不是"把
   六个写死的字符串各自改成新值",那样只是把一个过期的硬编码换成另一个。
   现在它是 `environments/<env>/config.yaml` 里的 **`platform_job_image`
   一个配置项**:
   - local-lite:`local/platform-runtime:0.1.0`(本地构建,这台机器上
     没有 ACR 凭据,而且本地开发该能改完立刻用)
   - cloud-full / prod:ACR 上带 commit SHA 的那份,**两档用同一个 tag**
     (同一份构建产物一路走到生产,不各建各的)

   能渲染的都改成渲染了(JupyterHub 组件、Argo CronWorkflow、
   singleuser 的 `PLATFORM_JOB_IMAGE` 环境变量);渲染不了的两处
   (`platform_sdk/config.py` 的兜底默认值、Airflow 的 DAG)由
   `scripts/check-platform-image-refs.py` 盯着,进 CI。文档和示例里原来
   写死的那两处**直接删掉了值** —— 镜像按环境不同,文档里名指哪一档都会
   误导另一档的读者(CLAUDE.md「能删掉重复的就删掉」)。

   `check-image-tag-freshness.py` 也一并覆盖了它:改了
   `apps/platform-image/` 或 `platform-sdk/` 却没换 SHA,CI 会红。
3. ~~在集群上验一次 notebook 和一个定时作业真的能起来~~ **2026-08-30 验完**:
   - ACR 上 `platform-runtime:49d1d1cd...` 确实存在,argo-workflows 命名空间
     能直接拉(拉取凭据也在)
   - 定时作业:克隆一份 CronWorkflow、时间改到两分钟后,它**自己**触发并
     跑成功,`main` 容器用的就是 ACR 那个镜像
   - notebook:用和 singleuser 完全相同的镜像+环境变量+ServiceAccount 起
     一个 pod,`platform_sdk.query()` 查得通 Trino(`current_user` 是
     `notebook_service`),`default_job_image()` 解析成 ACR 那份;再从这个
     pod 里 `submit_job()` 提交一个作业,**作业跑在同一个镜像上**并查到了
     真实数据(`orders` 10 行)。这一步验的就是 ADR-058 那条"交互开发和
     调度执行环境一致"

   **有一点没验到,如实记**:没有真的通过 JupyterHub 的 Web 界面(OIDC 登录)
   spawn 一个 notebook —— 那要走浏览器,脚本进不去。spawner 用哪个镜像是
   从渲染出来的配置和 `continuous-image-puller` 的 initContainer 确认的
   (两者都指向新镜像),不是从一个真的被 spawn 出来的 pod 上确认的。

**为什么第 3 步必须单独做**:一旦 ACR 上那个 tag 不存在或者名字拼错,
**集群上所有 notebook 和定时作业会同时 ImagePullBackOff**,而这是没法在
本地验证的。

---

## 生产可用性缺口(2026-08-23 主动盘点,独立于下面的 P0-P5)

[`docs/project/production-readiness-gaps.md`](../project/production-readiness-gaps.md) —— 六条
"不做会出事"的缺口:数据质量断言、数据新鲜度 SLO、查询审计、成本归属、
Schema Registry、门户改工作台。

**2026-08-30 核对:六条主体全部完成并实机验证过。** 那份文档此前比现实
落后四五天(三条写着"未部署"的东西早就部署验证了),已经改成**不再自己记
状态** —— 状态只在 `capability-matrix.md` 里有一份,那份文档留判断和"做到
什么程度才算做完"。`check-capability-matrix.py` 现在会拦住状态又长回去。

**真正剩下的缺口**(都写在那份文档的表格里):
- **「审计流断了」的告警** —— 唯一一条"不做会出事"性质的:审计流断了,
  现在没有任何人会知道。
- 成本的管理视角(按月聚合、和预算对比);Flink 作业接 Schema Registry;
  数据质量断言只覆盖两张表。

---

### ~~flink-kubernetes-operator 的 4 个 CRD 一直 OutOfSync~~ —— 已解决(2026-08-26)

原来这里猜的是"CRD 太大,ArgoCD 塞不进 last-applied 注解"(这个仓库踩过
四次的那一类)。**猜错了。** 把 chart 渲染出来的 CRD 和活对象逐字段比过
之后,差异只有 4 个字段,全部是 API server 的默认值归一化(`priority: 0`
被丢掉、`spec.conversion.strategy: None` 和 `spec.names.listKind` 被补上),
**值不同的字段是 0 个**——这个 OutOfSync 从来不代表任何真实漂移。
而且 `ServerSideApply=true` 本来就已经开着,那个"注解塞不下"的假设从一开始
就不成立。

解法是三条精确的 `ignoreDifferences` 路径,不是整个 CRD 的 blanket ignore
(后者会把真实的 schema 变更一起吞掉)。详见
`apps/components/flink-kubernetes-operator.yaml` 里的注释。

留着它的代价不是"看着难受",是**训练所有人忽略 OutOfSync**,下次真出漂移
时没人当回事。

### 1.1 环境抽象补上"组件选择"层 —— 已完成(2026-08-20)

这次拉起 P1.2/1.3/1.4 全靠人工 `git mv` + 逐个手动排查 Keycloak
client/Secret/scope 缺口完成,过程中暴露的好几个 bug(client 已存在但
Secret 缺失、groups scope 从来没配过)本可以在"改配置就重新拉起"这套
机制的约束下被更早、更结构性地测试捕获,现在只能靠"当天有没有人手动
测登录"这种运气发现。cloud-full 是 16 vCPU / 64 GiB,不是资源不够,是
"哪些组件在哪个环境启用"当时(2026-08-19)靠人工在 `apps/definitions/`
和 `environments/cloud-full/pending-definitions/` 之间 `git mv`——这个
机制是 2026-08-08 为 6GB 的 colima 本机设计的,搬到云上没有重新评估过。

**已完成(2026-08-20,ADR-057 第三批)**:"启用哪些组件"已经变成
`environments/<env>/config.yaml` 里的 `enabled_components` 声明式列表,
和已有的 `scripts/render-environment-config.py` 机制衔接(没有再造一个
新脚本),`pending-definitions/` 这个靠目录位置表达启用状态的机制已退役、
目录已删除。

同一层还缺"规格分档"(副本数/resources/持久化按环境取值),可以一起做,
优先级低于组件选择。

### 1.5 Argo Workflows RBAC —— 已完成(2026-08-19)

SSO 登录 CrashLoopBackOff(issuer/issuerAlias 配置问题)和登录后调用
workflows API 403(`server.sso.rbac` 需要的 ServiceAccount + 长期
token Secret + Role/RoleBinding)都已修好,四个资源加进了
`templates/apps-definitions/argo-workflows.yaml` 的 `extraObjects`。
真实 curl+cookie-jar 验证过:登录→查询 workflows API 200→建一个真实
Workflow→能查到→删除清理。详见 `docs/project/current-work.md` 归档记录。

### 1.6 Kafka 部署 —— 已完成(2026-08-19)

部署 + 真实建 topic/生产/消费一条消息验证通过,大数据开发角色最后一块
拼图补上。**还没接进真实数据管道**(没有真实 Producer/Consumer 应用,
目前零真实消费者,这条不算数据管道本身完成,只是组件可用性)。

### 1.7 算法链路端到端重新验证 —— 全部完成(2026-08-20)

JupyterHub/MLflow/Spark Operator/Feast/Argo Workflows 都已部署验证。
"Argo Workflows 编排训练"(2026-08-19)、"notebook 里触发训练"的 SDK
机制 `run_workflow_template()`(2026-08-20,云端 debug pod 验证过,见
BACKLOG 2.6)都已经完成并验证。

**最后一段空白"notebook → Feast 特征 → Argo Workflows 训练 → MLflow
记录"这条完整链路也已经真实跑通**:新增 `train-from-feast-features`
这个独立 WorkflowTemplate(不是给 `train-demo-model` 加第二个
entrypoint,`run_workflow_template()` 按名字触发不支持覆盖
entrypoint),用 `FeatureStore.get_historical_features()` 从
`customer_order_features` 取 point-in-time 正确的历史特征(不是像
`train-demo-model` 那样用合成数据),训练一个玩具分类器,注册进 MLflow
Model Registry。

**云端真实触发,过程中挖出并修好 4 个真实 bug**(如实记录,不是一次
就顺利跑通):
1. `feast[spark]==0.65.0` 的 extras 语法要求 `pyspark>=4.0.0`,和这个
   项目整条 Spark/Iceberg 链路锁定的 3.5 系列冲突——改成不用 extras
   语法,分别装 `feast`(不带 `[spark]`)+ `pyspark==3.5.9`。
2. `FeatureStore()` 初始化会 eagerly import `online_store` 配置对应的
   模块(即使这个脚本压根不碰在线存储),报 `FeastModuleImportError:
   No module named 'redis'`——补上 `redis` 包。
3. **最花时间的一个**:`spark.jars.packages` 触发的 Ivy 依赖解析直连
   `repo1.maven.org`,cloud-full 云主机上会真的卡死不动(几百 MB 的
   `aws-java-sdk-bundle` 下载进度停在 0 字节超过 8 分钟),加
   `spark.jars.repositories` 指向阿里云镜像也没用(Ivy 默认解析器
   优先级不会因为多了候选源就绕开卡住的那个)——改成在镜像构建期
   (GitHub Actions,境外 runner)把三个 jar 下载好打进镜像,训练脚本
   自己建 SparkSession 指向本地 jar 路径(利用 `SparkSession.builder.
   getOrCreate()` "已有活跃 session 就复用、忽略新 config" 这条特性,
   让 feast 内部拿到的是这个已经配好本地 jar 的 session),运行时完全
   不联网。
4. Hive Metastore 的 NetworkPolicy(`allow-consumers-to-hive-
   metastore`)只列了 trino/spark-operator/airflow/feast 四个命名
   空间,没人想到 `argo-workflows` 读特征时也要连它拿 Iceberg 表元
   数据——和这个项目反复踩过的"新命名空间消费共享服务,NetworkPolicy
   忘记加白名单"是同一类坑,第 N 次复现,补上。
5. `mlflow.sklearn.log_model()` 报 `ModuleNotFoundError: No module
   named 'skops'`——和 `train_demo_model.py` 早就踩过、也修过的同一个
   坑(MLflow 3.x 默认序列化格式这个精简镜像没装),补
   `serialization_format="pickle"`。

**最终验证**:`train-from-feast-verify-7q7pv` 这个 Workflow
**Succeeded**,日志显示"从 Feast 取了 10 行历史特征,训练完成…已注册
进 MLflow Model Registry(demo-region-classifier)",`MlflowClient.
get_model_version()` 查询确认 `status: READY`。

---

### 镜像拉取:registries.yaml **对这个集群不适用**(2026-08-26 更正)

这条原来写的是"配 `/etc/rancher/k3s/registries.yaml` 让 tag 和 digest 都
自动走镜像站"。**查证之后发现这个方案对这台机器行不通**,记在这里免得
以后有人照着做、白白重启一次 k3s:

`registries.yaml` 是 **k3s 内置 containerd** 的功能。而这个集群的 k3s 是用
`--docker` 起的(cri-dockerd,见 `scripts/21-bootstrap-cloud-vm.sh` 开头的
说明,当初是为了和本机 colima 保持同一套、也方便直接 `docker load`)。
走 cri-dockerd 的话,那个文件**根本不会被读**。

cri-dockerd 下实际可用的只有 Docker daemon 的 `registry-mirrors`
(`/etc/docker/daemon.json`),而它**只能镜像 Docker Hub**,ghcr.io 和
registry.k8s.io 都不支持按仓库配镜像。

所以现在的选项是这三个,都不是免费的:

| 方案 | 覆盖范围 | 代价 |
|---|---|---|
| `daemon.json` 的 `registry-mirrors` | **只有 docker.io** | 要重启 dockerd = 节点上所有容器重启 |
| k3s 换回内置 containerd | 全部仓库 | 镜像存储换一套,现有 56GB 全要重新准备;改动面大 |
| 维持现状 + `scripts/38-ship-image-to-cloud.sh` | 全部仓库 | 要人操作,但**不需要重启任何东西** |

**还有一个坑**:今天卡住的镜像引用是 `docker.getcollate.io/openmetadata/server`
——那是 Collate 自己的代理域名,**不是 `docker.io`**,所以哪怕配了
`registry-mirrors` 也不会命中。要让它生效,还得把 chart 里的
`image.repository` 从 `docker.getcollate.io/openmetadata/server` 改成
`openmetadata/server`。

**当前结论**:先不动运行时配置。scripts/38 已经把这件事变成一条可重复
执行的命令(实测 457MB 本地下载 1 分 19 秒 + 上传 90 秒,比等镜像站快一个
数量级),够用。等哪天真的频繁到受不了,再按上表第二行做一次彻底的
——那是个独立项目,不是顺手改配置。

---

### 黄金链路探针失败会把 ArgoCD 应用标成 Degraded(信号错位)

`golden-path-probes` 这个 Application 只要有过失败的 Job(而
`failedJobsHistoryLimit: 3` 会保留),ArgoCD 就判 Degraded——**但探针失败
说明它抓到了东西,不说明这个组件坏了**。

放着不管的后果:ArgoCD 上常年挂一个 Degraded。这个项目刚花力气消掉过
flink CRD 那个常年 OutOfSync,理由是一样的——**常年黄灯会训练所有人忽略
黄灯**。

解法倾向给这个 Application 配 ArgoCD 自定义健康检查(Lua),只看 CronJob
存不存在、不看历史 Job 成败。**失败信息应该只从告警和看板出去,不该混进
部署状态里**:这两套信号回答的是不同问题(部署对不对 vs 平台好不好),
混在一起两边都变钝。

细节见 [ADR-079](../decisions/079-golden-path-probes.md)。

---

### ~~hive-metastore 每次 pod 启动都要联网从 Maven 下 3 个 jar~~ —— 已解决(2026-08-28)

自建了 `apps/hive-metastore-image/`(多阶段构建:`curlimages/curl` 下 jar,
再 COPY 进 hive 镜像),两个下载用的 initContainer 整个删掉。

**实测:Pod 创建 → Ready 从 6~7 分钟降到 55 秒**,initContainer 从 3 个减到
1 个(只剩 `wait-for-postgres`)。换完跑 `goldenpath-query` 探针确认链路正常
——不只是"Pod 起来了"。

顺带修了两个自己挖的坑:第一版直接在 `apache/hive:3.1.3` 里 `RUN curl`,
CI 构建失败(**线索一开始就在**:原来的 initContainer 用的是
`curlimages/curl` 镜像,说明当时的人也知道 hive 镜像里没有 curl,只是那个
信息藏在"用哪个镜像"里没写成一句话);以及往 matrix 加了镜像却忘了加
`on.push.paths` 触发,导致改 Dockerfile 后 CI 根本不跑——已加
`scripts/check-build-triggers.py` 进 CI 拦这一类。

---

## P0(会阻断当前主线的,才有资格排这里)

当前没有。如果出现真实的数据风险/持续计费异常/安全问题,加在这里,
并在 `docs/project/current-work.md` 里注明"CURRENT 被 P0 阻断,原因是……"。

---

## P1:解锁角色能力(投入产出比最高,先做这一批)

依据 [`docs/project/capability-matrix.md`](../project/capability-matrix.md)。**1.2/1.3/1.4 已于 2026-08-19 完成**
(OpenMetadata / JupyterHub+MLflow / Spark Operator+SeaTunnel+Spark
History Server 全部部署并逐个真实登录验证过,不是只看 Pod Running)。
过程中发现并修复了 8 个真实 bug——这些组件长期 park、从没真的走过一次
登录,配置错误一直没暴露:SSO 端口/scope/upstream Service 名字写错
(mlflow-oauth2-proxy 配的 Service 名根本不存在)、Argo Workflows
issuer 不匹配导致 CrashLoopBackOff 两天多没人发现、Keycloak realm 从
建立起就没配过 groups claim mapper(意味着 Grafana/JupyterHub 的按组
授权可能从来没真的生效过,这次一并修了)、OpenMetadata 镜像走自定义
域名代理导致 ImagePullBackOff、MLflow 内存限制太小 36 秒内 OOMKill、
Spark History Server 缺 S3A 连接器 jar。详见 `docs/journal/2026-08.md`
和对应 commit。

### 1.1 环境抽象补上"组件选择"层 —— 已完成(2026-08-20)

这次拉起 P1.2/1.3/1.4 全靠人工 `git mv` + 逐个手动排查 Keycloak
client/Secret/scope 缺口完成,过程中暴露的好几个 bug(client 已存在但
Secret 缺失、groups scope 从来没配过)本可以在"改配置就重新拉起"这套
机制的约束下被更早、更结构性地测试捕获,现在只能靠"当天有没有人手动
测登录"这种运气发现。cloud-full 是 16 vCPU / 64 GiB,不是资源不够,是
"哪些组件在哪个环境启用"当时(2026-08-19)靠人工在 `apps/definitions/`
和 `environments/cloud-full/pending-definitions/` 之间 `git mv`——这个
机制是 2026-08-08 为 6GB 的 colima 本机设计的,搬到云上没有重新评估过。

**已完成(2026-08-20,ADR-057 第三批)**:"启用哪些组件"已经变成
`environments/<env>/config.yaml` 里的 `enabled_components` 声明式列表,
和已有的 `scripts/render-environment-config.py` 机制衔接(没有再造一个
新脚本),`pending-definitions/` 这个靠目录位置表达启用状态的机制已退役、
目录已删除。

同一层还缺"规格分档"(副本数/resources/持久化按环境取值),可以一起做,
优先级低于组件选择。

### 1.5 Argo Workflows RBAC —— 已完成(2026-08-19)

SSO 登录 CrashLoopBackOff(issuer/issuerAlias 配置问题)和登录后调用
workflows API 403(`server.sso.rbac` 需要的 ServiceAccount + 长期
token Secret + Role/RoleBinding)都已修好,四个资源加进了
`templates/apps-definitions/argo-workflows.yaml` 的 `extraObjects`。
真实 curl+cookie-jar 验证过:登录→查询 workflows API 200→建一个真实
Workflow→能查到→删除清理。详见 `docs/project/current-work.md` 归档记录。

### 1.6 Kafka 部署 —— 已完成(2026-08-19)

部署 + 真实建 topic/生产/消费一条消息验证通过,大数据开发角色最后一块
拼图补上。**还没接进真实数据管道**(没有真实 Producer/Consumer 应用,
目前零真实消费者,这条不算数据管道本身完成,只是组件可用性)。

### 1.7 算法链路端到端重新验证 —— 全部完成(2026-08-20)

JupyterHub/MLflow/Spark Operator/Feast/Argo Workflows 都已部署验证。
"Argo Workflows 编排训练"(2026-08-19)、"notebook 里触发训练"的 SDK
机制 `run_workflow_template()`(2026-08-20,云端 debug pod 验证过,见
BACKLOG 2.6)都已经完成并验证。

**最后一段空白"notebook → Feast 特征 → Argo Workflows 训练 → MLflow
记录"这条完整链路也已经真实跑通**:新增 `train-from-feast-features`
这个独立 WorkflowTemplate(不是给 `train-demo-model` 加第二个
entrypoint,`run_workflow_template()` 按名字触发不支持覆盖
entrypoint),用 `FeatureStore.get_historical_features()` 从
`customer_order_features` 取 point-in-time 正确的历史特征(不是像
`train-demo-model` 那样用合成数据),训练一个玩具分类器,注册进 MLflow
Model Registry。

**云端真实触发,过程中挖出并修好 4 个真实 bug**(如实记录,不是一次
就顺利跑通):
1. `feast[spark]==0.65.0` 的 extras 语法要求 `pyspark>=4.0.0`,和这个
   项目整条 Spark/Iceberg 链路锁定的 3.5 系列冲突——改成不用 extras
   语法,分别装 `feast`(不带 `[spark]`)+ `pyspark==3.5.9`。
2. `FeatureStore()` 初始化会 eagerly import `online_store` 配置对应的
   模块(即使这个脚本压根不碰在线存储),报 `FeastModuleImportError:
   No module named 'redis'`——补上 `redis` 包。
3. **最花时间的一个**:`spark.jars.packages` 触发的 Ivy 依赖解析直连
   `repo1.maven.org`,cloud-full 云主机上会真的卡死不动(几百 MB 的
   `aws-java-sdk-bundle` 下载进度停在 0 字节超过 8 分钟),加
   `spark.jars.repositories` 指向阿里云镜像也没用(Ivy 默认解析器
   优先级不会因为多了候选源就绕开卡住的那个)——改成在镜像构建期
   (GitHub Actions,境外 runner)把三个 jar 下载好打进镜像,训练脚本
   自己建 SparkSession 指向本地 jar 路径(利用 `SparkSession.builder.
   getOrCreate()` "已有活跃 session 就复用、忽略新 config" 这条特性,
   让 feast 内部拿到的是这个已经配好本地 jar 的 session),运行时完全
   不联网。
4. Hive Metastore 的 NetworkPolicy(`allow-consumers-to-hive-
   metastore`)只列了 trino/spark-operator/airflow/feast 四个命名
   空间,没人想到 `argo-workflows` 读特征时也要连它拿 Iceberg 表元
   数据——和这个项目反复踩过的"新命名空间消费共享服务,NetworkPolicy
   忘记加白名单"是同一类坑,第 N 次复现,补上。
5. `mlflow.sklearn.log_model()` 报 `ModuleNotFoundError: No module
   named 'skops'`——和 `train_demo_model.py` 早就踩过、也修过的同一个
   坑(MLflow 3.x 默认序列化格式这个精简镜像没装),补
   `serialization_format="pickle"`。

**最终验证**:`train-from-feast-verify-7q7pv` 这个 Workflow
**Succeeded**,日志显示"从 Feast 取了 10 行历史特征,训练完成…已注册
进 MLflow Model Registry(demo-region-classifier)",`MlflowClient.
get_model_version()` 查询确认 `status: READY`。

---
## P2:交付方式的可靠性(角色能开工之后,立刻做这一批)

ADR-057 认定的结构性债务。不做的话,上面 P1 拉起来的东西会以同样脆弱的
方式继续堆叠。

### 2.1 引入镜像构建流程,停止用运行时 `pip install`(最大的一条)—— 全部完成(2026-08-20)

全仓库只有 1 个 Dockerfile,CI 没有任何镜像构建,而 **8 个地方在容器
启动时现装 Python 依赖**。仅 2026-08-16 一晚就因此产生四次真实故障
(Superset 被 SIGKILL 循环、platform-portal 卡 pip 导致流量一直走旧 pod、
换镜像源导致 `ModuleNotFoundError`、同一 manifest 不同时间部署得到不同
运行时)。离线/内网环境根本装不起来,这对"能原样上生产"是硬伤。

**已完成(2026-08-20)第一段:3 个自建 Flask 应用**(permission-request-app
/ table-registration-app / platform-portal)。每个应用新增
`Dockerfile` + `requirements.txt`(锁定版本),GitHub Actions
(`.github/workflows/build-images.yml`)在 push 到 main 且改了对应目录时
构建镜像、推到 GHCR,tag 是构建时的 commit SHA(不用会变的 `latest`)。
`deployment.yaml` 已切到 `ghcr.io/hardstuding/bigdata_ml_paltform/
<app>@sha256:...` 这种带 digest 的引用,本地 docker pull + 起容器
`/healthz` 验证过。ConfigMap 里的 `app.py` 已经不需要了(
`permission-request-app` 的 ConfigMap 现在只剩 `employees.csv` 这份
demo 数据,`table-registration-app`/`platform-portal` 的 ConfigMap 整个
删掉了),`scripts/sync-app-configmaps.py` 已退役并删除。

**GHCR 包可见性**:GitHub Actions 用内置 `GITHUB_TOKEN` 推送,推完实测
`docker pull` 匿名可用(这个仓库是公开仓库,包默认跟着公开),不需要
额外配置 imagePullSecrets。

**已完成(2026-08-20)第二段:iam-sync**。这个组件和 3 个 Flask 应用不是
同一种情况——它每次跑都要拿 `platform/iam/` 的**最新** git 内容,不能
像 Flask 应用那样把源码整个固化进镜像,固化的只有"装 git/kubectl/
pyyaml 这几个工具"这件事(之前 initContainer + 主容器各自现装一遍,
踩过 apt 卡死/delayed-item 重试队列等好几个坑)。**这个镜像的构建 CI
不换阿里云镜像源**——和 3 个 Flask 应用不同,是因为构建方从"cloud-full
云主机 / 本机 colima"变成了 GitHub Actions(境外 runner),直连官方源
(deb.debian.org/pkgs.k8s.io)反而更快更稳,实测换阿里云源在这个网络下
连不上,已经记进 `apps/iam-sync/Dockerfile` 的注释。**顺带修了一个多
架构构建的隐患**:build-images.yml 一开始只建 linux/amd64,但
local-lite(colima,arm64)也会拉这几个镜像跑同一批组件,iam-sync 的
镜像里打包了 kubectl——这个仓库已经真实踩过"amd64 kubectl 在 arm64
节点上用 QEMU 模拟执行,触发 client-go 并发 bug"这个坑(当年退役前的
initContainer 就是为了绕开它),只建 amd64 会把这个坑重新引入,已经在
写 iam-sync 镜像的同时把 build-images.yml 改成 `docker buildx` 建
`linux/amd64,linux/arm64` 两个平台(连带 3 个 Flask 应用的镜像也补齐了
arm64,之前只顾着验证 cloud-full 这一个环境,没考虑到 local-lite)。

**已完成(2026-08-20)第三段:Superset**。它其实 2026-08-19 就已经解决了
"运行时 bootstrapScript 装依赖"这个反模式本身(`apps/superset-image/
Dockerfile`),只是当时是手动登 cloud-full 云主机现场 build + 手动导出
本地缓存(`image-cache-amd64/`)——这次接进同一条 CI 流水线,不用再手动
操作。**这个 chart 的 image 字段不支持 digest**(`helm show values`
确认只有 `repository`/`tag`,不像自己写的裸 manifest 能用 `@sha256:...`
语法),改用构建时的 commit SHA 当 tag,GHCR 里同一个 SHA tag 不会被
覆盖,达到等价的可追溯性。云端验证过:新 Pod 正常拉取 GHCR 镜像、
`psycopg2`/`authlib`/`trino` 三个包 import 正常、ArgoCD Synced/Healthy。

**第四段也顺手做完了:argo-workflows-training-image**。接进同一条 CI
流水线,`workflow-template.yaml` 切到 GHCR digest 引用。**云端真实触发
过一次训练 Workflow 验证**(不只是部署/拉取):`kubectl create` 一个
`workflowTemplateRef` 指向 `train-demo-model` 的 Workflow,**Succeeded**,
确认新镜像不只是能拉,训练本身也真的跑得通。

**第五段(收尾):feast feature-server-image**。基础镜像是 RHEL UBI +
`microdnf`(和其它几个 Debian-based 的 Dockerfile 不是同一套包管理器),
接进去之前先用 quay.io 的 manifest list API 确认了这个基础镜像原生
支持 `linux/amd64`+`linux/arm64`(不需要 QEMU 模拟),多架构构建没有
额外风险。`apps/feast/manifests/feature-server.yaml` 和
`apps/airflow/dags/feast_materialize.py`(离线物化用的是同一个镜像)
都从 `imagePullPolicy: Never`(要求"这台机器之前手动 build 过"这个隐藏
前提)切到 GHCR digest 引用 + `IfNotPresent`。**云端验证**:新 Pod
拉取镜像成功,`pyspark`/`java -version` 都确认可用,真实调用
`/get-online-features` HTTP 接口拿到正确的 schema 响应(不是连接失败或
崩溃)。

至此**这个仓库所有自定义 Dockerfile 都接进了同一条 CI 自动构建流水线,
不再有任何"必须手动登云主机 build"的镜像**,BACKLOG 2.1 完全收尾。
**明确不做**:不引入 Kaniko/Tekton 这类集群内构建体系,对单人维护的
项目过重。

**云端验证已完成(2026-08-20 当天)**:cloud-full 的 ArgoCD 自动同步到
这版 manifest 后,三个应用的新 Pod 都正常拉到 GHCR 镜像并 Running(
`kubectl describe`/`.spec.containers[0].image` 确认 digest 和本地构建
的完全一致),`/healthz` 200,ArgoCD Application 状态 Synced/Healthy。
cloud-full 访问 `ghcr.io` 的网络连通性此前是真实未知项(之前只验证过
`docker.io`/`quay.io`/`registry.k8s.io` 这几个,没验证过 `ghcr.io`),
这次确认是通的,不再是假设。

### 2.2 "生成式单一源码"脚本的增殖 —— 部分消解(2026-08-20)

之前有 3 个脚本在实现同一个模式(`sync-app-configmaps.py` /
`sync-airflow-dags-configmap.py` / `render-environment-config.py`)。
2.1 完成第一段后 `sync-app-configmaps.py` 已退役删除,现在剩
`sync-airflow-dags-configmap.py`(Airflow DAG 还是 ConfigMap 挂载模式,
没有跟着改成镜像)和 `render-environment-config.py`(职责不同,不是
同一类,继续保留)。iam-sync 已经检查过:它本来就不是"源码塞
ConfigMap"这个模式(`scripts/12-sync-iam.py` 一直是运行时 `git clone`
拿最新代码跑,不是从 ConfigMap 读固定内容),这次镜像化没有牵扯到
任何同步脚本,不需要额外处理。

### 2.3 Trino livenessProbe 的人工补丁要么固化要么消除 —— 已完成(2026-08-20)

之前 `scripts/07-fix-trino-liveness-probe.sh` 必须在**每次** Trino pod
template 变更后重跑,否则回退到 chart 的坏默认值——这不是 GitOps,是
"GitOps 加一个没人会记得的手工步骤"。

**选的方案是 CronJob 轮询,不是 ArgoCD postSync hook**:trino 这个
Application 的 source 是远程 Helm chart(trinodb/charts),postSync hook
资源要和目标 Application 在同一次 sync 里渲染出来,官方 chart 没有
extraDeploy/extraObjects 这类扩展点插不进去。新增 `apps/
trino-liveness-fix/`(CronJob,每 5 分钟检查 livenessProbe 是不是已经是
期望的 exec 探针,不是才 patch,已经一致就不动)。**cloud-full 上真实
验证过完整闭环**:手动把 livenessProbe 打坏成 chart 默认的 httpGet →
手动触发一次 Job → 确认自动识别出不一致并重新 patch 好 → 再触发一次
确认变成 no-op。`scripts/07-fix-trino-liveness-probe.sh` 保留作为"不想等
最多 5 分钟巡检周期"的立即手动修复快捷方式,不再是必须的步骤。

### 2.4 三个自建 Flask 工具的测试补完 —— 已完成(2026-08-20)

主体 2026-08-16 完成(60 个测试接进 CI)。**剩余缺口 2026-08-20 补完**:
`apply_to_git`/`apply_grant_to_git`/`reclaim_expired`/`transfer` 里真正
执行 git clone/commit/push 的分支,现在用 `local_git_repo` fixture(起
一个本地裸仓库当 `REPO_URL`,不连真实 GitHub)覆盖;外部 OA webhook 的
真实 POST(成功标记 `pending_external`、失败退化回本地 `pending` 两条
分支)用 `monkeypatch.setattr(perm.requests, "post", ...)` 覆盖,不发
真实网络请求。三个应用现在合计 106 个测试(platform-portal 17 +
table-registration-app 29 + permission-request-app 60,后者这次从 52
补到 60)。

### 2.8 NetworkPolicy 白名单遗漏是个反复发作的 bug 类 —— 已加 CI 拦截(2026-08-21)

同一类 bug 在 5 天内复发了 5 次:2026-08-13 Trino 连不上 Hive Metastore、
08-14 Feast 连不上 Hive Metastore/MinIO、08-19 Argo Workflows 传 artifact
到 MinIO 失败、08-20 train-from-feast-features 连 Hive Metastore 被拒、
08-21 SeaTunnel 写 Iceberg 连 Hive Metastore 被拒。每次都是"新命名空间
消费了共享服务,但中心化的白名单里忘了加一行"。

**危险的地方在于发现得晚**:组件 ArgoCD 显示 Synced/Healthy,只有真的
跑一次那条数据路径才暴露。SeaTunnel 那次因为 DAG 长期暂停,
`docs/project/capability-matrix.md` 里"批量数据接入 ✅"这个结论挂了好几天,实际从来没通过。

已加 `scripts/check-networkpolicy-consumers.py` 接进 CI:扫描仓库里引用
共享服务 DNS 的文件,推断部署命名空间,和白名单对账。**自测过它真的会
报警**(临时从白名单删掉 feast,检查确实报出来了),不是写完没验证的摆设。

**但它有个必须说清楚的盲区**:只抓得到"直接引用"——某个命名空间的
manifest 自己写着共享服务 DNS。抓不到"间接连接":调用方只是往服务 A 提交
请求,真正去连共享服务的是 A 自己的 pod。**2026-08-21 SeaTunnel 那次恰恰
是这类**(DAG 通过 REST API 提交给 seatunnel-0,真正连 Hive Metastore 的是
seatunnel 命名空间的 pod,DAG 源码里没有 `namespace="seatunnel"`)——也就是
说这个检查当初拦不住促成它诞生的那个 bug。**检查通过 ≠ 网络路径通**,
真实端到端验证一次都不能少。

想彻底解决间接连接这类,得换个思路(比如从"按命名空间名枚举"改成"按
命名空间 label 选择",让消费方自己声明),那是架构级改动,单独评估。

### 2.9 alloy/loki 的 chart 源依赖境外巨型 index.yaml —— 已解决(2026-08-22,ADR-061)

**选了下面的方案 2:chart vendor 进仓库**(`platform/loki-chart/`、
`platform/alloy-chart/`),Application 的 `repoURL` 指向本仓库、`path` 指向
那两个目录,不再走 Grafana 的 Helm 仓库。见
[ADR-061](../decisions/061-vendor-grafana-charts.md)。

**2026-08-30 更正**:这一条一直挂在"未解决"里,而它 08-22 就做完了。下面
保留原始分析 —— 那部分(为什么 OCI 走不通、三个方案各自的代价)仍然有效,
以后再遇到同类问题可以直接照着判断。



**2026-08-22 实测量化过的问题,不是猜测。** 传统 Helm 仓库每次同步都要先
拉整个 `index.yaml`,`grafana.github.io/helm-charts` 那份超过 1.4MB,从
cloud-full 这台境内云主机实测下载速度约 **12KB/s**,单是这个文件就要两
分钟以上,直接打爆 ArgoCD 的执行超时。表现是 alloy/loki/
kube-prometheus-stack 三个 Application 长期 `Sync Status = Unknown`
(底层 Pod 一直健康,纯粹是比较逻辑拉不到 chart)。手动 hard refresh
三次、跨约 6 分钟四轮独立重试,100% 失败,每次都精确卡满超时——是持续性
的,不是网络抖动。

kube-prometheus-stack 已经解决:换成同一个组织发布的 OCI 仓库
(`oci://ghcr.io/prometheus-community/charts/kube-prometheus-stack`),
OCI 不需要 index,按名字+版本直接取,实测几秒钟拉完。

**alloy/loki 走不通同一条路**:Grafana 目前没有把 chart 发到 OCI 仓库,
`ghcr.io/grafana/helm-charts/alloy`、`ghcr.io/grafana/charts/alloy`、
`ghcr.io/grafana/alloy` 三个路径实测全是 403/not found。

可选方向(没做,需要先定):
1. 用仓库已有的 GitHub Actions(境外 runner)把这两个 chart 镜像到我们
   自己的 GHCR OCI 仓库。技术上直接(`helm pull` + `helm push`),但
   GHCR package 默认私有,要么手动改成 public,要么给 ArgoCD 配拉取
   凭据,多一层维护。
2. 把 chart vendor 进仓库(和 argo-workflows CRD 一样)。最确定、也最
   适合"生产可能没有外网"这个场景,代价是升级要手动同步。
3. 只调大超时忍着。最省事,但每次同步多花两分钟,而且速度再降一点就
   又会失效——不解决问题,只是把阈值往后挪。

**这条对上生产是实质性的**:生产大概率也在境内网络,任何依赖境外
GitHub Pages 巨型 index.yaml 的 Application,一旦需要真正重新同步
(升版本、改 values)就会卡死。

### 2.10 自建镜像在境内怎么拉 —— 已解决(2026-08-29,选了方案 1:阿里云 ACR)

**方案 1 已落地并实测**:CI 把 12 个自建镜像推到阿里云 ACR
(`.github/workflows/build-images.yml`,没配 secret 时自动跳过、不让流水线
变红),集群侧由 `scripts/45-configure-acr-pull.sh` 给每个命名空间配拉取
凭据(2026-08-30 起已在 `bootstrap-all.sh` 里)。

**实测对比**:同一个 3.44GB 的镜像,GHCR 上 `docker pull` **25 秒 0 字节**,
ACR 上 **1 分 59 秒拉完**。

踩到并修掉的一个坑:buildx 的 `provenance`/`sbom` 会额外推一个
`application/vnd.oci.empty.v1+json` 的 attestation manifest,**阿里云 ACR
个人版不认这个类型**,报 `denied: unknown manifest class`。关掉那两个开关
即可(GHCR 认,所以会出现"GHCR 成功、ACR 失败"这种一半一半的现象)。

**2026-08-30 更正**:这一条一直挂在"需要定方案",而方案 08-29 就定了并
验证过了。下面保留原始分析 —— 那部分(为什么 DaoCloud 白名单盖不住自建
镜像、digest 固定和 `docker tag` 互斥)仍然有效。



**⚠️ 先更正一个我自己测错的数字。** 这一条最初写的是"直连 ghcr.io 约
80KB/s、镜像站 2.3MB/s",那组数字**是错的**:测法是看 `/data/docker` 的
增长,但这台机器的 Docker 29 用的是 **containerd 镜像存储**,层数据落在
`/data/containerd`,`/data/docker` 基本不动。按错的目录量出来的速度自然
接近零。留着这段是因为"用错误的观测指标得出结论"本身就是个值得记住的
坑——数字看起来很确定,不代表量对了东西。

**重新测过的真实情况(2026-08-22 夜)**:

| 路径 | 实测 | 说明 |
|---|---|---|
| `ghcr.nju.edu.cn`(南大镜像站) | **5.5 MB/s** | 1244MB 的自建镜像 3 分 45 秒拉完,**digest 与 GHCR 官方完全一致** |
| `docker.m.daocloud.io` | 可用 | 但**有白名单**,只代理知名上游;自建包被拒:`this image is not in the allowlist` |
| `ghcr.io` 直连 | 慢(未精确测) | Flink Operator 镜像实测 wall-clock 约 14 分钟 |

**真正的结论不是"境内拉不动"**,是:
1. **DaoCloud 白名单盖不住自建镜像** —— 这是 `scripts/23` 的真实覆盖边界,
   之前脚本注释里说"覆盖 69 个里的 66 个"没算上我们自己的包。
2. **digest 固定和"镜像站拉了再 `docker tag` 打回原名"互斥** ——
   `docker tag` 不能给 digest 引用打标签。目前 Flink/Producer 两个镜像
   因此改用 commit-SHA 标签(见 ADR-062)。
3. `ghcr.nju.edu.cn` / `ghcr.linkos.org` 这类无白名单代理可用,但**引入
   第三方代理必须核对 digest**(这次核对过,一致),而且可用性不由我们
   控制。

**需要定的方案(多节点演练的硬前置)**:

1. **把自建镜像同步一份到境内 registry(阿里云 ACR)** —— 生产上最正确
   的答案,ECS 同地域拉是内网速度。需要用户提供 ACR 凭据配进 GitHub
   Actions。**推荐这条。**
2. 把 `ghcr.nju.edu.cn` 加进 `scripts/23` 的映射表,作为 DaoCloud 盖不住
   时的兜底。便宜、今晚就验证可行,但依赖第三方善意。
3. 给云主机换 containerd 直连(k3s 默认就是 containerd,这台是特意用了
   `--docker`),containerd 支持按 registry 配 mirror,能同时保住 digest
   固定和加速。改动面大,要重新评估当初选 `--docker` 的理由。

3 台新机器 × 全量镜像,方案没定之前不要租。

**2026-08-28 实测,这条从"以后再说"升级成"已经在挡路了"**:升 Spark 4
之后镜像大了 350MB(AWS SDK v2 的 bundle 一个层就 570MB),四个新镜像
在云主机上**全部 ImagePullBackOff**——旧 pod 还在跑所以没断服务,但新
版本根本上不去。当天量到的数字:

- kubelet 拉:`Failed to pull image ... context canceled`(进度超时打断)
- `docker pull` 直连 ghcr.io:**25 秒 0 字节**,`/data/docker` 完全不增长
- `ghcr.nju.edu.cn`:manifest 拿得到(HTTP 200),但拉 blob 30 秒超时,
  **兜底方案 2 这次不成立**
- 同一时间小镜像(platform-portal,约 100MB)拉得下来——不是 GHCR 挂了,
  是大 blob 过不去

**2026-08-29 已解决**:CI 同时推 GHCR 和阿里云 ACR(个人版,杭州),集群
侧配 `acr-pull` 凭据并把清单里的自建镜像地址切到 ACR。实测对比同一个
3.44GB 的 spark-iceberg 镜像:

| 源 | 结果 |
|---|---|
| GHCR | `docker pull` **25 秒 0 字节**,kubelet 拉取超时被打断 |
| ACR | **1 分 59 秒拉完**(1.39GB 压缩后) |

小镜像(platform-portal,60MB)从 ACR 是 7.5 秒。

踩到的两件事记在 `docs/operations/image-registry.md`:buildx 默认推的
provenance 证明清单用 `application/vnd.oci.empty.v1+json`,ACR 个人版不认
(`denied: unknown manifest class`),要 `provenance: false`;个人版一个账号
只有一个实例、地域跟着实例走,跨地域就走不了 VPC 内网(但境内到境内仍然
远快于跨境)。

`scripts/38-ship-image-to-cloud.sh` 那条手工搬 tar 的路留着当兜底,不再是
日常路径。

### 2.5 扩大 CI —— 持续在做,2026-08-30 又加了 5 条

现在 CI 里的检查(`.github/workflows/validate.yml`):chart 渲染校验、
DAG / ConfigMap / 应用源码 三种单一源码防漂移、环境配置渲染防漂移、
镜像 tag 不许浮动、NetworkPolicy 消费方、资源规格、IAM 一致性、
服务目录、文档死链、文档里的 SDK 示例、prod 不许出现开发域名、
OPA 策略单测、四个应用的单元测试、render-jobs/render-streams 防漂移。

**2026-08-30 新增 5 条,每条都对应一次真实踩过的坑**:

| 检查 | 拦的是什么 |
|---|---|
| `check-capability-matrix.py` | 能力表里 ✅ 的行写"未验证";缺口文档自己记状态 |
| `check-bootstrap-coverage.py` | 一键脚本和文档的部署主线表不一致(加的时候发现文档漏了 11 步) |
| `sync-adr-index.py --check` | ADR 索引的状态和 ADR 原文分叉(加的时候发现 10 条过期) |
| `list-manual-credentials.py --check` | 有 Secret 被引用却没人创建、也没登记进"必须人工提供" |
| `check-doc-commands.py` | 文档里让人跑一个不存在的脚本(加的时候抓到 1 条) |

**还没做的**:真正的集成测试(在 CI 里起一个临时集群跑端到端)。代价很大,
而目前"实机验收脚本 + 黄金链路探针"这条路的性价比更高 —— 前者在真集群上
跑 28 条,后者持续回答"现在还成不成立"。

### 2.6 notebook 里直接调 `submit_job()` 被 singleuser NetworkPolicy 挡住 —— 已解决(2026-08-20,之前的"未解决"是记录错误)

2026-08-19(ADR-058 第一批验证)当晚查到 NetworkPolicy 该加一条什么规则
才能放行 notebook pod 连 K8s API server(k3s 的 API server 不是普通
pod,是节点自己起的,Service 的 Endpoints 指向节点 IP 不是 pod
IP——namespaceSelector/podSelector 匹配不到,只能用 ipBlock 精确放行
节点私网 IP + 6443 端口),规则写进了 `apps/components/jupyterhub.yaml`
(`ipBlock: 172.22.9.16/32` port 6443),但**当晚会话在触发验证之前就
结束了**,写commit 时如实标注成"没有验证通过,是已知未解决的限制"。

**2026-08-20 补验证:这条规则从一开始就是对的,不是"未解决"**——起一个
带 `singleuser-server` 标签、挂 `platform-sdk-submitter` ServiceAccount
的 debug pod(模拟真实 notebook pod 的网络身份和权限,不用真的登录
JupyterHub),`submit_job()`(建 Workflow→pod 里跑
`print("hello from notebook-labeled pod")`→Succeeded)和新增的
`run_workflow_template("train-demo-model")`(见下面 P1.7)都端到端
成功,`job_status()` 也能正确查到状态。教训:上一次的"已知限制"记录
本身没有错(如实记录了"没有验证通过"这个事实,不是编造),但下一次
真正有机会验证时,应该优先重新验证已标记"未解决"的东西,不要假设它
还是老样子——这条被漏验证了快一整天,如果不是这次顺手连带测了一下,
可能会继续被当成"已知限制"存在很久。

**当前状态**:notebook pod 里可以直接调 `submit_job()`/
`run_workflow_template()`,不需要 `platform-submit job.yaml` 这条
"从终端/CI 提交"的权宜做法了(那条依然能用,只是不再是必须的)。

### 2.7 cloud-full 节点上 k3s 内置 Traefik 一直没关 —— 已完成(2026-08-20)

排查其它问题时顺手 `kubectl -n kube-system get svc,ds` 才发现:k3s 默认
自带的 Traefik ingress controller 从来没有被显式禁用过(`k3s server`
没加 `--disable traefik`),`svclb-traefik-*` 这个 DaemonSet Pod
`2/2 Running` 了整整 4 天,占着节点的 80/443 端口,和这个项目真正用的
ingress-nginx 自己的 `svclb-ingress-nginx-*` 抢同一组端口——后者因此
一直 `Pending`(`FailedScheduling: didn't have free ports`),这也是
`ingress-nginx` 这个 ArgoCD Application 长期显示 `OutOfSync` 的一部分
背景噪音(另一部分是下面这条独立的旧同步错误)。

**已确认不影响真实访问**(修复前):外部访问路径是 NodePort
(`32460`/`32535`),不是靠 LoadBalancer 占的裸 80/443。

**已修复(2026-08-20,征得用户确认后现场处理,不是"顺手"做的——涉及
节点级操作,单独走了一轮确认再动手)**:
1. `/etc/rancher/k3s/config.yaml` 加 `disable: [traefik]`,
   `systemctl restart k3s`(单节点集群,重启期间控制面短暂不可用,
   已有 Pod 不受影响,重启后节点几秒内恢复 Ready)。
2. `scripts/21-bootstrap-cloud-vm.sh` 的 `INSTALL_K3S_EXEC` 同步加上
   `--disable traefik`,以后从空环境重新拉起这台节点不会再漏掉。
3. **验证**:重启后 `kubectl -n kube-system get svc,helmchart` 确认
   Traefik 的 Service/HelmChart CR 都已消失;之前一直 `Pending` 的
   `svclb-ingress-nginx-*` Pod 变成 `2/2 Running`(端口冲突解除的直接
   证据,不是推断)。

**顺带修好了一条独立的旧问题**:`ingress-nginx` 这个 Application 的
`status.conditions` 里挂着一条 2026-08-16 的 `SyncError`
(`validatingwebhookconfigurations.../ingress-nginx-admission` 的
`resourceVersion` 冲突),和 `docs/operations/troubleshooting.md` 里
"ArgoCD 卡在过期的同步操作上"是同一类问题——按那份文档的处置方式(先
`kubectl patch application ... operationState.phase=Terminating` 终止
卡住的旧操作)没能一次解决,深挖发现是 `argocd-application-controller`
自己的对象缓存也过期了,额外 `kubectl rollout restart
statefulset/argocd-application-controller` 清缓存,再删掉那个孤立的
`ValidatingWebhookConfiguration` 让 ArgoCD 重新创建,才彻底解决。现在
`ingress-nginx` 是 `Synced Healthy`,不再有任何已知的 OutOfSync 项。

---

## P3:打磨已有角色能力(不需要新组件,是体验问题)

- **两条链路的 Iceberg warehouse 前缀不一致**(2026-08-30 做 iceberg-backup
  时发现):审计 sink(`scripts/flink_trino_audit_sink.py`、
  `streams/device-events/job.py`)配的是 `s3a://lakehouse/warehouse`,推理
  留痕 sink(`streams/inference-log/job.py`)默认的是 `s3a://lakehouse/`,
  而 `apps/hive-metastore/manifests/core-site-configmap.yaml` 里写的也是
  `s3a://lakehouse/`。结果是同一个 lakehouse 桶里,一部分 schema 落在
  `warehouse/xxx.db/`,另一部分会落在 `xxx.db/`。
  **现在的影响是"任何按路径操作对象存储的东西都得两个位置都找"**
  ——`apps/iceberg-backup` 已经这么绕过去了,但这是绕,不是修。
  真正的修法是把 warehouse 前缀收敛成一处配置(渲染时注入),让所有
  sink 引用同一个值。**目前没有数据损坏风险,所以没排进 P0/P1**;等
  `ml` schema 真的有数据了再动,免得改路径导致已有表找不到。

- **告警送不到人** —— **机制那一半已验证([ADR-081](../decisions/081-alert-delivery-verified-with-echo-sink.md))**:
  告警真的被 POST 出去过,终点是集群内的 alert-echo-sink,能回看 payload。
  **还差的只是真实渠道地址**(企微/飞书/邮件),换渠道 = 改
  `monitoring/alertmanager-webhook` 这一个 Secret 的 url。
  按 使用方明确要求,真实渠道等上生产再接。
- **排障知识 Runbook 化**——2026-08-20 补了第一步:按症状类别分组、带
  锚点链接的真实索引(之前 `## 索引` 这个标题底下是空的)。**还没做的**:
  每条正文条目本身的"现象→原因→处置"结构不够统一(大部分条目已经有
  这个结构,但格式不是强制统一的,有的条目掺了排查过程叙事),真正的
  Runbook 化需要重新过一遍每条、统一格式,这次没做——838 行内容较大,
  一次性重写风险不小(容易在改写措辞时不小心丢失细节),留到有真实
  动机(比如真的按索引查找时发现某条不好用)时再逐条打磨,不批量重写。
- ~~**Superset 汉化**~~ **已完成(2026-08-28,[ADR-077](../decisions/077-superset-chinese-ui.md))**:实机验证 4054 条翻译生效。
- ~~**dbt 接 Airflow 编排 + OpenMetadata 摄入**~~ **已完成**:接 Airflow
  2026-08-21 就做了(`dbt_demo` DAG,刻意没用 Cosmos,理由见 DAG 顶部注释);
  OpenMetadata 的 dbt 血缘摄入 2026-08-29 做完并实机验证
  ([ADR-082](../decisions/082-dbt-lineage-ingestion.md)):血缘接口查得到
  `orders -> stg_orders -> daily_order_totals` 两条真实的边。
- **公网域名 + TLS 接入**:zhenghe 2026-08-16 明确的方向——"域名走配置化
  生效,配置 test 起来就是可临时访问的;未来配置 prod,就强制需要配置
  一个域名"。`test` 类环境允许没有真实域名(继续 NodePort + `/etc/hosts`);
  `prod` 应**强制**要求真实域名 + TLS,是校验层面的硬要求,不是建议。
  scheme(http/https)的配置化 2026-08-16 已经做完(`external_scheme`),
  剩下的是域名注册 + ICP 备案(中国大陆服务器强制,1-20+ 工作日,
  **需要 zhenghe 亲自做身份核验**)和 ingress-nginx/cert-manager 按环境
  分叉的设计,真正做时单独出一份 ADR。

---

## P1.5:外部评审(2026-08-29,Codex)提出的第二~四批 —— 已完成

**2026-08-30 实机验收:`./scripts/46-verify-p15.sh` 28 条全过、0 失败**
(1 条跳过:权限门户里没有申请记录,看不出状态中文化)。六条全部落地并
在 cloud-full 上验证过。

**还没验到的三条**(都要真人浏览器,脚本模拟不了):用两个真实账号验越权
(A 打不开 B 的作业详情)、组权限申请的批准按钮、作业详情页的外观。

> **这一轮最值得记的不是"六条都通过了",是验收脚本自己有三个 bug** ——
> 拿空字符串当日志判断(一条假阳性一条假阴性)、`| while read` 子 shell
> 把失败记账吞掉、断言 302 而 urlopen 默认跟随重定向。外加用
> `flask_login.login_user` 写测试差点把好功能报成坏的。详见
> [`capability-matrix.md`](capability-matrix.md) 底部那节。

第一批(4 条确定性缺陷)当天已全部修完并有测试,见
[`current-work.md`](current-work.md)。下面是**剩下三批**,每条带验收条件
——不写"后续优化"这种没法验收的说法。

### 分析师的浏览器 SQL 入口 —— ✅ 2026-08-30 实机验证通过

**问题**:现在把 Trino Web UI 当 SQL 工作台介绍,而它只是个查询监控界面。
**验收**:分析师能在浏览器里编辑 SQL、执行、看历史、下载结果、看到可读的
错误说明,并能从数据目录里的一张表一键跳到查询。

**2026-08-29 进展**:候选方案和退出方案记进 [ADR-084](../decisions/084-analyst-sql-workbench.md)
——选**复用 Superset SQL Lab**,不引入新组件(SSO、角色、impersonation 这四层
里最难的三层已经是通的)。门户已改:Trino 那张卡不再自称能写 SQL,新增
「SQL 工作台」卡,新增 `/query/<catalog>/<schema>/<table>` 给数据目录当落脚点。

**还差三件事,一件都没做完就不能标绿**:
1. **SQL Lab 里的 Trino 连接没单独验过 impersonation**(看板路径验过,SQL Lab
   走同一个 database 连接,但"应该一样"不算验证)。下次开机验:`analyst001`
   登录 → SQL Lab → `SELECT current_user` 要是他本人,查没 grant 的表要被拒。
2. permalink 深链的服务账号还没配(现在是降级成空的 SQL Lab,有测试锁住)。
3. OpenMetadata 表详情页上的跳转按钮没做。

### 门户升级成角色工作台 —— ✅ 2026-08-30 实机验证通过

**问题**:现在是一页工具链接 + 几张状态表,所有角色看到的东西一样。
**验收**:普通用户首页能看到我的作业/我的权限/即将过期的权限;审批人能看到
待审批和超时事项;点进某个作业能看日志、参数、镜像、资源、失败原因,并能
取消和重跑。底层组件不再对所有角色一视同仁地暴露。

**2026-08-29 做完的**(权限那一半):

- permission-request-app 开了两个只读接口:`/api/my-permissions`、
  `/api/my-approvals`。**必须带 `user` 参数**,不带就 400 —— 一个不带用户就
  返回全量的接口等于给门户开了越权读取的口子。
- 门户首页新增「我的表权限」(快到期的排最前、单独标黄)和「待我审批」
  (显示已等多久、超 48 小时标超时)。不是审批人就不显示第二块。
- **权限服务不可用时整块不显示,不是整页 500**,有测试锁住。
- 跨命名空间那两个坑都提前查过了:NetworkPolicy 里
  `allow-portal-probe` 已经放行;Secret 用 `copy_secret` 复制**同一份**
  token 过去(各生成一份的话表现是"首页那两块永远空着"、不报错)。

**2026-08-29 做完的**(作业详情那一半):`/job/<name>` —— 失败原因摆在最
上面(打开这页最常见的理由就是它挂了)、每一步的状态、按需拉取的日志、
镜像/命令/资源/队列/参数,以及取消和重跑。

**这块改动扩大了门户的 RBAC,所以边界单独说清楚**(写在 `rbac.yaml` 顶部,
不是某个 ADR 里):门户是所有登录用户都能打开的页面,它 ServiceAccount 的
权限就是"任何能登录的人间接能拿到的权限"上限。两层收口:

1. **RBAC 给最窄的动词** —— 取消用 `patch`(打 `spec.shutdown=Terminate`),
   **不给 `delete`**;重跑用 `create`。delete 意味着任何登录用户间接能删掉
   workflow,连带丢失这次运行的全部记录。
2. **应用层按归属收口** —— 每个入口(含日志)都先确认
   `platform-sdk/submitted-by` 等于当前登录用户。**日志一样严**:别人作业
   的日志里可能有他打印出来的敏感数据。日志接口还额外校验 pod 名确实属于
   这个 workflow,否则它就成了"读 argo 命名空间下任意 pod 日志"的入口。

**第 2 层的前提是 `X-Forwarded-User` 不可伪造**,而那靠 platform-portal 的
NetworkPolicy 只放行 oauth2-proxy 连 8080。**那条 NetworkPolicy 被去掉,
这里就是个越权入口** —— 这句话写进了 rbac.yaml,不是留在 review 记录里。

**"底层组件不再对所有角色一视同仁地暴露"2026-08-29 也做完了**:分类级
的可见性规则(运维/身份只给 platform-team,治理再加 data-analysts,其余
分类所有人可见)。**这不是权限控制,是降噪** —— 真正拦得住的是每个组件
自己的 SSO 和 OPA。门户是新人进平台看到的第一个页面,那一屏决定他觉得
这套东西"能用"还是"太复杂"。

规则按分类写不按工具写:按工具写的话每加一个工具都要记得改那张表,漏了
就是新工具对谁都不显示。**拿不到组信息时显示全部**,理由见下面那段。

### 作业发布从单文件升级成可维护流程 —— ✅ 2026-08-30 实机验证通过

**问题**:`jobs/` 和 `streams/` 目前是"一个脚本 + 一份 yaml",没有多文件项目、
依赖锁定、镜像构建、参数化重跑、补数、晋级。

| 验收项 | 状态 |
|---|---|
| 多文件项目 | ✅ 作业目录下所有 `.py` 一起挂进容器,`import jobkit` 直接可用。**不支持子目录**,理由见 `jobs/README.md` —— 要多层结构就该打成内部包(ADR-083),而不是在 ConfigMap 上模拟文件系统 |
| 依赖锁定 | ✅ `requires:` 和 `apps/platform-image/requirements.txt` 对账,写了镜像里没有的包 **CI 直接红**。**平台不在运行时装任何东西**(那是记过的反模式)。依赖也顺势从 Dockerfile 的续行搬进了 requirements.txt —— 校验的可信度取决于清单可不可靠 |
| 参数化 / 补数 | ✅ `params:` → Argo workflow parameter + `PARAM_<名>` 环境变量。补数 = `argo submit --from cronwf/x -p run_date=...`。没有参数的话,重跑日更作业只会再算一遍今天 |
| dev/test/prod 晋级 | ✅ `environments:` 列表,**晋级就是加一个环境名**,不是复制 yaml。校验对所有作业都做,不管它在不在当前环境 —— 否则"只在 prod 生效"的作业能一直绕过检查 |
| owner_group 可信身份绑定 | 🟡 机制做了(拿作业目录最后一次提交的 git 作者去对账 `memberships.csv`),但**今天不生效**:真实提交邮箱是个人邮箱、`employees.csv` 是占位数据,每次都走"拿不到身份 → 放行"。**每次运行会把跳过的作业打印出来**,不让它变成又一个静默走 else 的检查。接真实 HR/IdP 之后自动生效 |
| 不用理解 ConfigMap / Argo YAML | ✅ 写 `job.yaml` + `.py`,`jobs/README.md` 里没有一处要求读 Argo 文档 |
| 镜像构建(每作业一个镜像) | ❌ **没做,而且暂时不打算做**:现在的路径是"依赖进平台镜像"。每作业一个镜像意味着每个作业都要走一次 CI 构建 + 推仓库,对一个几十行的日更脚本是不成比例的。真需要独立依赖的作业,`image:` 字段本来就可以指定 |

**阻塞项**:owner_group 那条要真实 HR/IdP 数据才能生效,那是 zhenghe 提供
对接方的事。

### 建表注册工具 —— ✅ 2026-08-30 实机验证通过

**验收**:字段表单(含字段说明)、分区、生命周期、质量规则、提交前预览;
负责人不能任意冒充;明确哪些角色可直接建表、哪些要审批;OpenMetadata 回写
失败要有对账和重试,不允许长期停在"Trino 有表但目录里没有"的半成功状态;
页面上去掉 Phase/ADR 这类内部术语。

**2026-08-29 做完**:

- ✅ **负责人不能冒充** —— 这不是体验问题,是一条**真实可走的提权路径**:
  owner 原来是自由填写的表单字段,而表负责人在 permission-request-app 里是
  **第一级审批人**,所以"建表填自己 → 之后申请这张表的权限 → 自己批自己"
  是通的;组织架构里查不到上级的人,那条链上甚至只有他一个人。两层都堵了:
  建表端 owner 一律取登录身份(表单值不看),审批端加了兜底 —— **申请人
  永远不会出现在自己的审批链里**,剔完如果没人了就拒绝,不是放行。
  一条旧测试当时正好在断言这个漏洞(登录 zhenghe、表单填 someone、期望
  用 someone),已更正并说明原因。
- ✅ **对账和重试** —— `/internal/reconcile-openmetadata` + 每 30 分钟的
  CronJob,外加一个只读的 `/internal/reconcile-status` 报"还有几张卡着"
  (这个数字长期不为零就是有表在目录里隐形,可以拿它做告警)。
- ✅ **去掉内部术语** —— 页面上那句"权限 OA 审批系统 Phase 1……见 ADR-043"
  换成对使用者有用的话;OpenMetadata 里那个安全等级字段的说明也一样。

**2026-08-29 晚补完表单那几项**:

- ✅ **字段说明** —— `列名 类型 # 说明`,写进 Trino 的 COMMENT 和目录的
  description。旧的两段式格式仍然有效。
- ✅ **分区** —— 列名或 `year()/month()/day()/hour()/bucket(列,N)/truncate(列,N)`。
  **白名单校验,不靠转义**(这个字段最后要拼进 DDL)。
- ✅ **质量规则** —— 建**真的** OpenMetadata 断言(行数不为零 / 主键不重复 /
  关键列不为空),API 形状和 `scripts/34` 用的是同一套(那份是在真集群上试
  出来的)。**只给三条,不做成规则引擎**:断言的价值在于有人看、有人管,
  一开始就给二十种选项,结果是每张表挂一堆没人维护的检查,红了也没人理 ——
  学会忽略红灯比没有灯更糟。
- ✅ **提交前预览** —— `/preview` 用**和真正建表同一份 `build_ddl`**。预览
  显示一段 SQL、实际跑另一段,比没有预览更糟,所以这条有测试锁住。

**顺带修了一个自打脸的 bug**:表单里给的示例是 `amount DECIMAL(10,2)`,
而 DECIMAL **不在类型白名单里** —— 照着示例填的人第一次提交就会被打回。

**生命周期(数据保留期)刻意没做。** 要做成真的,得有一个按期删数据的作业;
而只把天数记进目录、不真的执行,就是这个仓库明确反对的"留一个不生效的开关"
(和 KServe 那个 `canaryTrafficPercent` 同一类)。要么实现执行,要么不提供,
不做中间态。

**"哪些角色可直接建表、哪些要审批"当天晚些时候也补上了**:1 级表谁都能直接
建,2 级起要先走审批(平台组除外)。

**规则按安全等级切,不按人切**,理由值得写下来:建表是日常工作,一律卡在
审批上只会逼人绕过平台直接连 Trino 写 DDL —— 那样建出来的表在数据目录里是
**隐形的**,查不到、没有负责人、没有安全等级,比"没有审批"糟得多。平台组
不受限是因为他们本来就有直连 Trino 的能力,拦他们只是让他们绕路。

被挡住时会落一行 `rejected` 记录,写清楚下一步该去哪 —— **这个工具不长出
第二套审批**,审批流程在 permission-request-app 那边。

**建表工具这一项到此全部做完**,只差实机验证。

**"代他人建表"当天晚些时候补上了**:platform-team 的人可以指定别人当负责人,
其他人不行。做法是给 Keycloak client 挂 **default** client scope(不请求也会
带上),所以 oauth2-proxy 那边**不需要**在 scope 里加 `groups` —— 请求一个
client 没配的 scope 才会 `invalid_scope`(MLflow 2026-08-19 就是这么炸的)。
这条区分是这次能安全做成的关键。

**一个刻意的不对称**:拿不到组信息时,门户**显示全部**,建表工具**按"不能
代建"处理**。同一个不确定状态,两处方向相反,依据是"错的那一边代价多大"
—— 门户多显示几个进不去的入口没有代价,建表那边放过去就是一个越权写入。

### 审批体验 —— ✅ 2026-08-30 实机验证通过

| 验收项 | 状态 |
|---|---|
| 状态中文化 | ✅ `STATUS_LABELS` + `\|zh` 过滤器。最要紧的是 `approved_pending_apply`:字面像"批了",实际是"批了但权限还没生效" |
| 时间按用户时区 | ✅ 服务端只输出 UTC + `<time class="lt">`,由页面 JS 按**浏览器自己的时区**换算(服务端不知道用户在哪,猜不如让浏览器算);3 天内显示相对时间,悬停看精确值 |
| 理由按敏感等级必填 | ✅ 2 级起必填、至少 10 字。**1 级不强制**是有意的:低敏表强制写理由只会逼出"查数"这种占位文字,反而稀释了真正需要理由的场合 |
| 批准/拒绝支持意见 | ✅ 存进 `approval_steps.comment`,申请人看得到 |
| 拒绝原因必填 | ✅ 服务端校验(前端 required 直接 POST 能绕过)。没有原因的拒绝对申请人是一堵墙 —— 他只能原样再申请一次,然后再被拒一次 |
| 到期提醒 | ✅ 回收前 7 天企微提醒。**这是这套机制最伤人的地方**:授权悄悄失效、OPA 5 分钟跟着生效,人第二天发现查不到数据,第一反应是"平台坏了" |
| 催办 | ✅ 申请人主动提醒当前这一级,24 小时限频。和超时升级是两件事:升级是系统越过人往上找,催办是申请人说"我还在等" |
| 续期 | ✅ 但**不是"把到期时间往后推"**。那样等于把 180 天复审变成形式 —— 授权设期限的意义就在于"过一段时间要有人重新看一眼这个人还需不需要"。所以续期走的是和第一次申请**完全相同的审批链**,理由从上一条带过来、标上 `[续期]`。它省的不是审批,是"等到查不到数据才想起来":门户首页把快到期的排最前、标黄、给一个续期入口 |

**阻塞**:真实 HR/IdP 对接需要 zhenghe 提供对接方,占位组织数据不能标成
生产完成。

### 文档职责重构 —— ✅ 2026-08-30 做完

- ✅ **能力表**只留状态 + **验证级别** + 最后验证时间 + 证据链接,叙述搬进
  `capability-matrix-archive-2026-08.md`。加了 `check-capability-matrix.py`
  进 CI,卡住历史失效模式:状态 ✅ 的行不许把验证级别写成「未验证」/「计划中」。
- ✅ **`current-work.md` 收敛回一页**(316 → 164 行),8 个「这一轮」搬进
  `docs/journal/2026-08.md`。判断标准改成可执行的一句:超过 ~150 行基本就是
  又开始写日记了。
- ✅ **使用指南按角色和任务拆分**,每节统一成 前置条件 / 操作 / 预期结果 /
  常见失败。**过程中发现并改掉一条反着说的安全描述**(旧版告诉用户"权限门户
  不做真正的查询拦截",而 OPA 强制、列级脱敏、行级过滤 08-26 就都验过了)——
  这正是"同一能力不能在两份权威文档里状态矛盾"这条验收要卡的东西。
- ✅ **验证级别标注**:每项能力标 生产验证 / 集成验证 / demo / 未验证 / 计划中。
  加上这栏之后一个事实直接摆出来了:**全平台没有任何一格是「生产验证」**。
- 🟡 **Runbook 六段结构**:`backup.md` 和 `onboarding-offboarding.md` 已改成
  触发条件 / 影响 / 前置检查 / 操作 / 验证 / 回滚。剩 `upgrade.md`、
  `multi-node-rehearsal.md`、`tuning.md` 没改。
- ✅ **2026-08-30 补的一批**(起因是 zhenghe 提"文档一定要写好,要能基本
  实现一键拉起,其他人、AI 能看懂"):
  - 新增 [`operations/deploy-from-scratch.md`](../operations/deploy-from-scratch.md)
    —— 假设读者对项目一无所知,从"要先准备什么"到"怎么确认真的能用"。
  - `scripts/README.md` 的部署主线表补全成 **27 步真实执行顺序**(此前漏
    了 11 步),并有 CI 保证它和 `bootstrap-all.sh` 一致。
  - 一口气改掉 5 处**过期到会误导判断**的状态断言:README 说三个组件
    "未部署"、architecture.md 说 "Trino 现在零访问控制"、ADR 索引 10 条
    "未部署验证"、production-readiness-gaps 落后四五天、CLAUDE.md 的
    "已知差距"有两条已不成立。
  - **每一处都不是"更新一遍"就完**:能删掉重复维护的就删掉
    (architecture 的三列环境启用状态、gaps 文档的状态标记),删不掉的
    就改成生成 + CI 防漂移(ADR 索引)。更新一遍只会在几天后再次过期。

  **`troubleshooting.md` 这 1842 行、66 个条目刻意不套这个结构** —— 它是
  「症状 → 定位 → 处置」的**排障手册**,不是操作手册。把"回滚"这一段硬安到
  "Trino 起不来怎么查"上面,只会产出填充文字,还会破坏它顶部那份 59 条症状
  索引的可扫性。六段结构适用的是**计划中的操作**(备份恢复、人员权限变更、
  组件升级、多节点演练),那几份已经改了或列在上面。

### 生产就绪度的诚实收口

**在下面这些完成之前,不得对外宣称"生产可用"**:多节点部署与故障切换演练、
MinIO 分布式方案及 Iceberg/MLflow 制品的备份恢复、反亲和/拓扑分散/PDB/
存储类验证、真实外部 IdP 接入、真实告警渠道、数据保留与 Iceberg snapshot
过期/孤儿文件/小文件合并、RPO/RTO/SLO 及恢复演练证据、组件升级与回滚演练。

这一条**不是待办清单,是一条门禁**:`capability-matrix.md` 里任何一格标成
"生产验证"之前,要能指出上面对应项的证据。

---

## P4:五条面向角色的产品主线(长期,都还没开始)

完整方案见 `docs/architecture.md` "Phase 4 之后"一节和原始评审
`docs/project/reviews/2026-08-15-external-review.md`。

**这张表 2026-08-30 重新核对过**,上一版(08-28)有 5 处已经不成立。
**逐条的权威状态在 [`capability-matrix.md`](capability-matrix.md)**(那份有
验证级别和证据链接,还有 CI 检查);这里只回答"这条主线整体走到哪了"。

| 线 | 状态 |
|---|---|
| A 统一开发工作台 | 🟡 作业模板 + `platform-submit --new` 脚手架;**CI/CD 那半 2026-08-29 已补上**(`jobs/` 写一行 `schedule` → CronWorkflow,支持多文件/依赖对账/参数化补数/按环境晋级,08-30 实机验证)。**还缺**:每作业独立镜像、多层目录结构 |
| B 数据资产与治理闭环 | 🟡 权限执行 ✅、审计闭环 ✅、数据质量 ✅、dbt 血缘 ✅(ADR-082);**2026-08-30 补上从 Trino 查询历史自动推血缘**(`scripts/47`,OpenMetadata 自带的 `DatabaseLineage`,**不需要人工声明 inputs/outputs**,**2026-08-30 实机验证通过**:血缘接口查到 `demo.orders -> lineage_probe_...`,从查询历史自动推出来的)。**还缺**:变更影响分析(有了边之后才谈得上);**Spark 血缘的 artifact 要重选** —— 2026-08-30 核实 ADR-014 选的 `openmetadata-spark-agent` 只有 2024 年的 `1.0-beta`(Java 11 + OpenLineage 1.7),不认 Spark 4,**没有盲目试**(类加载不到会直接打死现在能跑的批处理链路);以及一个已知局限 —— Trino 的 `system.runtime.queries` 是内存里的、coordinator 重启就清空,那段时间的血缘会永久缺失,要做到一条不漏得让采集器读 `audit.query_events`(ADR-066 那张表),那是另一件事 |
| C 完整 MLOps | 🟡 审批/回滚 ✅(ADR-080)、**推理留痕 2026-08-30 已实现并端到端实机验证**(ADR-085,KServe 自带 logger → Kafka → Iceberg;`iceberg.ml.inference_log` 里 request/response 成对落库,非 platform-team 账号被 OPA 拒;它是特征漂移的前置)、**推理可观测性 2026-08-29 已做**(`platform-inference` 看板 6 panel,真集群出数 P95 9.7ms);**特征漂移 2026-08-30 也做了**([ADR-087](../decisions/087-feature-drift-monitoring.md)) —— 就是当初判断的那样,`jobs/` 里一个作业,没有引入新组件:训练时把基线写进 MLflow tag(基线必须和模型版本绑在一起,事后重建出来的是"现在"的分布、结论会错且不报错),作业按 PSI 比线上和训练的分布,结果落 `ml.feature_drift`。**没上过集群**。**还缺**:告警(等上生产接真实渠道)、类别型特征、label/concept drift(需要标签回流,平台还没有那条链路)。灰度是这套部署形态做不了 —— RawDeployment 无 Knative,`canaryTrafficPercent` 会被收下但完全不生效,脚本现在显式拒绝它 |
| D 统一运维控制面 | 🟡 黄金链路探针 ✅(**2026-08-30 加到七条**,新增 audit)、Runbook ✅、容量/成本看板 ✅、**服务目录 2026-08-29 已做**([service-catalog.md](../reference/service-catalog.md),35 个服务 + CI 校验归属/owner/依赖不漂移);**还缺**:多节点故障/备份恢复/升级回滚演练 |
| E 管理驾驶舱 | 🟡 平台总览看板第一版已上;**还缺**:按月聚合和预算对比 —— Prometheus 持久化 2026-08-28 才补上,数据要自己攒 |

下面每条的原始描述保留,作为"完整形态长什么样"的参照。

- **A. 统一开发工作台**:项目模型 → SQL/Notebook 黄金路径 → 作业模板
  + CI/CD → 训练黄金路径。原则是"先做薄控制面,复用成熟组件,不重新
  自研查询引擎/调度器"。
- **B. 数据资产与治理闭环**:权限真正执行(✅ 已完成,ADR-051)+ 审计
  闭环 → 数据契约/质量规则 → 端到端血缘 + 变更影响分析 → 敏感字段行列级
  策略(OPA 原生支持,没配置)。
- **C. 完整 MLOps**:标准镜像 + 可复现训练 → 模型审批/灰度/回滚 → 推理
  可观测性 → 特征漂移监控。
- **D. 统一运维控制面**:服务目录 + 黄金链路告警 → 统一 Runbook → 容量/
  成本看板 → 多节点故障/备份恢复/升级回滚演练。
- **E. 管理驾驶舱**:不新建数据源,汇总现有系统指标;第一版只回答"平台
  健不健康、谁在用、资源花在哪、数据资产覆盖率、权限风险、模型健康度"。

排序建议(评审原文,已认可):可靠底座 → 统一项目模型 → 分析师黄金路径
→ 大数据开发黄金路径 → 算法黄金路径 → 运维控制面 + 管理驾驶舱(从前三条
产生的真实指标构建,不先造空看板)。

### 确认要做、还没设计的引擎/组件

- **Flink**:2026-08-15 zhenghe 明确"作为新的大数据平台有它的必要性",
  从"Phase 4 按需"改成"确认要做"。**设计已完成**([ADR-056](../decisions/056-flink-role-design.md)):
  定位成"流式计算引擎"(实时聚合/join/特征计算),不做"数据搬运"。
  **2026-08-30 更正**:这里原来写着"只是设计,没部署任何东西" —— 实际
  Flink 2026-08-22 就部署并端到端验证过了(Kafka → Flink → Iceberg,
  [ADR-062](../decisions/062-flink-streaming-pipeline.md)),还长出了
  `streams/` 这套发布机制(写几行 yaml + PyFlink 脚本)和查询审计那条
  链路(ADR-066)。当时"Kafka 零真实消费者"的判断也不成立了。
- ~~**Spark 4.x 评估**~~ —— **已评估并且已经升了**。评估(2026-08-26,
  [ADR-076](../decisions/076-spark-4-evaluation.md))当时的结论是暂不升,
  但查出一个关键联系:**Spark 4 要求 Java 17,而 Iceberg 卡在 1.10.0 的
  原因正是当前 Spark 镜像只有 Java 11**。2026-08-29 触发条件满足,
  **Spark 3.5.9 → 4.1.3 + Iceberg 1.10.0 → 1.11.0 一起升完并实机验证**
  (`SPARK_ICEBERG_DEMO_OK`),升级记录在
  [`upgrade.md`](../operations/upgrade.md) 的「已知升级路径」。
- **Stackable(Spark/Trino/Hive 统一 Operator 平台)**:2026-08-15 Codex
  新提出,**目前完全没评估过**。值得单独做 PoC(非生产、对比部署/升级/
  故障恢复成本、保留绕开它直接用官方 Operator 的能力),**不迁移任何
  现有组件**。已知代价:版本支持明显滞后社区(Spark 长期支持线停在
  3.5.8),CRD/Operator/镜像形成中等到较高的平台绑定。

---

## P5:项目瘦身审计(zhenghe 2026-08-16 明确提出)

原话:"现在我怀疑项目臃肿,最后还需要瘦身,作为开源项目,里面很多应该
是没有用的。"排在这里不是因为不重要,是因为**在 P1/P2 落地之前做瘦身,
会把还没定型的东西提前删掉**——等交付方式稳定了再审计更准。

审计范围:

- ~~**demo/验证脚本是不是还都有价值**~~ + ~~**`scripts/` 的编号约定
  撑不住**~~ —— **2026-08-21 审计完成,结论和当初的假设不一样**:逐个
  核对全部 51 个文件,**没有找到该删的死代码**。原本假设"作为开源项目
  里面很多应该是没用的",实际不成立——每个文件都对应一个真实用途,而且
  demo 脚本对开源项目是"证明平台能力可复现"的资产,不是负担(新人靠它
  判断这平台是不是真能用)。真正的问题是**可读性**:编号不等于执行顺序
  (真正的顺序由 `bootstrap-all.sh` 编排)、五类东西混在一个平坦目录里。
  产出是 [`scripts/README.md`](../../scripts/README.md) 这份按"你想干什么"
  分类的导航,不是一批 `git rm`。**没有重命名/移动任何文件**——编号被
  README/ADR/journal 大量引用,重命名的破坏面远大于收益。
- **试错留下的死代码/废弃方案痕迹**。
- **历史踩坑记录该不该继续散在代码注释里**:像 SSO 四层故障链这种,
  manifest 注释里现在动辄二三十行。对维护者有用,但对第一次读的人是
  噪音——考虑保留结论、细节移到 ADR/journal。
- **`environments/cloud-full/pending-definitions/`**:P1.1 做完之后这个
  目录整个消失了(2026-08-20 完成)。

---

## 曾经提出、明确决定不做/暂缓的

- **需求追踪矩阵**(`docs/requirements.md`,给每条用户需求分配 ID 逐条
  追踪):讨论过,判断是对当前规模过重的流程负担,ADR + BACKLOG + project/capability-matrix.md
  已经覆盖"决策留痕"和"待办不丢"这两个真实需求。
- **自建 `scripts/task-runner.sh`**(start/status/logs/stop/resume 的
  后台任务管理器):当前后台任务的规模用不上,`nohup` + 日志文件够用。
- **正式的多工作流并行调度表**(workstream ID/资源预算/依赖表):当前
  是单人 + 单个 CURRENT 的工作方式,不需要这一层。
- **PostHog 或同类产品分析工具**:算法层面的 A-B 实验应该落在 KServe
  canary + 现有 MLflow/湖仓链路,不是消费端产品分析工具。见
  `docs/architecture.md` "还没定的事"里 2026-08-11 那条。
- **Backstage / Kubeflow 整套部署**:只借鉴实体模型/能力划分,不部署
  整套 UI。见 ADR-028/032 和 architecture.md 的判断。
- **Ranger**:官方不维护 Helm chart,不满足"只用官方支持的部署方式"
  门槛;改用 OPA(已上线,ADR-051)。

---

## 已完成(存档,按完成时间倒序)

保留一行索引,细节在对应 ADR / `docs/journal/`。

- **环境配置渲染机制 + `external_scheme`**(2026-08-16/19):
  `environments/<env>/config.yaml` + `templates/` +
  `render-environment-config.py`,域名/端口/协议三类环境差异值单一源码,
  `--check` 接进 CI。**只覆盖"取值"这一层**,组件选择和规格分档见 P1.1。
- **cloud-full SSO 全链路修复**(2026-08-16):四层故障链(redirect_url
  丢端口 → Keycloak hostname 推断丢端口 → issuer/backchannel 前后端地址
  冲突 → nginx 响应头缓冲区太小),外加 Superset 的 FAB keycloak provider
  需要显式 `api_base_url`。全部用 curl + cookie jar 端到端验证过真实登录。
  详见 `docs/journal/2026-08.md`。
- **Trino OPA 细粒度访问控制正式上线**(2026-08-16,ADR-051)。
- **破坏性操作防护补全**(2026-08-16):`confirm-destructive-kubectl.sh`
  + namespace 允许清单,背景见 `docs/operations/incidents.md`。
- **镜像缓存 digest 校验**(2026-08-16):`verify-image-digests.sh`。
- **iam-sync / opa-grants-sync 在 cloud-full 上修复并恢复**(2026-08-16)。
- **`alloy`/`loki`/`kube-prometheus-stack` 的 ArgoCD 同步超时**
  (2026-08-16):`controller.repo.server.timeout.seconds: 180`。
- **一键部署 `scripts/bootstrap-all.sh`**(2026-08-16):14 步幂等,
  真实跑通过。
- **版本审计与升级**(2026-08-15/16):PostgreSQL 16.6→16.15;Feast Redis
  `7-alpine`(浮动、且落在 Redis 非开源许可证区间 7.4.x)→ 固定 8.4.5
  (同时修 RESTORE 命令的 use-after-free RCE 和许可证问题);Triton
  23.05→26.07-py3;KServe 7 个浮动 `latest` 全部固定版本 + digest;
  Python 依赖锁定 5 处。**刻意没升的**:Kafka 4.3.0→4.3.1(Strimzi 1.1.0
  的 `kafka-versions.yaml` 还没收录 4.3.1,升上去会被拒绝);Trino
  480→483(483 对 `http-server.http.port` 收紧校验,和我们关闭 HTTP
  监听器的配置冲突,拉过 chart 源码确认根因,483 无 CVE 动机,不值得为
  版本号打开一个明文内部端点——见 `apps/definitions/trino.yaml` 注释)。
- **Airflow DAG 单一源码校验**(2026-08-16):`sync-airflow-dags-configmap.py`。
- **权限治理全链路**:分级审批(ADR-044)、可插拔审批后端 + 通知 + 超时
  升级 + 权限交接(ADR-045)、到期自动回收(ADR-050)、浏览目录申请
  (ADR-046,**体验依赖 OpenMetadata**,见 P1.2)。
- **CloudNativePG 迁移**(2026-08-13,ADR-038/039):含真实数据迁移、
  切流量,老实例已下线。真正的多副本 HA 要等 prod 多节点。
- **推倒重建验证**(2026-08-13,ADR-039):从空集群完整跑通一次。
