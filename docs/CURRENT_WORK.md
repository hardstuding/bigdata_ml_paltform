# 当前工作

> 这份文档只回答三件事:**现在的主线是什么、下一步做什么、有没有还在跑
> 的后台任务**。规则:任何时候只有一个 CURRENT,新想法默认进
> `docs/BACKLOG.md`,不自动抢占 CURRENT。
>
> 2026-08-19 起,按日期堆叠的排障叙事**不再往这里写**,归档到
> `docs/journal/<年-月>.md`(原因见
> [ADR-057](decisions/057-architecture-review-2026-08-19.md))。这份文件
> 要一直保持"打开就能知道现在什么情况",不能再退化成日记。
>
> 想知道"某个角色今天能做什么" → [`docs/roles.md`](roles.md)
> 想知道"以前某个问题怎么解决的" → [`docs/journal/`](journal/) 和
> [`docs/operations/troubleshooting.md`](operations/troubleshooting.md)

## CURRENT(2026-08-20,第二轮)

用户明确授权连续自主工作(先 3h,后又延到至少 4h),这是当天第二轮
session,BACKLOG 2.1/2.3/2.6 + P1.7 的进展都在这轮做的,全部已在
cloud-full 云主机上真实验证,不是只写完代码。

- **BACKLOG 2.1 全部完成**(3 个 Flask 应用 + iam-sync 都切到构建期镜像):
  - 3 个自建 Flask 应用(permission-request-app/table-registration-app/
    platform-portal)新增 Dockerfile + GitHub Actions(`build-images.yml`)
    推 GHCR,`deployment.yaml` 切到 digest 引用,退役了运行时
    apt-get/pip install 那套(历史上一晚炸过 4 次的坑)。
  - iam-sync 镜像化:和 3 个 Flask 应用不同,它必须保留运行时
    `git clone`(每次要拿 `platform/iam/` 最新数据),固化的只是
    "装 git/kubectl/pyyaml 这几个工具"这件事。
  - **修了一个多架构构建的隐患**:一开始只建 linux/amd64,但
    local-lite(colima/arm64)也会拉这几个镜像,iam-sync 打包了
    kubectl——这个仓库已经真实踩过"amd64 kubectl 在 arm64 节点 QEMU
    模拟下触发 client-go 并发 bug"这个坑,只建 amd64 会重新引入。已经
    改成 buildx 建 `linux/amd64,linux/arm64` 两个平台,4 个镜像全部
    重新构建、更新了 digest。
  - **云端全部验证过**:3 个 Flask 应用 Pod 正常拉取新 digest 并
    Healthy;iam-sync 手动触发一次 Job,`git clone`→`kcadm.sh` 同步
    roles/groups 全部成功;Trino livenessProbe CronJob(见下面 2.3)
    的自愈逻辑手动打坏→触发→确认修复的完整闭环也验证过。
- **BACKLOG 2.3 完成**:Trino livenessProbe 之前必须每次 Deployment
  重建后手动重跑脚本,现在是 `apps/trino-liveness-fix/` 这个 CronJob
  每 5 分钟自动巡检修复(chart 是远程 Helm 源,插不进 ArgoCD postSync
  hook,改用轮询)。cloud-full 上真实验证过完整闭环(打坏→自愈→确认
  no-op),不只是部署。
- **BACKLOG 2.6:发现并纠正了一个记录错误**——2026-08-19 那次
  "notebook 里调 submit_job() 被 NetworkPolicy 挡住"记录成"已知未
  解决",但当时其实规则已经写对了,只是没来得及验证。这次用一个模拟
  notebook 网络身份的 debug pod 重新测试,`submit_job()` 和新增的
  `run_workflow_template()` 都端到端成功——这个 known limitation 已经
  不存在了,相关文档(BACKLOG/skill/manifest 注释)都同步更新。
