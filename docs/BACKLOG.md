# Backlog

新想法/顺手发现的问题,默认进这里,不自动打断
[`docs/CURRENT_WORK.md`](CURRENT_WORK.md) 里的当前主线。这份文件只做索引
和优先级排序,不重复描述已经在别处写清楚的内容——每一项都指向权威来源
(ADR / architecture.md),不在这里复制一遍。

**排序依据(2026-08-19 起,见 [ADR-057](decisions/057-architecture-review-2026-08-19.md)):
按"阻塞哪个角色的哪条能力"排,不按"还有哪个组件没接"排。**
判断某件事该排多前,先看 [`docs/roles.md`](roles.md) 的角色能力表。

> 2026-08-19 重排说明:此前的 P1.5/P1.6/P1.7 不是优先级,是不同日期追加
> 的批次(带小数的层级本身就是"一直在追加、没有重排过"的证据)。这次
> 按角色影响重新排了序,已完成的条目收敛到底部"已完成(存档)",避免
> 一份 400 行的清单里一半是划掉的东西。

---

## P0(会阻断当前主线的,才有资格排这里)

当前没有。如果出现真实的数据风险/持续计费异常/安全问题,加在这里,
并在 `docs/CURRENT_WORK.md` 里注明"CURRENT 被 P0 阻断,原因是……"。

---

## P1:解锁角色能力(投入产出比最高,先做这一批)

依据 [`docs/roles.md`](roles.md)。**1.2/1.3/1.4 已于 2026-08-19 完成**
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
Workflow→能查到→删除清理。详见 `docs/CURRENT_WORK.md` 归档记录。

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
`docs/roles.md` 里"批量数据接入 ✅"这个结论挂了好几天,实际从来没通过。

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

### 2.5 扩大 CI

**已迈出几步**(chart 渲染校验、DAG 单一源码、app ConfigMap 单一源码、
环境配置渲染防漂移、3 个 Flask 应用的测试)。原评审 P1-3 清单里更大的
扩展(镜像构建 —— 见 2.1、集成测试)还没做。

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

- **告警送不到人**:Alertmanager 已开、规则生效、抓到过真实问题,但
  **没有配任何外部通知渠道**,现在只能"打开界面查"。邮件/企微/Slack 的
  配置模板已预留在 `platform/apps/kube-prometheus-stack.yaml` 注释里,
  需要真实凭据才能激活(**这一步需要 zhenghe 提供,不是 Claude 能自己
  造的**)。
- **排障知识 Runbook 化**——2026-08-20 补了第一步:按症状类别分组、带
  锚点链接的真实索引(之前 `## 索引` 这个标题底下是空的)。**还没做的**:
  每条正文条目本身的"现象→原因→处置"结构不够统一(大部分条目已经有
  这个结构,但格式不是强制统一的,有的条目掺了排查过程叙事),真正的
  Runbook 化需要重新过一遍每条、统一格式,这次没做——838 行内容较大,
  一次性重写风险不小(容易在改写措辞时不小心丢失细节),留到有真实
  动机(比如真的按索引查找时发现某条不好用)时再逐条打磨,不批量重写。
- **Superset 汉化**:zhenghe 2026-08-16 提出,自己说了"不急"。Superset
  原生带中文语言包(`LANGUAGES`/`babel`),预期加几行 configOverrides
  就够,真做时先确认 6.1.0 的中文翻译完整度。
- **dbt 接 Airflow 编排 + OpenMetadata 摄入**:ADR-053 的最小骨架能跑
  `dbt build`,但没接 Cosmos(需要改 Airflow scheduler/dagProcessor 的
  Python 运行时)、没接 OpenMetadata dbt 连接器,等于"能手动跑 dbt",
  不是分析师的生产工具。
- **公网域名 + TLS 接入**:zhenghe 2026-08-16 明确的方向——"域名走配置化
  生效,配置 test 起来就是可临时访问的;未来配置 prod,就强制需要配置
  一个域名"。`test` 类环境允许没有真实域名(继续 NodePort + `/etc/hosts`);
  `prod` 应**强制**要求真实域名 + TLS,是校验层面的硬要求,不是建议。
  scheme(http/https)的配置化 2026-08-16 已经做完(`external_scheme`),
  剩下的是域名注册 + ICP 备案(中国大陆服务器强制,1-20+ 工作日,
  **需要 zhenghe 亲自做身份核验**)和 ingress-nginx/cert-manager 按环境
  分叉的设计,真正做时单独出一份 ADR。

---

## P4:五条面向角色的产品主线(长期,都还没开始)

完整方案见 `docs/architecture.md` "Phase 4 之后"一节和原始评审
`docs/claude-improvement-recommendations-2026-08-15.md`。**这五条现在都
还没有开始实现**,不要误以为在做。

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
  从"Phase 4 按需"改成"确认要做"。**设计已完成**([ADR-056](decisions/056-flink-role-design.md)):
  定位成"流式计算引擎"(实时聚合/join/特征计算),不做"数据搬运"。
  引入顺序上 Kafka 现在零真实消费者、Kafka Connect + Iceberg Sink
  这条轻量路径也没搭过,这两步应先于 Flink。**只是设计,没部署任何东西。**
- **Spark 4.x 评估**:仓库固定 Spark 3.5.9,官方已到 4.x。核实结论
  "方向对,但 Codex 支撑这个方向的一条关键理由是错的"(Spark 4.0 的
  SPARK-45265 是 Spark 内置 Hive 客户端支持 Hive 4.0 metastore,和我们
  独立部署的 Hive Metastore 版本锁定不是一回事,升 Spark 4 并不能解除
  那个锁定)。3.5.9 不是废弃版本,但确实是旧的大版本线。升级要一起动
  Scala 2.13 / Java 17 / `iceberg-spark-runtime-4.x_2.13`。
- **KServe runtime 矩阵设计**:TF Serving(2.6.2)/ TorchServe(0.9.0)
  要不要精简或换掉,是故意没做的更大设计判断,不是遗漏(7 个浮动
  `latest` 的固定版本 + digest 已经在 2026-08-15 做完)。
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
  产出是 [`scripts/README.md`](../scripts/README.md) 这份按"你想干什么"
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
  追踪):讨论过,判断是对当前规模过重的流程负担,ADR + BACKLOG + roles.md
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
