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

## CURRENT(2026-08-19)

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
- **`scripts/07-fix-trino-liveness-probe.sh` 必须在每次 Trino pod
  template 变更后重跑**,否则 livenessProbe 回退到 chart 的坏默认值。
  这条已经作为债务记进 ADR-057。
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