- **P1.7 新增能力(SDK 侧写好 + 云端验证过,notebook→Feast→训练完整
  链路串联仍是空白)**:`platform_sdk.run_workflow_template()`,notebook
  里一行代码触发已经部署好的 Argo WorkflowTemplate(比如
  `train-demo-model`),不用 `kubectl create`。云端debug pod 里真实
  触发过一次,Workflow Succeeded。
- **顺手发现但没动手修的事**(记进 BACKLOG 2.7):cloud-full 节点上
  k3s 内置的 Traefik 从没被禁用,和 ingress-nginx 抢 80/443 端口——
  已确认不影响真实访问(项目走 NodePort,不是裸 LB 路径),关闭 Traefik
  涉及节点级 k3s 配置改动,留给专门时间处理。
- 顺手清理了 `default` 命名空间里一个孤立的 JupyterHub 副本(17 小时
  前某次手动 `kubectl apply` 绕过 ArgoCD 留下的残留,crash-loop 但不
  影响真正在 `jupyterhub` 命名空间跑着的那个),以及几个陈旧的失败 Job
  Pod(superset-init-db/hook-image-awaiter 的历史重试记录)。
- **BACKLOG 2.1 又推进了两段**(用户确认继续工作后,同一天第三轮内容):
  - **Superset**:2026-08-19 其实已经解决了运行时装依赖的反模式,只是
    构建方式是手动登云主机——这次接进 CI 自动构建流水线。这个 chart
    的 image 字段不支持 digest(只有 repository/tag),用构建时的
    commit SHA 当 tag 达到等价可追溯性。云端验证:新 Pod 拉取 GHCR
    镜像成功、`psycopg2`/`authlib`/`trino` 三个包 import 正常、
    ArgoCD Synced/Healthy。
  - **argo-workflows-training-image**:同样接进 CI,`workflow-
    template.yaml` 切到 GHCR digest 引用。**云端真实触发了一次训练
    Workflow 验证**(`workflowTemplateRef` 指向 `train-demo-model`),
    Succeeded——不只是镜像能拉,训练本身也真的跑通。
- **BACKLOG 2.1 完全收尾(第五段,feast feature-server)+ 2.7(Traefik)
  一起处理完**(同一天第四轮):
  - **feast feature-server-image**:基础镜像是 RHEL UBI(`microdnf`,
    和其它 Debian-based 的 Dockerfile 不同),先用 quay.io manifest
    list API 确认原生支持 arm64,再接进 CI。云端验证:Pod 拉取新镜像、
    `pyspark`/`java` 都可用,真实调用 `/get-online-features` 拿到正确
    schema。**至此仓库里所有自定义 Dockerfile 都接进了 CI,不再有任何
    "必须手动登云主机 build"的镜像**。
  - **BACKLOG 2.7(Traefik)**:k3s 内置 Traefik 从没禁用,占用
    80/443 和 ingress-nginx 抢——这次是节点级操作,先问过用户确认才
    动手(不是"顺手"做的)。`/etc/rancher/k3s/config.yaml` 加
    `disable: [traefik]` + 重启 k3s,`scripts/21-bootstrap-cloud-vm.sh`
    同步补上参数。验证:之前一直 `Pending` 的 svclb-ingress-nginx Pod
    变成 `2/2 Running`。顺带发现并修好一条独立的 4 天前的 ArgoCD
    SyncError(`operationState` 清了还不够,`argocd-application-
    controller` 自己的对象缓存也陈旧,重启 controller 才彻底解决,
    新增了 `docs/operations/troubleshooting.md` 条目)。
  - 这轮结束时集群状态:除了 alloy/loki/kube-prometheus-stack 这三个
    已知的 helm 仓库网络抖动(`grafana.github.io` 连不上,和这次改动
    无关的老问题)之外,**没有任何已知的 OutOfSync/Degraded 项**。
- 这份 CURRENT 记录之后,VM 会停机(经济模式),不产生持续计费。

## 上一轮 CURRENT(2026-08-20,第一轮,已完成,存档)

- **标题**:ADR-057 第三批(环境抽象补"组件选择"层)+ ADR-058 补充
  (Airflow platform_sdk_demo DAG 首次干净成功验证)
- **状态**:**都已完成并在 cloud-full 云主机上真实验证过**。
  - ADR-057 第三批:`apps/components/` 成为全部 43 个组件定义的唯一
    源码,`apps/definitions/` 变成 100% 生成产物,`pending-definitions/`
    机制退役。本地零功能差异验证过,云端 `apps-root` 手动 hard refresh
    后确认 Synced 到最新 commit、43 个组件全部 Healthy(个别组件短暂
    Progressing/Degraded 是冷启动重启的正常现象,已确认自愈,不是回归)。
  - ADR-058 补充:`platform_sdk_demo` 这条 Airflow DAG 之前三次触发都
    卡在真实 bug 上(subPath DAG 挂载/subPath Trino 密码/OPA 白名单/
    airflow-worker 跨命名空间 RBAC),这次挖出并修好第 4 个(RBAC),
    重新触发后 DagRun 和 task 都是 **success**,是这条链路第一次真正
    端到端跑通的实测证据。
  - 顺带补完 `docs/BACKLOG.md` 2.4(三个 Flask 应用测试覆盖,补了 git
    写入路径 + OA webhook 的 mock 测试,106 个测试全绿,本地跑的,和
    云主机验证无关)。
  - 云主机(`i-0jlbped4h1959tp591pe`)这次是抢占式实例容量不足
    (`OperationDenied.NoStock`)卡了一阵,起了个后台重试循环等到有货
    才开机成功,不是操作失败。

## 上一版 CURRENT(2026-08-19,已完成,存档)

- **标题**:ADR-057 第一批(文档重组)+ P1.2/1.3/1.4(部署 OpenMetadata /
  JupyterHub+MLflow / Spark Operator+SeaTunnel+Spark History Server)
- **状态**:**都已完成**。P1.2/1.3/1.4 不只是部署,每个都用真实
  curl+cookie-jar 端到端登录测试验证过(不是只看 Pod Running)。
- **做完了什么**:
  - 文档重组(见上一版本的 CURRENT 记录 / ADR-057)
  - **OpenMetadata**:部署 + 真实 OAuth2 token 交换验证(拿到真实
    access_token/id_token,issuer 匹配)
  - **JupyterHub**:部署 + 真实登录验证(SSO 成功跳转 `/hub/spawn`)
  - **MLflow**:部署 + 真实登录验证(200,`<title>MLflow</title>`,
    按组授权生效)
  - **Spark Operator / SeaTunnel / Spark History Server**:部署 +
    History Server 真实登录验证(200,`<title>History Server</title>`)
  - 顺带修了 **8 个真实 bug**(这些组件长期 park、从没真的走过一次登录,
    配置错误一直没暴露,靠这次真登录测试才挖出来):
    1. OpenMetadata / JupyterHub / Spark History Server 的 SSO 端口
       (8-16 那批修复之前写的,没打 NodePort 端口)
    2. OpenMetadata 镜像拉取超时(`docker.getcollate.io` 是 scarf.sh
       包装的下载统计域名,本质是 docker.io,同一片网络问题)+ chart
       硬编码 `imagePullPolicy: Always` 导致本地缓存也没用
    3. MLflow 内存限制太小,新 pod 启动 36 秒内 OOMKill
    4. MLflow / Spark History Server 的 oauth2-proxy 没显式配 `scope`,
       二进制默认值里的 `groups` 在这个 realm 里根本不是有效 scope 名,
       Keycloak 直接 `invalid_scope` 拒绝
    5. MLflow oauth2-proxy 的 `upstreams` 写的 Service 名字不存在
       (chart 真实生成的是 `mlflow-mlflow`,不是裸 `mlflow`)
    6. Spark History Server 缺 S3A 连接器 jar(官方镜像不打包,加了个
       initContainer 从 Maven 下载并补进 `${SPARK_HOME}/jars/`)
    7. **Argo Workflows 从 8-16 起就 CrashLoopBackOff,两天多没人
       发现**——issuer 校验失败(discovery 文档拿到的 issuer 字段带
       端口,发起请求用的地址不带),读了官方源码确认要用
       `sso.issuerAlias`(对应 `oidc.InsecureIssuerURLContext`)才能
       两边都满足。**2026-08-19 晚些时候补完:登录后调 API 403 的
       RBAC 层也修好了**——`server.sso.rbac.enabled: true` 本身不建
       任何授权资源,读官方源码(`server/auth/gatekeeper.go`)确认
       还要手动建 ServiceAccount(挂 `workflows.argoproj.io/rbac-rule`
       注解匹配 `platform-team` 组)+ 长期 `kubernetes.io/service-
       account-token` 类型 Secret(K8s 1.24+ 不自动建)+ Role/
       RoleBinding,四个资源都加进了
       `templates/apps-definitions/argo-workflows.yaml` 的
       `extraObjects`。另外发现 argo-workflows 这个 client 当时没挂
       上 groups client scope(第 8 条修的那批,第一版列表也漏了它),
       一并补进 `scripts/03-configure-keycloak.sh`。真实 curl+cookie-jar
       验证过:登录 → `GET /api/v1/workflows/argo-workflows` 200 →
       建一个真实 Workflow → 能查到 → 删除清理。
    8. **Keycloak realm 从建立起就没有任何 client 配过 groups claim
       mapper**——直接解码拿到的 id_token 确认过,admin 明明在
       platform-team 组里,token 里完全没有 groups 字段。这意味着
       Grafana(ADR-028)/JupyterHub(ADR-025)"按组收紧已验证"的说法
       不准确,大概率从来没有真的按组生效过,只是没人拿会被拒绝的账号
       测过。已建 realm 级别的 `groups` client scope + mapper,挂到
       grafana/jupyterhub/mlflow/spark-history-server 这四个用到
       `allowed_groups` 的 client 上
  - 顺带修了两个和这次部署无关、但排查过程中撞见的独立问题:
    idle-shutdown-watchdog 开机后被自己的旧状态误杀(见下面"已知的事")、
    `scripts/03-configure-keycloak.sh` 的"client 已存在但 Secret 缺失"
    自愈逻辑
  - `docs/roles.md`、`docs/BACKLOG.md` 已同步更新反映这些变化

## 下一步唯一动作

**2026-08-19 晚些时候补充:ADR-058 第一批(platform_sdk + 统一开发镜像)
已实现并端到端验证,详见 ADR-058 全文,这里只记结论**——`platform_sdk`
(`platform-sdk/`)+ 统一镜像(`apps/platform-image/`)已经让 JupyterHub
notebook 开箱即用 `query()`(连 Trino)/`mlflow_setup()`(连 MLflow),
两个都在真实 notebook pod 里验证过成功;`submit_job()` 本身也端到端
验证成功(建 Workflow→pod 里跑→查 Trino→记 MLflow,全部 Succeeded),
但**只在不受限制的环境**(本机 kubeconfig / CI)里验证通过——直接从
notebook pod 里调 `submit_job()` 被 chart 默认的 singleuser
NetworkPolicy 挡住连 K8s API server,根因没查清,已知限制记在
`docs/BACKLOG.md` 2.6。过程中还顺带发现并修好一个这条 NetworkPolicy
本身挡掉 Trino/MLflow 的问题(默认 `privateIPs: false`,notebook 连不上
任何集群内部服务,这个已经修好并验证)。

下一步是 ADR-057 第三批(见下),或者继续把 ADR-058 剩下的部分(job.yaml
脚手架已有,submit-job skill 还没写)做完——两条都不阻塞对方,谁先做
看你的判断。

**ADR-057 第三批:补上环境抽象的"组件选择"层——现在优先级比之前判断的
更高,不是更低。**

这次拉起 P1.2/1.3/1.4/1.6 全靠人工 `git mv` + 逐个手动排查 Keycloak
client/Secret/scope 缺口完成的。过程中暴露的好几个 bug(client 已存在但
Secret 永远补不上、groups scope 从建realm起就没配过)本可以在"改配置就
重新拉起"这套机制的约束下被更早、更结构性地测试出来,现在只能靠"当天
有没有人手动测登录"这种运气发现。

做完这一批,以后再拉起/重建任何组件,应该默认带着"这次会不会又是同一类
配置漂移"的怀疑,而不是假设"Pod Running 就是好的"。

**2026-08-19 又补完的三项**:
- **Kafka**(P1.6):部署 + 真实建 topic/生产/消费一条消息验证通过。
  大数据开发角色最后一块拼图补上,但还没接进真实数据管道(没有真实
  Producer/Consumer 应用)。
- **算法链路"训练 → MLflow"这一段真实跑通**:`scripts/09-train-demo-model.sh`
  重新验证,真实训练一个 sklearn 模型(accuracy 0.855)、注册进 Model
  Registry、API 查询确认 status=READY。过程中顺带修了两个真实 bug:
  1. 脚本本地端口 5000 和 macOS 自带的 AirPlay 接收器冲突,
     `kubectl port-forward` 静默绑定失败,所有请求被 AirPlay 的 HTTP
     接口拦下返回 403(不是超时,容易误判成权限问题)——改用本地 15500。
  2. **更重要**:早些时候修 MLflow OOMKill 调大内存限制那次改动,
     部署时"看着"成功了(ArgoCD Synced/Healthy),实际上被 mlflow
     命名空间的 ResourceQuota(3Gi)卡住——RollingUpdate 需要新旧 pod
     同时占配额,超了配额新 ReplicaSet 一直 "exceeded quota" 起不来,
     旧的、没修好的 pod 继续服务流量,**卡了一个多小时没人发现**。
     手动 scale 旧 ReplicaSet 到 0 腾出配额才推进,并把 mlflow 的部署
     策略改成 `Recreate`,从根上避免这类"改动看着生效了、实际卡住"的
     坑再犯——**这是这次会话最值得记住的一类教训:低配额命名空间里,
     哪怕只改 resources 这种"看起来无害"的字段,也可能让滚动更新
     悄悄卡死,ArgoCD 的 Synced/Healthy 不能当作"真的生效了"的证据,
     要对照 ReplicaSet/Pod 的实际状态**。
- **Feast 特征链路也重新验证通过**:Iceberg → Spark 离线读取 → feast
  apply → materialize → Redis 在线存储 → Feature Server 在线查询,
  Alice/Bob 的 region/product/amount 全部查出正确值。过程中修了两个
  真实 bug:①`feast_materialize` 这个 DAG 默认是暂停状态(Airflow 新
  DAG 的默认行为),`scripts/19-feast-feature-pipeline.sh` 之前没有
  在触发前先取消暂停,手动触发的 DagRun 会一直卡在 queued,已经补上
  `airflow dags unpause` 这一步;②`feast-feature-server` 用的
  `local/feast-feature-server:0.65.0-spark` 是本地构建镜像,cloud-full
  这台远程节点上从没构建过,`ErrImageNeverPull`(这是本次会话早前已知、
  接受的差距,这次真正解决了)。直接在 cloud-full 这台 x86_64 节点上
  用官方 Dockerfile 现场 `docker build`——过程中又踩中一次真实的 PyPI
  带宽限速(装 317.9MB 的 pyspark,直连官方源实测约 39KB/s、要 2 个多
  小时,换阿里云 PyPI 镜像后约 1.6MB/s,40 倍提速,几分钟装完,已经把
  这个 mirror 参数写进仓库的 Dockerfile,是 git 里的,任何人往后重新
  构建都会用这条路径,不是这次的临时救急)。构建产物也 `docker save`
  导出进了这台 Mac 本地的 `image-cache-amd64/`(**这个目录整个在
  .gitignore 里,是本机本地缓存,不进 git**——manifest.txt 也是本地的,
  真要给一台离线的生产节点用,需要人工把这个目录整个传过去,不是 clone
  仓库就自动带上)。呼应"生产环境可能没有网络"这条顾虑:git 里的
  Dockerfile 修复保证"有网络的话,重新构建又快又不踩限速的坑";这份
  本地缓存保证"完全没有网络的话,把这个目录拷过去也能直接
  `docker load` 用上,不用再连一次网现场构建"——两条路径都留了,不是
  只顾一头。

**2026-08-19 晚些时候:"Argo Workflows 编排训练"这段空白补上了**——参考了
`/Users/zhenghe/my_work/ysb/algo`(zhenghe 真实生产团队的项目)里
DolphinScheduler + notebook + papermill 的既有模式,评估之后**没有照抄**:
那边团队日常在 notebook 里交互式开发,notebook 是原生工作产物;这个平台
自己的 notebook 交互式开发体验还是明确记录在案的差距(`docs/usage-guide.md`
"交互式开发/Notebook"一节——没有自动连 Trino、没有自动带凭据),在这个
体验成熟之前把 notebook 当生产流水线执行单元是本末倒置。改成沿用这个项目
已有的 `apps/spark-iceberg-demo` 模式:纯 Python 脚本(ConfigMap 挂载)+
专门构建的依赖镜像,复用 `scripts/train_demo_model.py`(改成完全靠环境
变量配置,`scripts/09-train-demo-model.sh` 手动跑和 WorkflowTemplate 跑
是同一份代码,只是 `MLFLOW_TRACKING_URI`/`MLFLOW_S3_ENDPOINT_URL` 的值
不同)。见 `apps/argo-workflows-training-image/` 和
`apps/definitions/argo-training-workflow-template.yaml`。

真实提交 Workflow 端到端验证过(不只是部署),过程中挖出 4 个真实 bug:
1. `mlflow-skinny` 不带 pandas(`mlflow.sklearn.log_model()` 内部隐式
   依赖做 schema 推断),本机 anaconda 环境凑巧有 pandas 一直没暴露,
   干净容器里第一次跑才报 `ModuleNotFoundError`。
2. `platform/network-policies/manifests/minio.yaml` 的 MinIO 消费者
   白名单漏了 `argo-workflows` 命名空间(和之前 feast/dbt 踩的是同一类
   坑,第四次复现),训练成功但上传模型 artifact 时
   `EndpointConnectionError`。**这次改动本身还踩了一次坑**:第一次提交
   只加了说明注释、漏加了实际的 namespaceSelector 条目,ArgoCD 显示
   Synced 但活对象没变——再次印证"Synced 状态不能当证据,要直接查活
   资源"。
3. WorkflowTemplate 没显式指定 `serviceAccountName`,落到 `default` SA,
   没权限创建 `workflowtaskresults`,训练本身成功但 Workflow 整体判定
   Error。
4. 改成指定 `serviceAccountName: argo-workflow` 后又报 SA 不存在——
   chart 自己建了对应的 Role/RoleBinding,但没建这个 ServiceAccount
   本身,补建了这一个最小对象(没改 chart values,没加权限规则)。

最终验证:`train-demo-model-rmndq` 这个 Workflow Succeeded,MLflow
Model Registry 查询确认 `demo-rf-classifier` version 3、
`status: READY`,run_id 和 Workflow pod 日志里打印的一致。

目前只有训练这一步(没有特征工程/评估这类多步骤 DAG,模板结构已经为
以后扩展准备好),也没有"notebook 里触发"这条腿——这两个仍然是真实的
未做项,记在 `docs/BACKLOG.md`。

**2026-08-19 当晚,重开 cloud-full 云主机给 zhenghe 现场看效果时,又发现并
修好三个真实问题**(不是主动排查出来的,是他实际点开页面才暴露的):
1. **给错密码**:一开始把 Keycloak 自己控制台(master realm)的 admin
   密码当成了 platform realm 里给各应用 SSO 用的账号——两个刚好都叫
   admin,是完全不同的账号。已经把 platform realm 的 admin 密码重置成
   已验证能用的值,记在本机 `secrets/generated-credentials.txt`(不进
   git)。
2. **Superset 官方示例数据存在临时 SQLite 里,pod 重启就丢**——
   `SQLALCHEMY_EXAMPLES_URI` 不配的话默认落到 pod 内本地文件
   `/app/superset_home/examples.db`,没挂任何持久化卷。仪表盘定义本身在
   Postgres(持久),但引用的数据表跟着 SQLite 一起消失,页面报
   "no such table"——和 troubleshooting.md 里 Keycloak H2 内存库那次是
   同一类教训。改成在已有的外部 Postgres 实例上新建一个
   `superset_examples` 库(复用同一个 superset 账号),已经验证真实数据
   落在 Postgres 里(`SELECT count(*)` 确认 FCC/birth_names 等表有数据)。
3. **Airflow 接入 Keycloak SSO(之前从未做过,这次是真正新增能力,不是
   修复)**,过程中连续踩了 3 个真实坑,顺着修完才端到端 curl+cookie-jar
   验证通过(登录→拿到 session→`/api/v2/dags` 返回 200 不是 401):
   - `config: fab: proxy_fix_x_port` 这种写法不生效(和上面
     `AIRFLOW__KUBERNETES_EXECUTOR__DELETE_WORKER_PODS_ON_FAILURE` 那条
     是同一类坑:chart 的 `config:` 合并只认预置在模板里的固定键),改用
     `AIRFLOW__FAB__PROXY_FIX_X_PORT` 环境变量。
   - OAuth 回调 `redirect_uri` 丢端口:Airflow 默认信任
     `X-Forwarded-Port`(FAB 的 ProxyFix),但 ingress-nginx 在 NodePort
     场景下转发的这个 header 反映它自己的内部端口,不是外部实际访问的
     32460,把这一项端口信任关掉,让 Werkzeug 直接用 Host 头本身
     (nginx 原样转发,是对的)。
   - Keycloak client 的 redirectUris 照抄 Superset 那条时漏了 `/auth`
     前缀——Airflow 3.x 的 api-server 是 FastAPI 套壳,FAB 这个 Flask
     子应用整个挂在 `/auth` 前缀下,Superset 没有这层挂载。

这三个问题在 `apps/definitions/airflow.yaml`、`apps/definitions/
superset.yaml`、`apps/superset/manifests/create-db-job.yaml`、
`scripts/03-configure-keycloak.sh` 都已经改成声明式/脚本化,不是手动改了
一下集群就完事——下次从空环境重新部署也会带上这些修复,不需要重新踩一遍。

**第四个:OpenMetadata SSO 登录报 "Account already exists"**。zhenghe 当场
指出不应该接受"换个账号绕过去"这种处理方式(原话:"如果一开始给
openmetadata 适配好 keycloak,应该不会出现你现在这个问题……因为人员是会
流动的,所以确实需要一个 admin 的管理员账号"),追问之后确认他说得对
——真正原因是 Postgres `user_entity` 表里有一条半损坏的 `admin` 用户记录
(`authenticationMechanism` 是空的),是之前某次不走完整浏览器 OIDC 流程
的验证方式(直接拿 token 测 API)留下的,不是 `apps/definitions/
openmetadata.yaml` 里的配置问题。删掉这条脏数据、走一遍完整登出+重新
登录后,`admin` 账号本身干净登录成功,`isAdmin: true` 正确。**这是数据
层面的一次性清理,不是配置 bug,不需要改任何"一键部署"代码**——真正
全新的部署从第一次登录起就是干净的,不会重现。详见
`docs/operations/troubleshooting.md`。教训记在同一份文档:验证 SSO 时
只用真实浏览器走完整流程,不要用"直接拿 token 调 API"这类捷径碰用户
身份这一层,会留下不完整的记录,后续才用一个不相关的错误冒出来。

## 正在运行的后台任务

**没有。**

- cloud-full 云主机(`i-0jlbped4h1959tp591pe`)**本次会话结束前会停机**,
  用 `scripts/26-stop-cloud-vm-economical.sh`,经济模式
  (`StoppedMode=StopCharging`),停机期间不产生计算费用。
- 重新开机后 SSH 隧道要重新建(公网 IP 不是固定 EIP,这次会话期间就
  变过两次),命令见 `environments/cloud-full/STATUS.md`。
- 本机 colima 上的重量级组件处于 park 状态。

## 已知的、还没解决的事(不要重新排查一遍)

- **idle-shutdown-watchdog 的开机自愈**(2026-08-19 修复,这个脚本本身
  按既定政策不进 git):停机几天后重新开机,看门狗第一次检查会用几天前
  的旧时间戳误判"已空闲超过阈值",机器刚开机 2-3 分钟就被自己关掉。
  已加开机时重置状态的机制,细节在本地脚本注释里,不在 git 历史里。
- **ArgoCD 偶发卡在过期的同步操作上**:`.status.operationState` 会卡在
  一个旧的操作快照上不断 retry,用它缓存的旧 source 把已经修好的资源
  改回去。处置方式(本次会话实测有效)见
  `docs/journal/2026-08.md`,搜"卡住的旧操作"。
- ~~`scripts/07-fix-trino-liveness-probe.sh` 必须在每次 Trino pod
  template 变更后重跑~~——**已解决(2026-08-20)**:`apps/
  trino-liveness-fix/` 这个 CronJob 每 5 分钟自动巡检并修复,不需要人
  记得手动重跑了,见 docs/BACKLOG.md 2.3。
- **cloud-full 上 Keycloak `platform` realm 的 `admin` 密码**是
  `TestLogin2026Aug`,和 `secrets/generated-credentials.txt`(那份是
  local-lite 的)不是一回事。
- **算法链路"训练 → MLflow"和"Feast 特征"都已验证,"notebook 触发"和
  "Argo Workflows 编排训练"是真空白**:JupyterHub/MLflow/Spark
  Operator/Feast/Argo Workflows/Kafka 都已部署验证,
  `scripts/09-train-demo-model.sh` 和 `scripts/19-feast-feature-pipeline.sh`
  分别证明了这两段真实可用。剩下两段不是"没重新验证"而是从没实现过,
  见上面"下一步唯一动作"里的说明。
- **低配额命名空间改 resources 字段要格外小心**:mlflow 命名空间的
  ResourceQuota 只有 3Gi,RollingUpdate 需要新旧 pod 同时占配额,改大
  resources 时如果新旧加起来超配额,新 ReplicaSet 会静默卡在
  "exceeded quota",ArgoCD 显示 Synced/Healthy 但实际流量还在旧 pod
  上——这次真实卡了一个多小时才发现。mlflow 已经改成 `Recreate` 策略
  规避,其它低配额命名空间(检查 `platform/resource-quotas/manifests/
  quotas.yaml`)如果也要改 resources,先算一下新旧加起来会不会超配额,
  或者一并考虑改成 Recreate。

## 结束一段工作前必须确认(照着过一遍,不要跳)

- [ ] `git status` 干净,该 push 的都 push 了
- [ ] 计费资源现在的状态说清楚了(开着/停了,为什么)
- [ ] 后台任务/SSH 隧道是不是还开着,写进了上面那节
- [ ] 这次做的事,哪些是真实验证过的、哪些只是写完代码没测,分层说清楚
- [ ] 有没有手工改过集群但没回写 git 的操作(有的话赶紧记下来或者补写)
- [ ] 失败但没解决的事情,写清楚现象+已经排除的原因,别人接手不用重新排查一遍
- [ ] **能力有增减的话,`docs/roles.md` 更新了吗**(新增的一条,ADR-057)
