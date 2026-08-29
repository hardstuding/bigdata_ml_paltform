# 常见问题排查(Runbook)

> 这份文件记录施工过程中遇到的真实问题,格式统一为**症状 → 定位 →
> 处置**三段式,主要给未来的 AI Agent 和人类共同排障用。记录要具体
> (报错信息、命令、涉及的组件版本),不省略细节。
>
> **2026-08-22 结构性改造**:此前正文按时间顺序堆叠(只增不减),索引
> 只是"贴到正文对应位置"的目录。这次改造把正文本身也按"故障发生的层次"
> 重新分组(集群/节点、ArgoCD/GitOps、网络与 Ingress、存储与 S3A、认证
> SSO、K8s 资源生命周期与 NetworkPolicy、各组件专属、本机/云主机环境、
> 已废弃),不再是时间顺序——这意味着这份文档和 git blame/commit 时间线
> 不再一一对应,想看某条记录是什么时候写的,去对应的 ADR 或
> `docs/journal/2026-08.md` 找日期。之所以做这个取舍:一篇按时间堆的长文
> 不是 Runbook,出事时没法在 30 秒内定位到对的那一条,这比"保持和
> commit 历史对应"更重要。每条尽量标注了原始来源(ADR 编号 / journal
> 日期),要看完整背景可以回那边查。
>
> 下面的**症状索引**是整份文档最重要的部分,出事时先看这个,不要从头
> 往下翻。

## 症状索引

### ArgoCD / GitOps 层(2026-08-23 追加)

### OPA 策略改了、ConfigMap 同步了、ArgoCD 全绿,但活的策略还是老的

**症状**:改了 `apps/opa/policy/trino.rego`,`opa test` 本地全过,push 之后
ArgoCD 显示 Synced/Healthy,`kubectl -n opa get cm opa-policy -o yaml` 里也
确实是新内容——但**权限行为一点没变**。

在权限策略上这个症状特别危险:它不会报错,只会**继续按老策略放行**。

**定位**:别看 ArgoCD,直接问活的 OPA 加载了什么:

```bash
kubectl -n opa port-forward svc/opa 18181:8181
curl -s localhost:18181/v1/policies | python3 -c "import json,sys; print(len(json.load(sys.stdin)['result'][0]['raw'].splitlines()))"
```

行数对不上,就是没重载。

**根因**:`opa run` **默认只在启动时加载一次策略**。2026-08-23 之前这个
仓库的 OPA 就没有 `--watch`——也就是说,历史上所有 OPA 策略改动,只有在
OPA Pod 恰好因为别的原因重启过之后,才真正生效过。

**处置**(已经修进 `apps/opa/manifests/deployment.yaml`,这里记的是当时
连续踩的三层):

1. 加 `--watch` —— 但**盯文件路径没用**。kubelet 更新 ConfigMap 挂载用的是
   原子替换 `..data` 符号链接,文件本身 inode 没变,盯具体文件的 inotify
   收不到事件。实测:改完等两分多钟,`/v1/policies` 里还是老的 288 行。
2. 改成盯目录 `--watch /policies` —— 直接 CrashLoopBackOff:
   `rego_type_error: multiple default rules data.trino.allow found at
   /policies/..2026_.../trino.rego:13, /policies/..data/trino.rego:13,
   /policies/trino.rego:13`。**OPA 不会自动跳过点开头的条目**,同一份策略
   被加载三遍。
3. 最终解:`--watch --ignore=..* /policies`。实测 push 之后 **48 秒**活的
   OPA 换成新策略,**Pod 没有重启**。

**顺带一条值得记的好消息**:第 2 步那次 CrashLoop 没有造成任何影响——
Deployment 的滚动更新策略让老 Pod 一直在跑,Trino 的鉴权全程正常。给
fail-closed 的组件配好 rollout,这次真的兜住了。

---

## 集群 / 节点层

- [CRD 太大报 "annotations too long",ServerSideApply=true 不是每次都管用](#crd-太大报-annotations-too-longserversideapplytrue-不是每次都管用)
- [prometheus-operator 起来了但一直不创建 StatefulSet,CPU 几乎是 0](#prometheus-operator-起来了但一直不创建-statefulsetprometheus-cr-的-status-一直是空的)
- [改 coredns-custom 加自定义域名解析,CoreDNS 直接 CrashLoopBackOff](#改-coredns-custom-加自定义域名解析coredns-直接-crashloopbackoff集群-dns-短暂中断)
- [`kubectl delete pod` 删 CNPG 的 Postgres pod 卡在 Terminating 十几分钟不退出](#kubectl-delete-pod-删-cnpg-的-postgres-pod-卡在-terminating-十几分钟不退出)
- [`k3s-uninstall.sh` 清不掉自定义 `--data-dir`,"推倒重建"其实什么都没建](#k3s-uninstallsh-清不掉自定义---data-dir推倒重建其实什么都没建)
- [cloud-full 这台机器的 k3s 集群不是这个项目独占的](#cloud-full-这台机器的-k3s-集群不是这个项目独占的)
- [idle-shutdown-watchdog 开机后用旧时间戳误判空闲,几分钟内把机器自己关掉](#idle-shutdown-watchdog-开机后用旧时间戳误判空闲几分钟内把机器自己关掉)
- [ArgoCD dex-server 在真实压力下 SIGSEGV,内存限制设小了](#argocd-dex-server-在真实压力下-sigsegv内存限制设小了)
- [改 resources 之后 ArgoCD 全绿,流量却还在旧 pod 上](#改-resources-之后-argocd-全绿流量却还在旧-pod-上)
- [`kubectl get pods -A` 里一大片 Error,但其实什么都没坏](#kubectl-get-pods--a-里一大片-error但其实什么都没坏)



### 改 resources 之后 ArgoCD 全绿,流量却还在旧 pod 上

**症状**:调大了某个 Deployment 的 memory,push、ArgoCD 显示 Synced/Healthy,
但行为完全没变 —— 因为**流量还在旧 pod 上,新的那个根本没建出来**。

**定位**:低配额命名空间 + `RollingUpdate`。滚动更新要新旧 pod **同时**占
配额,新旧加起来一超,新 ReplicaSet 就卡在 `exceeded quota`。这个失败是
**静默的**:Deployment 的 `.status` 里看得到,ArgoCD 的健康判断看不到。

```bash
kubectl -n <ns> describe rs | grep -i quota
kubectl -n <ns> get resourcequota -o yaml
```

**处置**:先算一下新旧加起来会不会超配额;超了就把这个 Deployment 改成
`strategy: Recreate`(mlflow 已经这么改了,它的命名空间只有 3Gi),或者
先调大配额。哪些命名空间是低配额,看
`platform/resource-quotas/manifests/quotas.yaml`。

**2026-08 实测**:真实卡了一个多小时才发现,全程 ArgoCD 是绿的。

### `kubectl get pods -A` 里一大片 Error,但其实什么都没坏

**症状**:开机后一看,几十个 Error 的 pod,探针、采集任务全红,像是塌了。

**定位**:**pods 列表里的 Error 是「过去某一刻」的快照,不是现在的状态。**
CronJob 会保留最近几次失败的 Job pod(`failedJobsHistoryLimit`),所以一次
短暂故障会在列表里留下一片红,而且会一直留着。

判断"现在到底好没好",看的是每个 CronJob **最近一次**那个 pod:

```bash
kubectl get pods -n <ns> --sort-by=.metadata.creationTimestamp --no-headers \
  | grep "<cronjob名>-" | tail -1
```

**处置**:最近一次是 `Completed` 就没事,清掉历史失败的即可:

```bash
kubectl delete pods -A --field-selector status.phase=Failed
```

**2026-08-29 实测**:停机前看到 50+ 个 Error(六条黄金链路探针全红、
OpenMetadata 采集全红),查下来全是两小时前 Trino coordinator 重启循环那个
窗口的残留 —— Trino 一倒,依赖它的探针和采集一起红;它恢复之后,下一轮
定时执行就都绿了。**当时如果按"列表里有红的"下判断,会去排查一个已经不
存在的故障。**

### ArgoCD / GitOps 层

- [git push 之后,ArgoCD 迟迟不应用新配置——标准排查步骤](#git-push-之后argocd-迟迟不应用新配置标准排查步骤)
- [Helm Application 手动 patch 过 Deployment 之后,ArgoCD 卡住不再应用新的 git 变更](#helm-application-手动-patch-过-deployment-之后argocd-卡住不再应用新的-git-变更)
- [从 apps/definitions 挪走一个组件后,ArgoCD 里那个 Application 卡在 Missing 删不掉](#从-appsdefinitions-挪走一个组件后argocd-里那个-application-卡在-missing-删不掉)
- [手动 `helm template | kubectl apply` 绕过 ArgoCD 之后,命名空间删不掉,卡在 Terminating](#手动-helm-template--kubectl-apply-绕过-argocd-之后命名空间删不掉卡在-terminating)
- [ArgoCD Application 显示 Healthy,但里面唯一的 Job 其实从来没跑过](#argocd-application-显示-healthy但里面唯一的-job-其实从来没跑过)
- [OPA 策略改了、ConfigMap 同步了、ArgoCD 全绿,但活的策略还是老的](#opa-策略改了configmap-同步了argocd-全绿但活的策略还是老的)
- [ArgoCD 卡在 "waiting for healthy state of ..." 不动,手动改了 values 也没用](#argocd-卡在-waiting-for-healthy-state-of--不动手动改了-values-也没用)
- [Airflow scheduler 反复长出两个并存 ReplicaSet、子 Application spec 一度没跟上 git——根因是 ArgoCD 控制面自己被 OOMKilled,不是 Airflow chart 的 bug](#airflow-scheduler-反复长出两个并存-replicaset子-application-spec-一度没跟上-git根因是-argocd-控制面自己被-oomkilled不是-airflow-chart-的-bug)
- [一个 Application 的 SyncError 卡住不动,`operationState.phase=Terminating` 这条标准处置有时不够,还要重启 argocd-application-controller](#一个-application-的-syncerror-卡住不动operationstatephaseterminating-这条标准处置有时不够还要重启-argocd-application-controller)
- [Application 卡在一个过期的旧操作快照上,不断把已经修复的配置改回旧值](#application-卡在一个过期的旧操作快照上不断把已经修复的配置改回旧值)
- [helm 有一个改不掉的 120 秒超时,index.yaml 太大导致全新集群装不上组件](#helm-有一个改不掉的-120-秒超时indexyaml-太大导致全新集群装不上组件)
- [`airflow-migrate-db` Job 打满 backoffLimit 后永久 Failed,不会自己重试,ArgoCD 也不会重建它](#airflow-migrate-db-job-打满-backofflimit-后永久-failed不会自己重试argocd-也不会重建它)
- [`bootstrap-all.sh` 跑一遍全部脚本报"完成",但实际上很多 Secret/初始化步骤都被跳过了](#bootstrap-allsh-跑一遍全部脚本报完成但实际上很多-secret初始化步骤都被跳过了)
- [ArgoCD 默认的 Ingress/Service 健康检查,在没有云 LoadBalancer 的裸机上永远卡在 Progressing](#argocd-默认的-ingressservice-健康检查在没有云-loadbalancer-的裸机上永远卡在-progressing)
- [ingress-nginx admission webhook 的 caBundle 被 selfHeal 清空,证书校验失败](#ingress-nginx-admission-webhook-的-cabundle-被-selfheal-清空证书校验失败)
- [argo-workflows CRD 安装依赖 Mac-only 代理地址,换台机器直接失败](#argo-workflows-crd-安装依赖-mac-only-代理地址换台机器直接失败)

### 网络与 Ingress 层

- [kubectl logs / exec 在这台机器上直接报 "Internal Privoxy Error"](#kubectl-logs--exec-在这台机器上直接报-internal-privoxy-error)
- [某些镜像仓库(如 quay.io)在这个网络下连不上,但 docker hub 是通的](#某些镜像仓库如-quayio在这个网络下连不上但-docker-hub-是通的)
- [推倒重建集群之后,ArgoCD/Trino/Superset/OpenMetadata/MLflow 这类做 OIDC discovery 的组件全部连超时](#推倒重建集群之后argocdtrinosupersetopenmetadatamlflow-这类做-oidc-discovery-的组件全部连超时)
- [Alloy 采不到日志:`loki.source.kubernetes` 拉不到数据,换成 hostPath 又报 "no such file or directory"](#alloy-采不到日志lokisourcekubernetes-拉不到数据换成-hostpath-又报-no-such-file-or-directory)
- [cloud-full 登录流程最后一步变成 502,ingress-nginx 报 "upstream sent too big header"](#cloud-full-登录流程最后一步变成-502ingress-nginx-报-upstream-sent-too-big-header)
- [kserve-controller/ingress-nginx-controller 镜像从没缓存到云主机,直连 registry 超时](#kserve-controlleringress-nginx-controller-镜像从没缓存到云主机直连-registry-超时)

### 存储与 S3A 层

- [Hive Metastore 建 Iceberg schema 报错 "Failed to create external path ... : null"](#hive-metastore-建-iceberg-schema-报错-failed-to-create-external-path---null)
- [Trino 建 Iceberg schema 时指定 location 会报错,不指定就正常](#trino-建-iceberg-schema-时指定-location-会报错不指定就正常)

### 认证 SSO 层

- [Keycloak start-dev 自带的 H2 是内存/临时数据库,pod 重启就把 realm 全部丢光](#keycloak-start-dev-自带的-h2-是内存临时数据库pod-重启就把-realm-全部丢光)
- [OpenMetadata 改了 OIDC 环境变量,`/api/v1/system/config/auth` 还是显示旧的 basic 认证](#openmetadata-改了-oidc-环境变量apiv1systemconfigauth-还是显示旧的-basic-认证)
- [OpenMetadata SSO 登录报 "Account already exists. Please contact administrator."](#openmetadata-sso-登录报-account-already-exists-please-contact-administrator不是配置问题是数据库里一条半损坏的用户记录)
- [cloud-full 上登录跳转回来是 404,连续挖出四层叠在一起的真实故障](#cloud-full-上登录跳转回来是-404连续挖出四层叠在一起的真实故障)
- [Superset OAuth 登录卡在最后一步,报 "Invalid URL 'openid-connect/userinfo'"](#superset-oauth-登录卡在最后一步报-invalid-url-openid-connectuserinfo)
- [Keycloak realm 从建立起就没有任何 client 配过 groups claim mapper,按组鉴权从来没真正生效过](#keycloak-realm-从建立起就没有任何-client-配过-groups-claim-mapper按组鉴权从来没真正生效过)
- [K8s 1.24+ 不会自动创建 service-account-token 类型 Secret,Argo Workflows 登录成功但调 API 一直 403](#k8s-124-不会自动创建-service-account-token-类型-secretargo-workflows-登录成功但调-api-一直-403)
- [ArgoCD 接 Keycloak OIDC,登录跳转到集群内部域名,浏览器打不开(已废弃,仅存档)](#argocd-接-keycloak-oidc登录跳转到集群内部域名浏览器打不开已废弃仅存档)

### K8s 资源生命周期 / NetworkPolicy

- [Helm chart 的 envFromSecrets(复数)不一定覆盖所有容器,initContainer 可能读不到](#helm-chart-的-envfromsecrets复数不一定覆盖所有容器initcontainer-可能读不到)
- [同一个命名空间里的 Job 连不上同命名空间的 pod,NetworkPolicy 报 "connection refused"](#同一个命名空间里的-job-连不上同命名空间的-podnetworkpolicy-报-connection-refused)
- [新建的 Job/CronJob 的 pod 刚起来第一次连接直接 Connection refused,但同样标签的 pod 手动测试是通的](#新建的-jobcronjob-的-pod-刚起来第一次连接直接-connection-refused但同样标签的-pod-手动测试是通的)
- [给 ConfigMap 新增一个 key 之后,subPath 挂载这个新 key 的文件在 pod 里变成了一个空目录](#给-configmap-新增一个-key-之后subpath-挂载这个新-key-的文件在-pod-里变成了一个空目录)

### 各组件专属故障

### 新增 Trino 服务账号后登录报 401 Invalid credentials

**症状**:`scripts/00-generate-secrets.sh` 打印"已追加: trino/trino-service-account
里的 xxx",Secret 里也确实有了,而用这个账号连 Trino 就是
`401 Access Denied: Invalid credentials`。

**原因**:`password.db` 是 **subPath 挂进 coordinator 的**,而 subPath 挂载的
Secret/ConfigMap **Kubernetes 永远不会更新**(见下一条)。跑着的 Trino 里
那个文件还是旧的。**从任何一层都看不出是"没生效"** —— Secret 对、脚本说
成功了、Trino 也没报错,只有登录的那一刻失败。

**处置**:重启 coordinator。`scripts/00` 现在会在新增过账号时自动做这件事,
但手动加账号的话要自己来:

```bash
kubectl -n trino rollout restart deploy/trino-coordinator
```

**注意 Trino 启动要几分钟**(startupProbe 预算 610s),重启期间所有查询会断。

### 改了 DAG(或任何 subPath 挂载的 ConfigMap)之后不生效,而且一切显示正常

**症状**:改完 DAG、push、ArgoCD 显示 Synced/Healthy、`kubectl get cm
airflow-dags -o yaml` 里也确实是新内容 —— 而 Airflow 的行为完全没变。
2026-08-29 实测遇到的具体表现:给 DAG 加了 `schedule`,`airflow dags
unpause` 成功、`is_paused=False`,而 `next_dagrun_run_after` 一直是 `None`。

**定位**:进 pod 里看那个文件本身。

```bash
kubectl -n airflow exec deploy/airflow-scheduler -c scheduler -- \
  grep -n "schedule=" /opt/airflow/dags/dbt_demo.py
```

看到的还是旧内容,就是这个问题。

**原因**:**subPath 挂载的 ConfigMap,Kubernetes 永远不会更新**——不是
「有延迟」,是根本不更新(官方文档明确写了这一条)。这里用 subPath 是有
原因的:挂整个 ConfigMap 目录会让 Airflow 3.x 的 DAG 遍历把 `..data` 软链
识别成递归循环直接崩溃退出。

**处置(已经做成自动的,一般不用手动)**:DAG 内容的哈希写进了
apiServer/scheduler/dagProcessor/workers 四处的 `podAnnotations`,由
`scripts/sync-airflow-dags-configmap.py` 生成、CI 用 `--check` 校验。
DAG 一改哈希就变 → pod 模板变 → ArgoCD 自动滚更。
急着生效可以手动:`kubectl -n airflow rollout restart deploy/airflow-scheduler
deploy/airflow-dag-processor`。

**同类风险**:仓库里任何用 subPath 挂 ConfigMap 的地方都有这个性质。
改那类配置之后,不要只看 ArgoCD 是不是绿的。

### Airflow 任务瞬间失败,日志里是 `exceeded quota: compute-quota`

**症状**:DAG Run 变红,任务 `start_date` 是空的(根本没启动),
scheduler 日志里:

```
pods "xxx" is forbidden: exceeded quota: compute-quota,
requested: limits.memory=512Mi, used: limits.memory=6016Mi, limited: 6Gi
```

**原因**:KubernetesExecutor 每个任务都要在 `airflow` 命名空间起一个
worker pod,和常驻组件共用同一份 ResourceQuota。**超配额不是排队,是任务
直接失败**——这一点和一般人对「配额」的直觉相反。

**处置**:调大 `platform/resource-quotas/manifests/quotas.yaml` 里 airflow
那份;同时看一眼 `environments/resource-profiles.yaml` 的
`airflow_worker_pod_*`,worker 规格和配额上限要一起算。

**这个坑这个仓库栽过四次**(2026-08-19 MLflow 改 resources、08-23
OpenMetadata 采集 Job 要 4Gi、08-28 OpenMetadata 命名空间、08-29 airflow)。
根子是同一个:配额按「当时跑着的东西」配,而任何新增的按需 pod 都从同一个
池子里扣。**看到 Job/任务「Running 0/1 但一个 Pod 都没有」或者秒失败,
先查配额。**

### DAG 报 `Variable ... does not exist`

**症状**:任务起来了但几秒内失败,栈顶是 `Variable.get(...)`。

**原因**:Airflow Variable 存在元数据库里,不在 git 里。库被重建/恢复过
之后就没了,而 `bootstrap-all.sh` 只在部署时跑一次。

**处置**:`./scripts/14-configure-airflow-seatunnel-variable.sh`(幂等)。
`airflow variables list` 能确认。

**2026-08-29 的真实经过值得记一笔**:发现它是因为把 DAG 从手动触发改成了
定时——在那之前那条 DAG 从来不自己跑,所以「Variable 全丢了」这件事一直
没有任何人和任何东西发现。**让东西自己跑起来,本身就是一种检测手段。**


- [Superset 报 ModuleNotFoundError: No module named 'psycopg2'(或 'authlib'、数据源驱动包)](#superset-报-modulenotfounderror-no-module-named-psycopg2或-authlib)
- [组件重新拉起来报 "password authentication failed",Postgres 密码"变了"](#组件重新拉起来报-password-authentication-failedpostgres-密码变了)
- [`KubernetesPodOperator` 拉起跨命名空间/自定义镜像的 Spark 任务,一路要闯好几关](#kubernetespodoperator-拉起跨命名空间自定义镜像的-spark-任务一路要闯好几关rbac日志流容器-uid模板变量)
- [Trino 新建 service account 之后连接仍然报 Invalid credentials,密码看着是对的](#trino-新建-service-account-之后连接仍然报-invalid-credentials密码看着是对的)
- [自建的 `python:3.12-slim` 薄应用 pip install 反复 exit 124,但 `curl` 从其他 pod 测同一个网络明明是通的](#自建的-python312-slim-薄应用-pip-install-反复-exit-124但-curl-从其他-pod-测同一个网络明明是通的)
- [`apt-get install` 卡死不动,`Acquire::Retries` 不管用](#apt-get-install-卡死不动acquireretries-不管用apt-自己的-delayed-item-重试队列是另一套机制)
- [postgres 镜像版本升级后,下游建库 Job 在 postgres 真正就绪前就打满重试次数](#postgres-镜像版本升级后下游建库-job-在-postgres-真正就绪前就打满重试次数)
- [`trino:483` 收紧了配置校验,chart 生成的属性和我们的配置冲突,新版本直接拒绝启动](#trino483-收紧了配置校验chart-生成的属性和我们的配置冲突新版本直接拒绝启动)
- [dbt_demo DAG 三个连续的根因性 bug:只读挂载、NetworkPolicy 漏名单、catalog.json 缺步骤](#dbt_demo-dag-三个连续的根因性-bug只读挂载networkpolicy-漏名单catalogjson-缺步骤)
- [排查方法论:看不到真实错误时,起一个和 DAG 配置完全一致的调试 Pod 比反复触发整条 DAG 快得多](#排查方法论看不到真实错误时起一个和-dag-配置完全一致的调试-pod-比反复触发整条-dag-快得多)

### 本机(colima)/ 云主机环境与脚本习惯

### 计费机器空转:靠"机器有没有在动"判断空闲,在这个平台上必然失效

**症状**:装了空闲自动关机的看门狗,机器还是长时间空转计费。查看门狗
日志会发现它每分钟都判定成"使用中",而实际上没有任何人在操作。

**原因**:这个平台自己就有一堆定时任务——每 5 分钟一轮的 `iam-sync`、
`opa-departments-sync`、`opa-grants-sync`、`device-events-producer`、
`trino-liveness-fix`,加上六条黄金链路探针。它们会同时打中"有网络流量"
和"有新出现的短命进程"这两类信号:kubelet 拉 Pod 要走网络,容器进程本身
就是新出现的短命进程。**平台越勤快,越判不出没人在用**。

换句话说,"机器有没有在动"和"有没有人在操作"在一个健康运行的平台上是
两回事,而计费该看的是后者。

**做法**:挑一个**只有人才会产生**的信号。这套环境里是 **SSH 上真实传输
的字节数**——人类/Agent 的操作全都走 SSH(`ssh` 执行命令、`scp`/`rsync`,
以及走 `ssh -L 16443:127.0.0.1:6443` 隧道的 `kubectl`,见
`scripts/32-start-cloud-vm.sh`),而集群内部的 CronJob / kubelet /
containerd 一个字节都不经过 sshd。

量法:`ss -tinH state established '( sport = :22 or dport = :22 )'` 把每条
连接的 `bytes_sent`/`bytes_received` 加起来,和上一分钟比差值;`ss` 不可用
时退回"所有 sshd 进程的 CPU 时间差值"(加解密要花 CPU,空闲 keepalive
几乎不消耗)。阈值取 20KB/分钟:SSH keepalive 每分钟只有几百字节,而一次
`kubectl get pods` 就是几十 KB。**连接重建会让计数器归零、差值变负,要
显式当成 0**,否则会误判成活跃。

**两个已经踩过的坑一并记着**:

1. 状态文件放 `/var/lib`(重启不丢)而只在安装时清空一次 ⇒ 停机几天后
   开机,第一次判定用的是几天前的时间戳,机器刚开机 2-3 分钟就自己关掉
   (2026-08-19 真实发生)。修法是加一个开机时重置状态的 oneshot service。
2. 用"文件最近有没有改动"当信号 ⇒ 自己 SSH 上去 `tail` 一次日志,日志
   轮转和 containerd 的后台写入就把它判成"使用中"(2026-08-16)。

**实测代价**:2026-08-28 夜里因为第二版失效,机器空转 2.5 小时,约 ¥10。

> 看门狗的安装脚本(`scripts/24-install-idle-shutdown-watchdog.sh`)按
> 仓库的开源定位过滤器不进 git(强绑定这台机器的运维设施),但上面这个
> 判据的取舍是通用的,所以记在这里。


### 云主机上想手动预拉镜像:用 `docker pull`,不是 `k3s ctr` / `crictl`

**症状**:大镜像被 kubelet 的拉取超时打断(`Failed to pull image ...
rpc error: code = Canceled desc = context canceled` → `ImagePullBackOff`),
想绕开 kubelet 直接在节点上拉,结果:

- `k3s ctr images pull` → `cannot access socket /run/k3s/containerd/containerd.sock`
- `crictl` → 不可用
- `ctr -a /run/containerd/containerd.sock -n k8s.io images pull` → **拉得下来,
  但白拉**:那个 containerd 的 `k8s.io` 命名空间是空的,kubelet 根本不用它

**原因**:cloud-full 的 k3s 是用 `--docker` 起的(cri-dockerd,见
`scripts/21-bootstrap-cloud-vm.sh`)。容器运行时是 **Docker**,镜像存在
Docker 那边(containerd 的 `moby` 命名空间),不在 `k8s.io` 命名空间里。
所以判断"某个镜像在不在节点上"要看 `docker images`,不是 `ctr images ls`。

**做法**:

```bash
ssh -i "$KEY" root@"$IP" "nohup docker pull '<镜像@digest>' > /root/pull.log 2>&1 &"
```

`docker pull` 没有 kubelet 那个进度超时,大镜像慢慢拉能拉完;拉完之后
Pod 的 `imagePullPolicy: IfNotPresent` 会直接用本地这份。

**怎么一眼确认运行时是哪个**:`ls /data/k3s/agent/` 里有 `cri-dockerd`
这个目录就是走 Docker。或者 `ctr -n k8s.io images ls -q | wc -l`,是 0
而集群里明明有一堆 Pod 在跑,就说明拉错 daemon 了。

**这次花的时间**:2026-08-28 升 Spark 4 时,镜像比原来大 350MB,被 kubelet
超时打断,然后在错误的 containerd 上拉了两轮才发现,约 20 分钟。


- [colima 会自动把 k3s LoadBalancer 的 80/443 转发到 Mac 的 localhost](#colima-会自动把-k3s-loadbalancer-的-80443-转发到-mac-的-localhost)
- [bash 脚本用 `set -euo pipefail`,给不存在的东西 `grep` 会让脚本"悄悄卡住"](#bash-脚本用-set--euo-pipefail给不存在的东西-grep-会让脚本悄悄卡住)
- [`03-configure-keycloak.sh` 遇到还没 unpark 的命名空间直接报错退出,后面的 client 全部没建成](#03-configure-keycloaksh-遇到还没-unpark-的命名空间直接报错退出后面的-client-全部没建成)
- [cloud-full 上 pip/kubectl 下载被限速到几十 KB/s,换阿里云镜像站秒装](#cloud-full-上-pipkubectl-下载被限速到几十-kbs换阿里云镜像站秒装)

### 已废弃 / 仅存档

- [ArgoCD 接 Keycloak OIDC,登录跳转到集群内部域名,浏览器打不开(已废弃,仅存档)](#argocd-接-keycloak-oidc登录跳转到集群内部域名浏览器打不开已废弃仅存档)

---

## 集群 / 节点层

### CRD 太大报 "annotations too long",ServerSideApply=true 不是每次都管用

- **症状**:`CustomResourceDefinition.apiextensions.k8s.io "xxx" is invalid:
  metadata.annotations: Too long: may not be more than 262144 bytes`。
  这个仓库里至少踩到过四次,都是同一个根因,不同 CRD:
  - `kube-prometheus-stack`(`prometheuses.monitoring.coreos.com` 等):
    ArgoCD 里长期 `OutOfSync`,`kubectl get crd prometheuses.monitoring.coreos.com`
    NotFound,Prometheus 的 Pod/StatefulSet 一直没创建出来。
  - `KServe`(`inferenceservices.serving.kserve.io`,ADR-027)。
  - `CloudNativePG`(`clusters.postgresql.cnpg.io` /
    `poolers.postgresql.cnpg.io`,ADR-038)。
  - cloud-full 首次拉起时,CloudNativePG/kube-prometheus-stack 又各中招
    一次(2026-08-16,journal)。
- **定位**:CRD 内嵌的 OpenAPI schema 太大,ArgoCD 默认走 client-side
  apply 会把整份 manifest 写进
  `kubectl.kubernetes.io/last-applied-configuration` 这个注解,超过 k8s
  单个注解 262144 字节的硬限制。给对应 Application 的
  `syncPolicy.syncOptions` 加 `ServerSideApply=true` 值得先试(免费、
  不会有副作用),但**不能假设它总能解决问题**——实测下来:
  - kube-prometheus-stack:加了**没用**,还是报一样的错(具体是 ArgoCD
    内部哪个环节导致的没有深究)。
  - KServe:加了**有用**,官方文档也推荐这么做,是唯一一次这个选项真的
    解决了问题。
  - CloudNativePG:加了**没用**,还是一样的错。
  三次里只有一次真的管用,经验上分不出规律(不是"越大的 CRD 越容易失败"
  ,KServe 和 CNPG 的 CRD 大小同一个量级)。
- **处置**:实际总是有效的处理是把 CRD 从 ArgoCD/Helm 的管理范围里摘
  出去,单独用原生 `kubectl apply --server-side` 装:
  ```bash
  ./scripts/04-install-kube-prometheus-crds.sh
  ./scripts/16-install-cloudnative-pg-crds.sh
  ```
  然后在 chart 的 values 里设 `crds.enabled: false`(kube-prometheus-stack)
  或 `crds.create: false`(CloudNativePG),CRD 用一次性脚本
  `kubectl apply --server-side --force-conflicts` 直接装,让 ArgoCD 只管 chart 本体
  (Deployment/CR 等),不再插手 CRD 的创建。这是和 ArgoCD 本身、
  `platform/root-app.yaml` 一样的"允许手动执行"的例外(见 ADR-005),
  升级 chart 版本、CRD schema 变化时需要重新跑一遍对应脚本。
  确认修好:`kubectl get crd <crd名>` 能查到、对应 Application 变成
  `Synced`/`Healthy`。
  - **教训**:遇到这个报错,先加 `ServerSideApply=true` 试一下,但不要
    卡在"为什么这个选项不管用"上深挖太久——三次里两次都不管用,与其
    排查 ArgoCD 内部机制,不如直接跳到"摘出管理范围、走一次性脚本"这个
    总是有效的方案。
- **涉及文件**:`platform/apps/kube-prometheus-stack.yaml`、
  `scripts/04-install-kube-prometheus-crds.sh`、
  `scripts/16-install-cloudnative-pg-crds.sh`。来源:正文历史记录 +
  ADR-027、ADR-038、journal 2026-08-16。

### prometheus-operator 起来了但一直不创建 StatefulSet,Prometheus CR 的 status 一直是空的

- **症状**:`kube-prometheus-stack-operator` Pod 是 `Running`/`Ready`,
  `kubectl top` 看 CPU 几乎是 0(说明它没在干活,不是卡在重活里)。
  `kubectl get prometheus` 能看到 CR,但 `.status` 一直是空对象,迟迟不
  出现对应的 StatefulSet/Pod。RBAC(`kubectl auth can-i`)检查全部正常,
  不是权限问题。
- **定位**:大概率是 operator 进程在某个初始化步骤(比如 informer 首次
  List/Watch)卡死了,但因为没配置 liveness probe,k8s 没检测出来去重启
  它,readiness probe 又恰好能过,所以外部看着"Running/Ready"其实内部
  没在正常工作。具体卡在哪一步没查(受限于下面"Internal Privoxy Error"
  那条,拿不到它的日志)。
- **处置**:直接 `kubectl delete pod -l app=kube-prometheus-stack-operator`
  让它重建,新 Pod 起来后几十秒内就正常创建出 StatefulSet 了。确认修好:
  `kubectl get statefulset -n <ns>` 能看到对应资源、`.status` 不再是空
  对象。以后再遇到"资源部署了但一直没有下游对象、CPU 几乎为 0"这种症状,
  先怀疑组件卡死,重启对应 Pod 试试,不用一直等。

### 改 coredns-custom 加自定义域名解析,CoreDNS 直接 CrashLoopBackOff(集群 DNS 短暂中断)

- **症状**:给 `kube-system/coredns-custom` 这个 ConfigMap(k3s 官方留的
  自定义 DNS 扩展点)加一段 `hosts { ... }` 配置,重启 coredns 之后整个
  CoreDNS Deployment 起不来,一直 `CrashLoopBackOff`,期间集群里所有 DNS
  解析(包括 `xxx.svc.cluster.local`)全部失效,是一次真正会波及全局的
  中断,不是某个业务组件自己的问题。
- **定位**:`crictl logs` 显示 `plugin/hosts: this plugin can only be
  used once per Server Block`。k3s 默认的 Corefile 主 `.:53` block 里
  已经有一个 `hosts /etc/coredns/NodeHosts {...}`,自定义配置又在同一个
  block 里加了第二个 `hosts {...}`,CoreDNS 不允许这样。
- **处置**:k3s 的自定义扩展点有两种导入方式,效果完全不同——
  `*.override` 文件导入到主 `.:53` block **里面**(会和已有插件冲突);
  `*.server` 文件导入到主 block **外面**,相当于新开一个独立的 server
  block。要新增 `hosts` 这类"整个 server block 只能有一份"的插件,必须
  用 `*.server`,给它配一个专门的 zone(比如
  `local-lite.test:53 { hosts {...} fallthrough } }`),不要用
  `*.override`。确认修好:`kubectl get pods -n kube-system -l
  k8s-app=kube-dns` 全部 Running。
  - **教训**:改跟 CoreDNS/DNS 相关的集群基础设施配置,风险等级和改
    业务组件的配置不是一个量级——一旦搞错会让整个集群短暂失明,动手前
    最好先确认清楚扩展机制,改完立刻验证,别的组件跟着重启排查之前先
    确认 CoreDNS 自己是不是先健康的。

### `kubectl delete pod` 删 CNPG 的 Postgres pod 卡在 `Terminating` 十几分钟不退出

- **背景**:ADR-041,给 Postgres 加 `priorityClassName` 之后,想通过删
  pod 触发重建来让字段生效,结果卡住了。
- **症状**:`kubectl delete pod postgres-cnpg-1 -n data` 之后,pod 一直
  停在 `Terminating`,`kubectl get events` 能看到 17 分钟前就有
  `Killing: Stopping container postgres` 这条事件,但迟迟没有真正终止。
- **定位**:`crictl logs` 进去看,postgres 进程本身已经在响应停止信号
  (持续拒绝新连接,报 `FATAL: the database system is shutting down`),
  不是进程完全没反应,只是没有真正完成关闭流程。没有深挖到底(不确定是
  CNPG 自己的 shutdown 钩子卡住,还是这台机器磁盘 I/O 慢导致 checkpoint
  flush 慢),但确认了一个关键背景:CNPG 默认的
  `terminationGracePeriodSeconds` 是 **1800 秒(30 分钟)**——
  `kubectl delete pod`(不加 `--force`)默认会一直等到这个宽限期结束才
  会强制杀掉,单实例、数据量很小的 Postgres 实测都能卡这么久,不是配置
  错误导致的异常长等待。
- **处置**:`kubectl delete pod <name> -n <ns> --grace-period=0 --force`
  跳过优雅关闭直接强杀。Postgres 自身的 WAL 崩溃恢复机制是为这种场景
  设计的,不是赌运气——实测新 pod 23 秒就变成 `1/1 Ready`、Cluster 状态
  回到 healthy,真实数据(`keycloak.user_entity` 表的行数)核对过和崩溃
  前一致,没有丢失或损坏。全程也确认了下游组件(Keycloak/Hive
  Metastore)的 pod 重启次数在操作前后没有变化,说明连接重试机制扛住了
  这段不可用窗口,没有级联故障。
  - **教训**:CNPG(或者任何设了很长 `terminationGracePeriodSeconds` 的
    有状态组件)卡在 `Terminating` 不一定是真的出问题了,先查一下这个
    字段的值,别死等——但也别一遇到"卡住"就本能地强杀,Postgres 能这么
    干是因为有崩溃恢复机制托底,不是所有卡在 `Terminating` 的组件都能
    安全地这样处理。

### `k3s-uninstall.sh` 清不掉自定义 `--data-dir`,"推倒重建"其实什么都没建

- **背景**:ADR-039 2026-08-22 补充。cloud-full 的 k3s 是用
  `--data-dir /data/k3s` 装的(见 `scripts/21-bootstrap-cloud-vm.sh`),
  想照搬 local-lite 上"推倒重建验证一键部署"的做法。
- **症状**:跑完 `k3s-uninstall.sh`、重新装回去,`node` 的 `AGE` 还是
  6d1h,所有 Application 和数据都"回来了"——看着像是重装成功,实际上
  一行新东西都没建过。**这个坑的危险之处不是"没删干净"**,是它会让人
  得出一个假的结论:"卸载重装一遍,全部 Synced/Healthy,一键部署验证
  通过"。
- **定位**:k3s 官方卸载脚本只处理默认数据目录路径,不知道这台机器用了
  自定义 `--data-dir`。实测:`k3s-uninstall.sh` 跑完之后
  `/data/k3s/server/db` 和 `/data/k3s/storage`(13 个 local-path PV)
  原封不动。
- **处置**:要从空开始必须显式清掉自定义数据目录。2026-08-22 真正执行
  时用的不是 `rm -rf`,而是 `mv /data/k3s /data/k3s.pre-teardown-20260822`
  ——同一个文件系统内改名,瞬间完成、不占额外空间,而且集群状态和所有
  PV 完整保留在备份目录里,回滚就是把目录名改回去。196G 的镜像在
  `/data/containerd` + `/data/docker`,不在被移走的目录里,所以重建
  不需要重新拉任何镜像。确认起点是真的空:`kubectl get ns` 只剩 4 个
  默认命名空间,`node` `AGE` 是几秒。
- **涉及文件**:`scripts/21-bootstrap-cloud-vm.sh`。来源:ADR-039。

### cloud-full 这台机器的 k3s 集群不是这个项目独占的

- **症状/风险**:在 cloud-full 上做任何"清空重建"类操作前,如果只想着
  "清掉我自己的东西",可能会连带影响到另一个并行项目。
- **定位**:`/data/k3s/storage` 里能看到
  `pvc-..._data-ai-platform-v2_control-api-data` 这类 PV 名——Codex 那个
  并行项目(`bigdata_ai_platform_v2`)和这个平台**共用同一个 k3s 集群**,
  不只是共用一台云主机。这条约束此前只记在 journal(2026-08-16)里,
  没进任何一份必读文档,做推倒重建规划时曾经没被想起来。
- **处置**:在 cloud-full 上做任何可能影响集群整体状态的操作
  (`k3s-uninstall.sh`、重启 k3s、清理 `/data/k3s` 等)之前,先确认
  Codex 那边当时是不是在用,取得明确授权再动手。2026-08-22 那次执行
  `k3s-uninstall.sh` 曾经导致集群短暂中断、Codex 项目也跟着中断,后来
  用 `scripts/21-bootstrap-cloud-vm.sh` 原样装回,数据因为 data-dir
  没被删而完整恢复,没有发生丢失,但过程是有惊无险,不是设计上安全。
- **来源**:ADR-039。

### idle-shutdown-watchdog 开机后用旧时间戳误判空闲,几分钟内把机器自己关掉

- **症状**:cloud-full 停机几天后重新开机,看门狗脚本第一次检查就直接
  把机器自动关掉了——机器刚开机 2-3 分钟就被自己关掉,来不及做任何事。
- **定位**:看门狗判断"是否已空闲超过阈值"用的时间戳是上次运行时留下的
  旧值,开机后第一次检查会拿几天前的旧时间戳去算,直接判定"已经空闲超过
  阈值",立刻触发关机。
- **处置**:2026-08-19 已修复,加了开机时重置状态的机制,确认修好:
  重新开机后看门狗不再在几分钟内自杀。
  - 这个脚本本身(`/usr/local/bin/idle-shutdown-watchdog.sh`)按项目
    "个人化/强绑定当前环境的运维脚本不进 git"的既定政策不进这个仓库,
    细节记在 `docs/journal/2026-08.md`,这里只记录症状和修复思路,方便
    以后在另一台机器上重新实现同款看门狗时避免同一个坑。
- **来源**:`docs/project/capability-matrix.md`(计费资源门禁一节)、journal 2026-08。

### ArgoCD dex-server 在真实压力下 SIGSEGV,内存限制设小了

- **症状**(2026-08-16,cloud-full 第一次拉起全套组件):`argocd-dex-server`
  在真实压力下崩溃重启。
- **定位**:`kubectl describe pod` / `lastState` 能看到 SIGSEGV,内存
  限制只有 128Mi,在这台机器同时拉起 30+ Application 的真实负载下不够。
- **处置**:把 dex-server 的内存限制调到 512Mi。确认修好:`kubectl get
  pod -n argocd -l app.kubernetes.io/name=argocd-dex-server` 不再反复
  重启。
- **来源**:journal 2026-08-16"任务#16"清单第 1 条。

---

## ArgoCD / GitOps 层

### git push 之后,ArgoCD 迟迟不应用新配置——标准排查步骤

这个问题反复出现了好几次(不只是某一个组件的特例),整理成一套标准检查
顺序,以后遇到直接按这个走,不用每次重新摸索:

- **症状**:改了 git 里的配置、push 之后,过了应该同步的时间窗口,
  Pod/Deployment 看起来还是旧的行为。
- **定位**(按顺序执行,不要跳步):
  1. `git -C <repo> log -1 --format=%H` 拿到本地最新 commit。
  2. `kubectl -n argocd get application apps-root -o jsonpath='{.status.sync.revision}'`
     看 app-of-apps 本身同步到了哪个 commit——**先确认这一层对了,再往下
     查**,这一层没追上,底下所有子 Application 的配置都不可能是最新的。
  3. apps-root 追上之后,再检查具体那个子 Application 的 spec 是不是
     最新值:
     `kubectl -n argocd get application <name> -o jsonpath='{.spec.source.helm.valuesObject.<字段路径>}'`
     ——**这一步经常被跳过,以为 apps-root 追上了子 Application 就一定
     跟着更新了,实际不是,子 Application 自己也可能要再刷新一次**。
  4. 确认 Application 的 spec 是最新的之后,再检查实际部署的
     Deployment/StatefulSet 有没有跟上
     (`kubectl get deploy <name> -o jsonpath='{.spec.template.spec...}'`)
     ——同样可能卡在下面"等待健康"死锁,需要手动删掉旧的
     Deployment/Pod 强制重建。
- **处置**:
  - 如果 apps-root 没追上:hard refresh(
    `kubectl -n argocd patch application apps-root --type merge -p
    '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'`),
    等几秒后重新比对 revision;如果连着刷新几次都不动,重启 repo-server
    (`kubectl -n argocd delete pod -l app.kubernetes.io/name=argocd-repo-server`)。
  - 不要只做"改一下 git、hard refresh 一次"就假设生效了去看 Pod
    状态——Pod 起不来的时候,先按上面 1-4 步确认问题出在哪一层,再决定
    下一步怎么修,能省很多来回。

### Helm Application 手动 patch 过 Deployment 之后,ArgoCD 卡住不再应用新的 git 变更

- **症状**:直接 `kubectl apply` 手动改过一个由 ArgoCD 管理的
  Deployment(比如为了打破"等待健康"死锁),之后即使改了 git 里的配置、
  hard refresh、强制 sync,Application 一直卡在
  `OutOfSync`/`Progressing`,Deployment 的实际内容长时间不更新,新的
  修复迟迟不生效。
- **定位**:手动 patch 制造的"实际状态"和 Git 期望状态之间的 diff,再加
  上 Deployment 本身处于不健康状态(旧问题还没解决),会让 ArgoCD 的
  多阶段同步逻辑卡在评估"这一步健康了没"上,新变更迟迟排不上号——是
  几类"死锁"问题的复合叠加,不是单一原因。
- **处置**:与其反复等 ArgoCD 自己收敛,不如直接把"这一轮手动 patch
  应该长什么样子"想清楚,一次性 `kubectl apply` 到位(包括所有相关的
  env/volume 引用都要改对,不要只改一半),用真实运行的报错
  (`crictl logs`)一步步验证,而不是干等 ArgoCD 的状态机自己转过来。

### 从 apps/definitions 挪走一个组件后,ArgoCD 里那个 Application 卡在 Missing 删不掉

- **症状**:把某个组件的 `.yaml` 从 `apps/definitions/` 挪到
  `environments/cloud-full/pending-definitions/`(按标准流程收起来)
  之后,`kubectl get applications -n argocd` 里那个 Application 一直
  显示 `OutOfSync`/`Missing`,删不掉,`apps-root` 自己也跟着卡在
  `OutOfSync`/`Progressing` 不收敛,hard refresh、重启 repo-server 都
  没用。
- **定位**:如果在组件还没在 git 里正式移除之前,已经手动清理过它的
  命名空间(比如 `kubectl delete namespace xxx`),这个 Application 的
  `finalizers`(`resources-finalizer.argocd.argoproj.io`)在真正被
  apps-root 判定要删除时,会尝试去确认"所管理的资源都清理干净了"才肯
  放行删除——但它认的资源列表可能是不完整/过期的,导致这个确认过程
  卡住,finalizer 一直移不掉,对象就一直卡在"正在删除"状态出不来。
  先确认这个组件真的没有任何残留资源在集群里(`kubectl get all -n
  <那个命名空间>` 查一遍)。
- **处置**:确认没有残留后,直接把这个 Application 的 finalizers 清空
  来放行:
  ```bash
  kubectl -n argocd patch application <名字> --type merge -p '{"metadata":{"finalizers":[]}}'
  ```
  这是"确认没有东西要清理,只是卡住了"这个前提下的合理操作,不是绕过
  安全检查——如果还没确认清楚集群里没有残留资源就这么干,可能会漏删
  东西。

### 手动 `helm template | kubectl apply` 绕过 ArgoCD 之后,命名空间删不掉,卡在 Terminating

- **症状**:为了绕开 ArgoCD 卡住的同步问题,直接用
  `helm template | kubectl apply` 手动把资源怼上去。后续要清理这个
  命名空间时,一直卡在 `Terminating`,`kubectl get all -n <ns>` 显示还
  有个 Job 也卡在 `Terminating`,而且这个 Job 根本不是自己写的(是 chart
  自带的 init job)。
- **定位**:chart 自带的资源里,有些标了
  `helm.sh/hook: post-install,post-upgrade` 这类注解(通常配合
  `init.jobAnnotations` 这种字段,本意是给 ArgoCD/Helm 的 hook 机制
  用)。手动 `kubectl apply` 把这种资源怼进一个 ArgoCD 正在管理的命名
  空间时,ArgoCD 会给它加上 `argocd.argoproj.io/hook-finalizer` 这个
  finalizer,但因为这个资源根本没有经过 ArgoCD 自己的 sync 流程,ArgoCD
  的控制器永远不会去"确认 hook 执行完毕"从而清掉这个 finalizer——变成
  一个永久卡住、删不掉的资源,拖着整个命名空间没法终止。
- **处置**:确认没有需要保留的东西之后,直接清空这个资源的 finalizers
  放行:
  ```bash
  kubectl -n <ns> patch job <name> --type merge -p '{"metadata":{"finalizers":[]}}'
  ```
  - **更根本的教训**:手动 `helm template | kubectl apply` 是排查问题
    时的应急手段,不是常规操作——用完之后要意识到可能留下这类"ArgoCD
    认识但没法正常管理"的资源,清理的时候要连带检查有没有卡住的
    finalizer,不能假设 `kubectl delete namespace` 一定能干净收尾。

### ArgoCD Application 显示 Healthy,但里面唯一的 Job 其实从来没跑过

- **症状**:一个 Application 只包含一个 Job(比如 `airflow-db-init`),
  Application 状态是 `Synced`/`Healthy`,但 `kubectl get jobs -n <ns>`
  什么都没有,该 Job 要做的事(比如建数据库用户)根本没发生。下游依赖
  这个 Job 结果的组件(比如 Airflow 的 Postgres 认证)会报"密码认证
  失败"之类的错,排查半天以为是密码不对,其实是 Job 压根没跑过。
- **定位**:给这个 Job 加了 `argocd.argoproj.io/hook: PostSync` 注解。
  PostSync hook 的触发条件是"这个 Application 里的常规(非 hook)资源
  先同步完成",但如果 Application 里**只有这一个 hook 资源、没有别的
  常规资源**,这个触发条件永远不成立,hook 实际上从来不会执行。ArgoCD
  判断 Application 是否 Healthy 时,没有常规资源可评估,于是直接报
  Healthy——一个空转的假健康状态,不代表任何东西真的跑成功了。
- **处置**:如果一个 Application 就是为了跑一个独立的 Job(不依赖同一
  Application 里其他资源的同步顺序),不要加 hook 注解,当成普通资源
  交给 ArgoCD 管理就行,靠 Job 自身的幂等逻辑 + `backoffLimit` 重试
  保证正确性。hook 只在"这个动作必须发生在同一 Application 内其他资源
  同步之前/之后"这种真实依赖关系时才需要。确认修好:`kubectl get jobs
  -n <ns>` 能看到这个 Job 真的跑过且 Completed。
- **涉及文件**:`apps/airflow/manifests/create-db-job.yaml`。

### ArgoCD 卡在 "waiting for healthy state of ..." 不动,手动改了 values 也没用

- **症状**:改了 Application 的 `helm.valuesObject`(比如换镜像源)、
  push 到 git、hard refresh、甚至手动触发 sync,Application 状态一直是
  `OutOfSync` + `Running`,`operationState.message` 显示
  `waiting for healthy state of apps/Deployment/xxx`,但
  `kubectl get deploy -o yaml` 看那个 Deployment 的镜像还是旧的,根本
  没被更新过。
- **定位**:组件的 Helm chart 里有依赖 Deployment 先变健康才继续执行的
  hook(比如 MinIO 的 `makeBucketJob`,建 bucket 前要等 MinIO 本身跑
  起来)。如果 Deployment 当前就是坏的(比如镜像拉不下来),ArgoCD 的
  多阶段同步会卡在"等这一步健康"上,永远等不到,新的镜像值也就没机会被
  应用下去——典型的先有鸡还是先有蛋。
- **处置**:先用 `kubectl set image deployment/<name> <container>=<新镜像>`
  手动把 Deployment 改成能跑起来的状态,打破这个死锁。等它变健康,
  ArgoCD 的 selfHeal 会自动接管,后续的 hook(如建 bucket 的 Job)也能
  正常触发,不用手动全部做完——通常再手动 sync 一次让 hook Job 重跑
  就行。

### Airflow scheduler 反复长出两个并存 ReplicaSet、子 Application spec 一度没跟上 git——根因是 ArgoCD 控制面自己被 OOMKilled,不是 Airflow chart 的 bug

- **背景**:2026-08-14,Feast 集成那一轮资源紧张期间发现。
- **症状**:`airflow-scheduler` 这个 Deployment 反复出现两个并存的
  ReplicaSet(都 `DESIRED=1`,CPU request 叠加顶到 99%,手动
  `kubectl scale --replicas=0` 也会被立刻纠正回来)。同一时期还发现子
  Application 显式触发 sync、`status.sync.status` 显示 `Synced`,但
  `.spec.source` 没有真的更新成最新 git 内容,要 `kubectl replace -f`
  整个替换才生效——比"git push 之后 ArgoCD 迟迟不应用新配置"那条更严重
  一层,这次连 ArgoCD **自己的** Application 对象本身都没跟上。
- **定位**:先怀疑是 Helm chart 渲染非确定性(`helm template` 同一份
  chart+values 本地连续渲染 8 次,逐字节 diff,**完全没有发现任何差异**
  ——排除了这个假设)。转向检查 ArgoCD 控制面本身状态:
  ```bash
  kubectl get pod argocd-application-controller-0 -o jsonpath='{.status.containerStatuses[0].lastState}'
  ```
  显示 `reason: OOMKilled, exitCode: 137`——真实发生过,不是猜测。更
  关键的是,即使在"相对安静"(Airflow/Trino 都已经 park 掉)的状态下,
  `kubectl top pod` 实测 controller 常驻内存高达 **1814Mi**,已经是当时
  2048Mi 限制的 88.6%。`argocd-repo-server` 同样查到过 OOMKilled 记录
  (`lastState.reason` 是 `OOMKilled`,即使 `exitCode` 显示 0——k8s 对
  这类情况的上报本身不总是一致,不能只信 `exitCode` 字段),但它的
  limits 一直只有 512Mi,且从未有实测数据支撑过这个数字。
- **结论**:两个现象大概率是同一个根因的两种表现——ArgoCD 控制面
  (controller + repo-server)在这台机器 25+ 个 Application 的真实规模
  下持续吃紧,批量 sync/渲染大 chart(Airflow 官方 chart 不小)时的内存
  峰值远超之前配的上限,被自己的资源限制误杀,导致 in-flight 的状态
  更新/渲染被中断,表现出各种"不一致"的症状,不是某个具体组件的 bug。
- **处置**:`platform/bootstrap/argocd-values.yaml` 里 controller 的
  limits 从 2048Mi 调到 3072Mi、repoServer 从 512Mi 调到 1024Mi(只调
  limits,不调 requests——整体内存基线已经在 86% 上下,大幅调高
  requests 会挤占其他组件的可调度余量,风险更大)。改完用
  `NEEDS_LOCAL_PROXY=1 ./scripts/01-bootstrap-argocd.sh` 重新
  `helm upgrade`(**不要手动裸跑 `helm upgrade` 命令,容易漏带这台机器
  必需的代理 overlay 参数**——排查过程中就真的漏带过一次,虽然后来验证
  发现即使没有代理这台机器当时也能连上 GitHub,但那只是巧合,不能当成
  可以跳过这个参数的依据,一切以脚本记录的标准流程为准)。升级后验证
  过:所有 Application 仍是 `Synced/Healthy`,repo-server 新 pod 强制
  hard-refresh 后能正确同步到最新 git commit,代理 env 也确认还在。
  - **没有解决的部分**:这只是把 ArgoCD 自己"被自己的资源上限误杀"这个
    问题缓解了,**不是把这台机器物理内存不够的结构性问题解决了**——
    colima 当时 11GB 的空载基线就已经 86%+,以后如果同时启用的重量级
    组件更多,这两个数字大概率还要继续往上调。**后续更新**:2026-08-14
    当天 colima 从 11G/4vCPU 扩到 13G/6vCPU,`feast_materialize` 这个
    DAG 最终在稳定资源下跑出了成功记录,完整的排查链路(一共 9 个独立
    的坑,资源抢占只是其中一个)见
    [ADR-042](../decisions/042-feast-feature-store.md)
    "2026-08-14:端到端验证真正跑通"那一节。

### 一个 Application 的 SyncError 卡住不动,`operationState.phase=Terminating` 这条标准处置有时不够,还要重启 argocd-application-controller

- **症状**:某个 Application 的 `status.conditions` 里挂着一条几天前的
  `SyncError`(比如"某个资源的 `resourceVersion` 冲突"),
  `kubectl patch application <name> --type merge -p
  '{"status":{"operationState":{"phase":"Terminating"}}}'` 终止卡住的
  旧操作、再手动触发一次新 `sync` 之后,状态依然是 `OutOfSync`,而且
  报错时间戳变成了刚才这次重试的时间(不是几天前那条缓存的旧消息)——
  说明确实在重新尝试,但还是同一个错误。
- **定位**:即使卡住的旧 `operationState` 已经清掉,`argocd-application-
  controller` 自己维护的一份对象缓存也可能是陈旧的,新的 sync 尝试用
  这份陈旧缓存里记的 `resourceVersion` 去 PATCH 一个可能已经不存在、
  或版本已经变了的资源,自然又失败一次——这是应用层面(Application 的
  operationState)和控制器进程内存缓存两层不同的"卡住",只清前一层
  不够。
- **处置**:
  ```bash
  kubectl -n argocd rollout restart statefulset/argocd-application-controller
  ```
  (这个仓库用的 ArgoCD 部署方式,application-controller 是 StatefulSet
  不是 Deployment,先确认清楚再重启,别用错资源类型报 NotFound 就以为
  没这个组件),等它重新 Ready、缓存重建后再 hard refresh + 触发一次
  sync。如果那个具体的 OutOfSync 资源本身内容不重要/能安全重建(比如
  `ValidatingWebhookConfiguration` 这类无状态配置),直接
  `kubectl delete` 掉它再让 ArgoCD 重新创建,比反复重试"patch 一个卡在
  奇怪状态的资源"更省事。
- **涉及文件**:无(纯运维操作,不改任何 git 里的 manifest)。

### Application 卡在一个过期的旧操作快照上,不断把已经修复的配置改回旧值

- **背景**:2026-08-16 深夜,journal。和上一条"SyncError 卡住"不是同一
  个具体故障,但同属"ArgoCD 自身状态卡住"这一大类,容易混淆,分开记录。
- **症状**:Application 的 `.status.operationState` 卡在一个**过期的**
  同步操作快照上——即使 Application 的 `.spec`/`comparedTo` 已经反映了
  最新的 git 提交,这个卡住的旧操作仍然在不断 retry,每次 retry 都用
  它自己缓存的旧 source 把已经修好的 Deployment 改回旧版本。实测抓到
  过一次:刚把 Superset 的 startupProbe 改到 90 次阈值、新 pod 也已经
  Ready 了,几分钟后又被这个卡住的旧操作悄悄改回 60 次阈值,又开始重复
  "pip 装到一半被强杀"的循环。
- **定位**:`kubectl -n argocd get application <name> -o yaml` 看
  `.status.operationState.startedAt` 是不是明显早于最近一次预期的
  sync,同时观察 Deployment 的字段是否在"修好"之后又被莫名改回旧值。
- **处置**:
  ```bash
  kubectl patch application <name> -n argocd --type merge -p \
    '{"status":{"operationState":{"phase":"Terminating"}}}'
  ```
  终止卡住的旧操作,再手动触发一次新的 `.operation.sync`(**不能只指望
  selfHeal 会自动重新触发**,实测等了将近一分钟没有自动发生)。这不是
  某次改动引入的新问题,是 ArgoCD 本身已知的一类 flaky 行为(和
  kube-prometheus-stack Application 长期卡 Unknown 是同一大类),记在
  这里方便以后遇到同样症状时能认出来,不用每次都重新排查一遍。
- **来源**:journal 2026-08-16 深夜"顺带发现并处理了一类 ArgoCD 稳定性
  问题"一节。

### helm 有一个改不掉的 120 秒超时,index.yaml 太大导致全新集群装不上组件

- **背景**:[ADR-061](../decisions/061-vendor-grafana-charts.md),
  2026-08-22。`alloy`/`loki`/`kube-prometheus-stack` 三个 Application
  长期 `Sync Status = Unknown`。因为底层 Pod 一直 Healthy,这件事一度
  被当成"只是状态显示不准",挂在 BACKLOG 里很久没人动。
- **症状**:量化之后发现判断错了——在一个**全新**集群上,这几个
  Application 根本装不起来,一键部署会直接断在这里。这件事此前从没
  暴露,是因为现有集群是增量长出来的(chart 早就拉下来过了,Pod 一直
  活着,没人注意到"再也拉不到新的了")。
- **定位**:
  - 传统 Helm 仓库每次同步都要先拉整个 `index.yaml`。
    `grafana.github.io/helm-charts` 那份 **4.0MB**,
    `prometheus-community.github.io/helm-charts` 也在同一量级。
  - 从这台境内云主机实测下载速度约 **12KB/s**(同一时刻,同一台机器上
    拉具体的 chart tgz 只要 4.4 秒——慢的是那个巨型 index,不是整条出口
    链路都不通)。
  - repo-server 日志里 `time_ms=120030`,**每次都精确卡在 120 秒**。而
    `ARGOCD_EXEC_TIMEOUT` 明明配的是 180s(实测 `kubectl get deploy
    argocd-repo-server` 确认过)。也就是说这 120 秒是 **helm 自己的
    HTTP 超时,ArgoCD 调不到**——这个问题**没法靠调大 ArgoCD 的任何
    超时解决**。这一点值得单独强调,因为"只调大超时忍着"是最容易被
    想到、也最容易被当成"已经想过了"而写进 backlog 的方案,实际根本
    不成立。
- **处置**:分两种情况,不搞一刀切:
  1. 上游有官方 OCI 仓库的:换 OCI,比如
     `kube-prometheus-stack` → `oci://ghcr.io/prometheus-community/charts/kube-prometheus-stack`。
     OCI 不需要 index,按名字+版本直接取 manifest。实测:894KB 的 chart
     几秒拉完,`helm template` 184ms,Application 从 `Unknown` 变成
     正常比较。
  2. 上游没有 OCI 的:vendor 进仓库。Grafana 目前没把 chart 发到 OCI
     (`ghcr.io/grafana/helm-charts/alloy`、`ghcr.io/grafana/charts/alloy`、
     `ghcr.io/grafana/alloy` 三个候选路径实测全是 403 / not found),
     所以 `alloy` 和 `loki` 用 `scripts/28-vendor-helm-chart.sh` 原样
     解包进 `platform/alloy-chart/` 和 `platform/loki-chart/`,
     Application 改成从这个 git 仓库的 `path:` 读(体积可接受:两个
     chart 加起来 379 个文件、约 200KB 压缩)。
  - **为什么不选"镜像到自己的 GHCR OCI 仓库"**:技术上直接,但 GHCR
    package 默认私有,要么需要人去 UI 上手动改成 public,要么得给
    ArgoCD 配拉取凭据——多一个"换台机器/换个账号就要重做一遍"的手工
    步骤,和"一键部署"的目标相反。
  - **确认修好**:对应 Application 变成正常的 `Synced`/`Healthy`,不再
    是 `Unknown`。
  - **顺带解决的**:vendor 进来的 chart 不需要任何外网访问就能部署,对
    "生产环境可能没有外网"这个场景是实质性的改善。升级 chart 变成显式
    动作(改 `scripts/28-vendor-helm-chart.sh` 的调用参数重跑,然后
    review diff),chart 版本记在各自的 `VENDORED.md` 里。
- **涉及脚本**:`scripts/28-vendor-helm-chart.sh`。

### `airflow-migrate-db` Job 打满 backoffLimit 后永久 Failed,不会自己重试,ArgoCD 也不会重建它

- **背景**:ADR-039 2026-08-22 补充,cloud-full 真正推倒重建那一次抓到。
- **症状**:全新集群上,`airflow-migrate-db` 和 Postgres(CNPG 建集群)、
  `airflow-create-db`(建库)是被 ArgoCD 同时创建的。库还不存在的那一两
  分钟里 `airflow db migrate` 连着失败三次打满 `backoffLimit`,Job 变成
  永久 `Failed`。结果 `airflow-db-init` 永远 `Degraded`。
- **定位**:`kubectl get job airflow-migrate-db -n airflow` 看
  `status.failed` 是不是等于 `backoffLimit`;确认 Job 一旦 Failed 就
  不会自己重试,manifest 也没变,在 ArgoCD 看来"已经 in-sync",不会主动
  重建它。
- **处置**:必须人工 `kubectl delete job airflow-migrate-db -n airflow`
  触发重建。**根本修法不是调大 backoffLimit**(那只是把竞态窗口拉宽,
  没解决问题),是先等依赖就绪再开始算重试次数:加了一个有上限的
  `airflow db check` 等待循环,确认 Postgres 真的可连接之后才开始跑
  `migrate`。确认修好:全新集群从零构建时这个 Job 不再需要人工介入。

### `bootstrap-all.sh` 跑一遍全部脚本报"完成",但实际上很多 Secret/初始化步骤都被跳过了

- **背景**:ADR-039 2026-08-22 补充,同一次 cloud-full 推倒重建测试里
  抓到的第二个、更严重的坑。
- **症状**:两个原因叠加,导致"20 步全过、EXIT=0"和"平台实际能用"是
  两回事:
  1. `scripts/00-generate-secrets.sh` 往十几个 namespace 里塞 Secret,
     但那些 namespace 是 ArgoCD 同步各 Application 时用
     `CreateNamespace=true` 建的。脚本跑到这一步时它们一个都不存在,
     脚本逐个打印"跳过"然后过去。于是 oauth2-proxy / spark-history-server
     / table-registration-app / feast 起来之后一直
     `CreateContainerConfigError`(`secret "minio-root" not found`
     这类),**没有任何东西会回头补建**。
  2. "建 Airflow 账号""配 Superset 数据源"这些组件专属初始化步骤紧跟
     在配 Keycloak 后面,那时候组件一个都没起来,好几个步骤全部打印
     "跳过"——**脚本报"全部完成",实际一件都没做**。
- **定位**:`bootstrap-all.sh` 的日志里逐条搜"跳过"/"skip",数一下有
  多少步是因为目标 namespace/组件还不存在而跳过的,不能只看最后的
  `EXIT=0`。
- **处置**:插两步:
  1. `wait_for_namespaces`(目标 namespace 列表从 Application 的
     `destination.namespace` 自动推导,不用手工维护)之后重跑
     `scripts/00` 补 Secret;
  2. `wait_apps_converged` 等收敛,然后才做组件专属初始化。
  两个等待都是超时只警告不中止——有些 Application 本来就要等后面的
  初始化步骤跑完才健康,死等会锁死。确认修好:重跑一遍验证,20 步全过、
  零跳过,之前缺的 5 个 Secret 全部建上。

### ArgoCD 默认的 Ingress/Service 健康检查,在没有云 LoadBalancer 的裸机上永远卡在 Progressing

- **背景**:2026-08-16,cloud-full 首次拉起,journal"任务#16"清单第
  8 条。
- **症状**:一些依赖 Ingress/Service 健康状态判断的 Application,在
  cloud-full(裸机 k3s,没有云厂商 LoadBalancer)上永远卡在
  `Progressing`,不会变成 `Healthy`。
- **定位**:ArgoCD 默认的健康检查逻辑假设 `LoadBalancer` 类型的
  Service/Ingress 会拿到一个外部 IP 才算健康,裸机环境下永远拿不到。
- **处置**:加自定义健康检查(Lua 脚本)覆盖 ArgoCD 对这类资源的默认
  判断逻辑,不再等外部 IP。确认修好:对应 Application 变成
  `Synced`/`Healthy`。

### ingress-nginx admission webhook 的 caBundle 被 selfHeal 清空,证书校验失败

- **背景**:2026-08-16,cloud-full 首次拉起,journal"任务#16"清单第
  9 条,也是这次验证收尾时 `ingress-nginx` 长期 `OutOfSync/Healthy`
  的原因(不影响功能,是已知的展示问题)。
- **症状**:`ingress-nginx` 的 `ValidatingWebhookConfiguration` 上的
  `caBundle` 会被清空,导致证书校验失败。
- **定位**:这个 chart 的 admission webhook `caBundle` 是通过一个
  命令式的 Job 事后 patch 注入的,不在 Helm 渲染的期望状态里——ArgoCD
  的 selfHeal 机制发现"实际状态和期望状态不一致"就会把它清空回期望
  状态(也就是空值)。
- **处置**:给这个字段加 `ignoreDifferences`,让 ArgoCD 不要拿这个字段
  去做 selfHeal 比较。确认修好:webhook 证书校验不再失败,`ingress-nginx`
  Application 的 `OutOfSync` 只剩这一个已知、不影响功能的字段级 drift。

### argo-workflows CRD 安装依赖 Mac-only 代理地址,换台机器直接失败

- **背景**:2026-08-16,cloud-full 首次拉起,journal"任务#16"清单第
  2 条。
- **症状**:argo-workflows chart 默认靠一个 pre-install Job 从
  `raw.githubusercontent.com` 实时下载 CRD,这台云主机上这个 Job 失败。
- **定位**:Job 配的 `HTTP_PROXY` 是 colima 宿主机专用地址
  (`192.168.5.2:1087`),cloud-full 连不上这个地址,不是 GitHub 本身
  连不上。
- **处置**:把 8 个 CRD(注意是 8 个,不是 7 个——`workflowtemplates`
  这个和 `clusterworkflowtemplates` 长得像,第一版漏了)vendor 进
  `apps/argo-workflows-crds/manifests/`,chart 里关掉 `crds.install`,
  新增 `scripts/25-install-argo-workflows-crds.sh` 一次性装好,不再
  依赖任何网络。确认修好:全新集群上不需要连 `raw.githubusercontent.com`
  也能装好这 8 个 CRD。

---

## 网络与 Ingress 层

### kubectl logs / exec 在这台机器上直接报 "Internal Privoxy Error"

- **症状**:`kubectl -n <ns> logs ...`、`kubectl -n <ns> exec ...` 这类
  需要直连 kubelet(端口 10250)的操作,无论有没有设置
  `HTTP_PROXY`/`NO_PROXY` 环境变量都会报错,错误里能看到实际在请求
  `https://192.168.5.1:10250/...`,返回 `Internal Privoxy Error`。就算
  从 colima 虚拟机内部执行同样命令也一样报错。
- **定位**:这台 Mac 上装的代理工具(从报错特征看是走 Privoxy 的那一类,
  比如 Surge / ClashX 的增强模式)在**系统网络层**做透明拦截,不是靠
  进程读 `HTTP_PROXY` 环境变量生效的,所以在 shell 里 unset 代理变量
  没用——连虚拟机自己发出的流量都被拦了。它把发往 `192.168.5.0/24`
  (colima 虚拟网络的私网段)这种内网地址的流量也当成"要走代理"处理,
  但代理软件自己又不知道怎么路由到这种私网地址,所以报错。
- **处置**:
  - **需要用户处理的部分(命令行无法修复)**:在代理工具里给
    `192.168.5.0/24`(colima 的虚拟网络段,网段变了以此类推)、
    `10.0.0.0/8` 这类私网地址加"直连/不代理"的规则,或者调试期间临时
    关掉增强模式/TUN 模式。加完规则后 `kubectl logs`/`kubectl exec`
    应该就正常了。
  - **临时绕过办法(不需要用户处理时)**:改用不经过这条路径的诊断
    方式——`kubectl describe`、`kubectl get -o yaml`(看
    `.status`/`.status.conditions`)、`kubectl get events`、`kubectl
    top`、`kubectl auth can-i`,这些都走 API server 的常规请求路径,
    不受影响。真的需要看容器内部日志时,先怀疑"组件是不是卡死了",
    直接重启 Pod 往往比死磕日志更快。更彻底的绕过:
    `colima ssh -- sudo crictl ps -a` 找到容器 ID,
    `colima ssh -- sudo crictl logs <id>` 直接走本地 containerd
    socket,完全不碰网络。
  - **2026-08-09 补充**:装 Loki+Alloy 集中日志时,Alloy 的
    `loki.source.kubernetes` 组件(通过 K8s API 的 `containerLogs`
    接口拉日志,原理和 `kubectl logs` 完全一样)在集群**内部**照样踩了
    同一个坑——不是只有本机的 `kubectl` 会被拦,任何组件只要走
    API server -> kubelet 这条 `containerLogs` 代理路径,都会被同样
    拦截。处理方式是从设计上完全绕开这条路径,见下面 Alloy 那条记录,
    不是加代理白名单就能一劳永逸解决的(因为拦截规则的具体网段/范围
    不受我们控制)。

### 某些镜像仓库(如 quay.io)在这个网络下连不上,但 docker hub 是通的

- **症状**:Pod 一直 `ImagePullBackOff`,事件里是 `TLS handshake
  timeout` 或下载到一半 `EOF`。`colima ssh` 里直接 `curl` 目标 registry
  也是 `Connection timed out`,换成 `https://registry-1.docker.io/v2/`
  测试却能正常返回(401 也算通,那是正常的匿名访问被拒)。
- **定位**:本机代理对不同站点的连通性不一致,quay.io 这类站点即使走
  代理也可能连不上,不是配置错误,是这条网络对特定站点没有稳定路由。
  先用 `colima ssh -- curl` 测一下目标 registry 通不通,别默认怀疑是
  k8s 配置问题。
- **处置**:很多镜像(包括 MinIO)官方会同时发布到 docker hub 和
  quay.io,遇到这种情况直接把 chart/manifest 里的 `image.repository`
  换成 docker hub 上的同名镜像,不用死磕代理配置。

### 推倒重建集群之后,ArgoCD/Trino/Superset/OpenMetadata/MLflow 这类做 OIDC discovery 的组件全部连超时

- **背景**:ADR-039,真的删掉本机 colima VM 重建一次才第一次暴露。
- **症状**:`curl http://keycloak.local-lite.test/...` 之类的请求 5 秒
  超时(`Connection timed out`),不是报错,是单纯连不上——但 DNS 解析
  本身"成功"了,能拿到一个 IP。
- **定位**:`platform/coredns-custom/` 把 `*.local-lite.test` 硬编码
  指向 `ingress-nginx-controller` 这个 Service 的 ClusterIP 字面量。
  ClusterIP 是集群创建时按 Service CIDR 分配的,不是固定不变的
  值——重建集群后 `ingress-nginx-controller` 分到了一个新的
  ClusterIP,旧的硬编码值就是一个查得到但完全连不上的废 IP。
- **处置**:改用 CoreDNS 的 `rewrite name regex ... answer auto` 把
  查询重写成 `ingress-nginx-controller.ingress-nginx.svc.cluster.local`,
  交给同一个 server block 里的 `kubernetes` 插件动态解析,不管
  ClusterIP 怎么变、集群重建几次都不用再改这份配置。
  - **教训**:任何写死 ClusterIP 字面量的配置都是定时炸弹,只是触发
    条件(集群重建/该 Service 被删重建)平时很少见——`kubectl get svc`
    之类的命令确认过"IP 对不对"不代表这份配置本身是对的,要看它是不是
    把 IP 写死进了另一份配置文件里。

### Alloy 采不到日志:`loki.source.kubernetes` 拉不到数据,换成 hostPath 又报 "no such file or directory"

- **背景**:见 ADR-020。装 Loki + Grafana Alloy 做集中日志采集时连续
  踩了两个坑。
- **症状与定位**:
  - **坑一**:官方更推荐的 `loki.source.kubernetes` 组件一条数据都
    拉不到,报 `Internal Privoxy Error`——这是本机代理拦截问题(见上面
    那条记录),不是 Alloy 配置错误。
  - **坑二**:换成 hostPath 之后,虽然
    `/var/log/pods/<ns>_<pod>_<uid>/<container>/0.log` 这个路径本身
    是存在的,但 Alloy 报 `stat ...: no such file or directory`。用
    `crictl exec ... ls -la` 进容器实际查看才发现:这个 `0.log` 是个
    **符号链接**,指向
    `/var/lib/docker/containers/<容器id>/<容器id>-json.log`——colima
    这个 profile 的容器运行时是 docker(用 docker 的 json-file 日志
    驱动写日志),不是纯 containerd 直接写文件。只挂了 `/var/log` 的话,
    这条符号链接在容器里指向一个够不着的路径,`stat` 自然失败。
- **处置**:
  - 坑一:改用 `discovery.kubernetes`(只拿 pod 元数据做 relabel)+
    `local.file_match` + `loki.source.file`,日志内容直接从 hostPath
    挂载的宿主机文件读,不经过 K8s API 的 `containerLogs` 接口。
  - 坑二:Alloy chart 的 `alloy.mounts.dockercontainers: true` 把
    `/var/lib/docker/containers` 也挂进去,两个 mount 都要开
    (`varlog` + `dockercontainers`),缺一个都不行。
  - **教训**:这类"文件路径存在,但读不到内容"的问题,先用
    `crictl exec ... ls -la <path>` 进容器实际看一眼(不要只看宿主机上
    文件存不存在),符号链接指向哪里一目了然,比看 chart 文档去猜"是不
    是该开某个 mount 开关"快得多。

### cloud-full 登录流程最后一步变成 502,ingress-nginx 报 "upstream sent too big header"

- **背景**:2026-08-16 深夜,journal,是下面"cloud-full 上登录跳转回来
  是 404"那条四层故障链的第 4 层,单独摘出来是因为这个具体症状
  (502 + upstream 头太大)本身值得单独按报错文本检索。
- **症状**:SSO 登录前几步都修好之后,流程走到最后一步变成 502,
  `ingress-nginx` 日志报 `upstream sent too big header`。
- **定位**:`kubectl -n ingress-nginx logs <controller pod>` 确认报错
  文本和是哪个 Ingress 触发的。
- **处置**:oauth2-proxy 在 `/oauth2/callback` 种的 Set-Cookie 带着
  完整 JWT(access/id/refresh token 三个),超过 nginx 默认响应头缓冲
  区。全局调大 `proxy-buffer-size`/`proxy-buffers-number`,不用逐个
  Ingress 加 annotation。确认修好:用 curl+cookie jar 走完整登录流程,
  能拿到最终落地页而不是 502。

### kserve-controller/ingress-nginx-controller 镜像从没缓存到云主机,直连 registry 超时

- **背景**:2026-08-16,cloud-full 首次拉起,journal。
- **症状**:`kserve-controller-manager`/`ingress-nginx-controller` 两个
  镜像 `ImagePullBackOff`,直连 `registry.k8s.io`/`docker.io` 超时。
- **定位**:这两个镜像之前从没缓存到云主机上(local-lite 上一直有,
  云主机是第一次需要),而这台境内云主机直连这两个 registry 本身就慢/
  不稳定。
- **处置**:通过国内镜像站(`k8s.m.daocloud.io`)拉取再 retag。
  - **kserve-controller 这个还额外发现一个独立问题**:chart 默认
    `imagePullPolicy: Always`,即使本地已经有镜像,kubelet 每次调度
    还是会去连 `registry-1.docker.io` 校验,同样导致
    `ImagePullBackOff`——改成 `IfNotPresent`。**改的过程中还踩了一次
    自己的坑**:第一版改 values 层级写错了,导致生成了
    `map[imagePullPolicy:IfNotPresent]:v0.19.0` 这种非法镜像名,第二次
    commit 才改对——这是"改完一定要跑 `helm template` 确认渲染结果,
    不能只看 diff 顺眼"的一个真实反例。

---

## 存储与 S3A 层

### Hive Metastore 建 Iceberg schema 报错 "Failed to create external path ... : null"

- **症状**:Trino 里 `CREATE SCHEMA iceberg.xxx WITH (location =
  's3://...')` 报
  `Failed to create external path s3://... for database xxx. This may
  result in access not being allowed if the
  StorageBasedAuthorizationProvider is enabled: null`,错误信息里那个
  `null` 没有任何有用的堆栈信息。
- **定位**:SQL 层面的报错信息把真正原因盖住了,得去 Hive Metastore
  自己的日志(`grep -i "s3a\|MetaException"`)才能看到 `Failed to
  create external path` 这条更具体的 metastore 侧报错。
- **原因**:以为给 Hive Metastore 的 `SERVICE_OPTS` 加
  `-Dfs.s3a.endpoint=...` 这类 JVM 系统属性就能配置 S3A 客户端,结果
  完全不生效——**Hadoop 的 `Configuration` 类只从 XML 配置文件
  (`core-site.xml`)读取 `fs.s3a.*` 这类属性,不会读 JVM 的 `-D` 系统
  属性**。
- **处置**:改用 ConfigMap 挂载真正的 `core-site.xml` 到
  `/opt/hive/conf/core-site.xml`,`fs.s3a.access.key`/
  `fs.s3a.secret.key` 这类敏感值用 `${env.VAR}` 语法引用容器环境变量
  (Hadoop 配置支持这个插值语法),不直接写死在 ConfigMap 里。
- **涉及文件**:`apps/hive-metastore/manifests/deployment.yaml`、
  `apps/hive-metastore/manifests/core-site-configmap.yaml`。

### Trino 建 Iceberg schema 时指定 location 会报错,不指定就正常

- **症状**:`CREATE SCHEMA iceberg.xxx WITH (location =
  's3://lakehouse/xxx/')` 报和上一条一样的 `Failed to create external
  path ... : null`,但把 `WITH (location = ...)` 去掉、直接
  `CREATE SCHEMA iceberg.xxx`(用 Hive Metastore 默认的 warehouse
  路径)就能成功建表、写入、读出,完全正常。
- **定位**:S3A 连接本身没问题(能证明,因为默认路径的读写全部成功,
  数据真的落到了 MinIO 的 `opt/hive/data/warehouse/xxx.db/...` 下),
  问题窄化到"显式指定 external location"这一条特定路径上,具体是 HMS
  侧对显式路径多做的存在性校验/权限检查(错误信息里提到的
  `StorageBasedAuthorizationProvider`)出了什么问题,还没深挖。
- **处置(现状)**:**不指定 location,用默认路径**是当前的可行方案,
  没有继续深究显式 location 这条路径——已经达成"验证
  Trino/Iceberg/Hive Metastore/MinIO 能端到端工作"这个目标,显式指定
  bucket 内子路径这个需求不紧急,不值得为此无限排查下去。真的需要
  显式控制表的物理路径时再回来查。
  - **附带发现**:默认路径会带上 `opt/hive/data/warehouse/` 这一截
    前缀(来自 Hive 的 `hive.metastore.warehouse.dir` 默认值,被当成
    s3a 路径里的 key 前缀),不是干净的 `s3://lakehouse/xxx.db/...`。
    能用,但不好看,以后可以通过显式设置
    `hive.metastore.warehouse.dir=s3a://lakehouse/warehouse/` 来清理,
    不紧急。

---

## 认证 SSO 层

### Keycloak start-dev 自带的 H2 是内存/临时数据库,pod 重启就把 realm 全部丢光

- **症状**:colima 停机重启一次(比如隔天再打开电脑),ArgoCD 显示所有
  Application 都是 `Synced`/`Healthy`,包括 `keycloak`,但打开
  ArgoCD/Grafana 的登录页,Keycloak 登录选项要么消失要么点了报错。用
  `kcadm.sh get realms/platform` 查发现 realm 直接不存在了。
- **定位**:`platform/apps/keycloak.yaml` 一开始为了图省事,只写了
  `args: ["start-dev"]`,没配 `database`,keycloakx chart 默认落到自带
  的 H2 数据库,数据存在容器内存/临时文件系统里,pod 一重启(不管是
  OOM、节点重启,还是简单的 `colima stop` 再 `colima start`)就清空。
  ArgoCD 只关心 Deployment/StatefulSet 是不是 Ready,不知道"里面的
  业务数据被清空了"这种事,所以看着一直是 Healthy,具有很强的欺骗性。
- **处置**:改成接共享 Postgres(和 hive-metastore/mlflow 等组件一样,
  见 `apps/postgres/`),`database.vendor: postgres` +
  `existingSecret` 指向 `keycloak-db`(`apps/keycloak-db-init/` 负责
  建库建用户,模式和 `apps/mlflow/manifests/create-db-job.yaml` 一样)。
  落盘之后 pod 重启不再丢数据。
  - **教训**:任何"看起来只是跑个 demo"的组件,只要它自己攒了状态
    (realm、用户、配置),就不能假设临时/内存存储没关系——
    `ArgoCD Healthy` 只保证进程活着,不保证数据还在,这两者是完全不同
    层面的健康。

### OpenMetadata 改了 OIDC 环境变量,`/api/v1/system/config/auth` 还是显示旧的 basic 认证

- **症状**:`apps/definitions/openmetadata.yaml` 里
  `openmetadata.config.authentication.*` 改成了 Keycloak OIDC 配置,
  ArgoCD 也确认 Synced,进容器 `crictl exec ... env` 也能看到
  `AUTHENTICATION_PROVIDER=custom-oidc` 等环境变量都是对的,但打
  `/api/v1/system/config/auth` 这个 API,返回的还是 `"provider":"basic"`、`"authority":"https://accounts.google.com"` 这些默认值,
  而且不是偶发,反复请求结果一样。
- **定位**:直接查 `SELECT json FROM openmetadata_settings WHERE
  configType='authenticationConfiguration'` 能看到数据库里存的是哪份
  配置,不用猜。
- **原因**:OpenMetadata 只在 `openmetadata_db` 这个数据库**第一次
  初始化**时,把 env var 算出来的认证配置写进 `openmetadata_settings`
  表(`configType = 'authenticationConfiguration'` 那一行,存的是一
  整块 JSON)。**之后每次启动都是数据库里的这份 JSON 说了算,不会再
  重新读 env var**。
- **处置**:这台机器上是纯测试数据,直接 `DROP DATABASE
  openmetadata_db` + 重建(**这是破坏性操作,执行前问过用户**),让它
  从空库重新初始化。生产环境如果真的要改认证方式,不能这么干,需要
  研究 OpenMetadata 有没有提供"强制用 env var 重新覆盖 settings 表"的
  CLI/API,这次没往这个方向查(local-lite 阶段直接重建更快),留给以后
  真的要在有数据的库上切换认证方式时再查。
  - **连带的坑**:重建空库之后,`openmetadata-ops.sh migrate`(建
    OpenMetadata 自己的表)会跑成功,但 App 主进程启动时可能报
    `relation "act_ge_property" does not exist`(Flowable 治理工作流
    引擎自己的表,不属于 OpenMetadata 自己的 schema migration 范围)
    导致 CrashLoopBackOff。实测这次重试(删 Pod 重建)后就自己好了,
    像是 Flowable 自己的 schema 自动建表在第一次启动时偶发没跟上,
    不是每次都复现,遇到先重启一次 Pod 看看,不用一开始就当成需要深入
    排查的硬故障。

### OpenMetadata SSO 登录报 "Account already exists. Please contact administrator."——不是配置问题,是数据库里一条半损坏的用户记录

- **症状**:`apps/definitions/openmetadata.yaml` 里 Keycloak OIDC 配置
  从最初部署起就是对的(`authentication.provider: custom-oidc`,
  `jwtPrincipalClaims: [email, preferred_username, sub]`),但用
  Keycloak 的 `admin` 账号登录 OpenMetadata,页面报 "Account already
  exists. Please contact administrator.",换一个全新的 Keycloak 用户名
  登录完全正常。
- **定位**:直接查 Postgres 的 `user_entity` 表(`SELECT json FROM
  user_entity WHERE name='admin'`)发现这条记录的
  `authenticationMechanism` 是空的,`updatedAt` 精确对应到某次早先的、
  不走正常浏览器 OIDC 流程的交互(比如直接拿 token 调 API 做验证测试)。
  `entity_relationship` 表里确认这条记录没有被任何其它实体引用
  (`fromid`/`toid` 查询 0 条),证明它只是一条孤立的半成品,不是承载了
  真实数据的账号。
  - **排查过程中一个容易踩的坑**:重试登录时看着换了浏览器操作,但只要
    Keycloak 自己的 SSO session cookie(或者 OpenMetadata 自己的服务端
    session)还活着,点"重新登录"实际上是在复用旧 session,不会真的
    重新走一遍 OIDC 授权流程,会看到看似矛盾的结果(比如报另一个
    "Session not active"/"invalid_grant" 错误)。要排查这类问题,必须
    先显式访问 Keycloak 的 `/protocol/openid-connect/logout` **和**
    OpenMetadata 自己的 `/logout`,两边都清干净,再重新走一遍完整登录,
    不能只清浏览器 cookie(有的 session cookie 是 `HttpOnly`,JS
    `document.cookie` 清不掉)。
- **原因**:这类操作会触发 OpenMetadata 的用户自动创建/更新逻辑,但
  走的不是完整的 OIDC code flow,留下一条"创建了但没有正常关联认证
  方式"的半成品用户记录。之后任何人用同一个用户名(这里是 `admin`)
  走正常 OIDC 登录,OpenMetadata 发现这个用户名已经存在但认证方式对
  不上,判定为"身份冲突",拒绝登录——这是它自己的防呆机制,不是 bug,
  但触发条件是这条脏数据,不是配置本身有问题。
- **处置**:直接从 `user_entity` 表删掉这条记录(先确认
  `entity_relationship` 里没有引用,再删,别对着一个可能有真实数据挂
  在上面的账号做这个操作),然后完整走一遍登出+重新登录,OpenMetadata
  会用干净的状态重新创建这个用户,`isAdmin: true` 正确、`email` 和
  Keycloak 里的 claim 完全对上。全程没有改任何
  `apps/definitions/openmetadata.yaml` 里的配置——这是数据层面的一次性
  清理,不是需要同步进"一键部署"代码的修复,真正全新的部署从第一次
  登录起就不会有这条脏数据,不会重现这个问题。
  - **教训**:验证 SSO/OIDC 集成时,只用真实浏览器走完整的 code flow
    测试(curl+cookie-jar 或者真实浏览器测试),避免用"直接拿 token
    调 API"这类走捷径的验证方式碰触用户身份这一层——这类捷径可能会在
    应用自己的用户表里留下不完整的记录,虽然不影响当时测试本身"看起来
    通过",但会在后续正常登录时以完全不相关的报错形式冒出来,排查成本
    比老老实实走一遍完整浏览器流程高得多。

### cloud-full 上登录跳转回来是 404,连续挖出四层叠在一起的真实故障

- **背景**:2026-08-16 深夜,journal。用户打开
  `portal.local-lite.test:32460` 报 404,连续深挖出 4 层叠在一起的真实
  问题,每一层都实测验证过修复,最后用 curl+cookie jar 完整模拟了一次
  真实登录(提交账号密码、拿 Keycloak 授权码、oauth2-proxy 换 token、
  最终落地门户首页),不是只测了某一步。
- **症状与定位(按发现顺序)**:
  1. **redirect_url 没带端口**——`platform-portal`/
     `permission-request-app`/`table-registration-app` 三个
     oauth2-proxy 的 `redirect_url` 都写死不带端口,local-lite 靠
     colima 自动转发 80/443 不需要,cloud-full 的 ingress-nginx 是
     NodePort(32460/32535),不带端口登录跳转回来直接 404。
  2. **Keycloak 自己的 hostname 推断丢端口**——修完①还是 404,发现
     Keycloak 自己的 discovery 文档(`authorization_endpoint` 等)也
     丢了端口:`KC_HOSTNAME_STRICT=false` 靠 `X-Forwarded-*` 头推断,
     但 ingress-nginx 自己内部监听的是 80/443,反映不出外面真正访问的
     NodePort。这不是某一个组件的问题,是全部接了 SSO 的组件
     (argocd/grafana/jupyterhub/trino/superset/openmetadata/mlflow/
     argo-workflows)共同的根因。
  3. **前后端不同地址导致的连锁问题**——固定 `KC_HOSTNAME`(带端口)+
     `KC_HOSTNAME_STRICT=true` 之后:`issuer` 字段(安全校验用,不能像
     backchannel 端点那样动态化)带了端口,3 个 oauth2-proxy 的自动
     discovery 拿这个 issuer 和自己配的 `oidc_issuer_url` 精确比对,
     不匹配直接拒绝启动("oidc: issuer did not match"),但它们在集群
     内部又连不通这个带端口的外部地址(实测 `HTTP:000`,NodePort 只在
     节点网卡监听)。
  4. **nginx 响应头缓冲区太小**——前 3 层修完,登录流程走到最后一步变成
     502(独立成一条记录,见上面"网络与 Ingress 层"里的
     "upstream sent too big header")。
- **处置**:
  1. 3 个 oauth2-proxy 的 `redirect_url` 补上端口。
  2. Keycloak 改成 `KC_HOSTNAME`(带端口)+ `KC_HOSTNAME_STRICT=true`。
  3. 加 `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` 让 Keycloak 的
     backchannel 端点(token/jwks/userinfo)跟着"谁在问"动态生成,同时
     把 3 个 oauth2-proxy 改成 `skip_oidc_discovery=true` 手动分开配置
     `login_url`(带端口,外部)和 `redeem_url`/`oidc_jwks_url`/
     `profile_url`(不带端口,内部)——和 Grafana 一直以来的做法是同一
     个模式。
  4. 见"网络与 Ingress 层"里独立的那条记录。
  - **验证方式**:用 curl 手动模拟了一次完整的浏览器登录(cookie jar
    走完"访问 portal → 跳 Keycloak → 提交表单登录 → 拿授权码 → 回调
    oauth2-proxy 换 token → 落地门户首页"全程),最终确认拿到
    `<title>平台门户</title>` 和"当前登录"字样,不是错误页——这是这次
    真正的验收标准,不是"配置看着对了就行"。
  - **仍然没有逐个验证到底的**:`argo-workflows`/`trino`/`superset` 也
    是自动 discovery,理论上有同一类潜在问题,但当时实测目前没有崩溃
    重启(懒验证,只在真登录时触发),如实记录不是回避。
  - **副作用记录**:调试过程中用 `kcadm set-password` 重设过 cloud-full 上
    Keycloak `platform` realm 的 `admin` 密码(原密码丢失/不确定)。**密码
    值本身不再写在文档里**,原因和取法见下面「cloud-full 的 Keycloak admin
    密码」那一节。


### cloud-full 的 Keycloak admin 密码

**2026-08-29 发现的问题**:这个密码的明文值曾经写在 4 个文档文件里,而
**这个 git 仓库是公开的**(`github.com/hardstuding/bigdata_ml_paltform`)。
已从当前版本里去掉,但 **git 历史里还在,去不掉** —— 唯一真正有效的处置是
**换掉这个密码**,不是改文档。

**zhenghe 2026-08-29 的决定:不换。** 原话"密码不用换,我们只是开发测试"。
所以这条**不是待办**,不要在下一轮又提一次。前提是这台机器一直只作开发
测试用;**哪天它承载真实数据或真实用户,这条要重新拿出来。**

真要换的时候:

```bash
KC_ADMIN_PW=$(kubectl -n keycloak get secret keycloak-admin -o jsonpath='{.data.password}' | base64 -d)
kubectl -n keycloak exec keycloak-keycloakx-0 -- /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080/auth --realm master --user admin --password "$KC_ADMIN_PW"
# 换成一个新的强密码,并且**只记在本地**(比如 secrets/,那个目录不进 git)
kubectl -n keycloak exec keycloak-keycloakx-0 -- /opt/keycloak/bin/kcadm.sh set-password \
  -r platform --username admin --new-password '<新密码>'
```

**取当前值**:realm 里那个 `admin` 用户的密码是人工设的,集群里没有存明文
可读的副本 —— 忘了只能按上面的步骤重设。master realm 的管理员密码则在
`keycloak-admin` 这个 Secret 里,上面第一条命令就是取它。

**教训**:"临时调试改的密码"最容易被顺手记进文档,而文档是会被提交的。
凡是真实环境的口令,记在 `secrets/`(不进 git)或者干脆不记、需要时重设。

### Superset OAuth 登录卡在最后一步,报 "Invalid URL 'openid-connect/userinfo'"

- **背景**:2026-08-16 深夜,journal。
- **症状**:用 curl+cookie jar 走完整流程,GET `/login/keycloak` →
  Keycloak 登录页 → 提交账号密码 → 拿到授权码回调
  `/oauth-authorized/keycloak` → **卡在这一步,被重定向回 `/login/`**,
  不是预期的落地页。`kubectl logs` 能看到真实报错:
  ```
  Error returning OAuth user info: Invalid URL 'openid-connect/userinfo':
  No scheme supplied. Perhaps you meant https://openid-connect/userinfo?
  ```
- **定位**:读 flask_appbuilder 的源码确认(不是猜的):
  `get_oauth_user_info` 对 provider name 严格等于 `"keycloak"` 有一段
  写死的分支,不走 `server_metadata_url` discovery 出来的
  `userinfo_endpoint`,而是自己拼
  `oauth_remotes["keycloak"].get("openid-connect/userinfo")`——这是
  相对路径,需要 `remote_app.api_base_url` 做前缀,但当时的
  `OAUTH_PROVIDERS` 配置只给了 `server_metadata_url`,漏了
  `api_base_url`。
- **处置**:补上 `api_base_url`(内部地址、不带端口,和
  `server_metadata_url` 同一类服务端到服务端调用)。确认修好:最终
  页面 `data-bootstrap` 里拿到
  `{"user": {"username": "admin", ..., "isAnonymous": false,
  "loginCount": 1, "roles": {"Admin": [...]}}}`,落地
  `/superset/welcome/`,是一个真实、完整的已认证会话,不是重定向链断
  在中间。
- **涉及文件**:`apps/definitions/superset.yaml`。

### Keycloak realm 从建立起就没有任何 client 配过 groups claim mapper,按组鉴权从来没真正生效过

- **背景**:2026-08-19,`docs/project/current-work.md`。
- **症状**:直接解码拿到的 `id_token` 确认过,`admin` 明明在
  `platform-team` 组里,token 里完全没有 `groups` 字段。这意味着
  Grafana(ADR-028)/JupyterHub(ADR-025)之前"按组收紧已验证"的说法
  不准确,大概率从来没有真的按组生效过,只是没人拿会被拒绝的账号测过。
- **定位**:解码任意一个已登录用户的 `id_token`(base64 解 JWT
  payload),看有没有 `groups` claim。
- **处置**:建 realm 级别的 `groups` client scope + mapper,挂到
  grafana/jupyterhub/mlflow/spark-history-server 这四个用到
  `allowed_groups` 的 client 上。确认修好:重新登录后 `id_token` 里能
  看到 `groups` 字段,包含正确的组名。

### K8s 1.24+ 不会自动创建 service-account-token 类型 Secret,Argo Workflows 登录成功但调 API 一直 403

- **背景**:2026-08-19,`docs/project/current-work.md` / `docs/project/capability-matrix.md`。
  Argo Workflows 从 8-16 起就 CrashLoopBackOff,两天多没人发现,先修好
  的是 issuer 校验失败(discovery 文档拿到的 issuer 字段带端口,发起
  请求用的地址不带,读官方源码确认要用 `sso.issuerAlias` 对应
  `oidc.InsecureIssuerURLContext` 才能两边都满足)。这条记录的是修好
  issuer 之后,登录能进去了,但调 API 还是 403 的那一层。
- **症状**:登录成功,但调 `/api/v1/workflows/...` 之类的 API 一直
  403。`server.sso.rbac.enabled: true` 本身不建任何授权资源。
- **定位**:读官方源码(`server/auth/gatekeeper.go`)确认,启用
  `server.sso.rbac.enabled` 之后还要手动建这几样东西:ServiceAccount
  (挂 `workflows.argoproj.io/rbac-rule` 注解匹配 `platform-team` 组)+
  长期 `kubernetes.io/service-account-token` 类型 Secret(**K8s 1.24+
  不会自动创建这种 Secret**,这是这条记录的核心坑)+ Role/RoleBinding。
- **处置**:四个资源都加进 `templates/apps-definitions/
  argo-workflows.yaml` 的 `extraObjects`。另外发现 `argo-workflows`
  这个 client 当时没挂上 `groups` client scope(和上一条是同一批漏配),
  一并补进 `scripts/03-configure-keycloak.sh`。确认修好:真实
  curl+cookie-jar 验证过登录 → `GET /api/v1/workflows/argo-workflows`
  200 → 建一个真实 Workflow → 能查到 → 删除清理,全程通过。
  - **举一反三**:任何自己给 Argo Workflows(或其它同样靠
    `kubernetes.io/service-account-token` 类型 Secret 做长期认证的
    组件)新建 ServiceAccount 的场景,都要记得这个 Secret 在 K8s 1.24+
    集群上不会自动生成,必须显式声明,不能假设"建了 ServiceAccount 就
    自动有 token"。

### ArgoCD 接 Keycloak OIDC,登录跳转到集群内部域名,浏览器打不开(已废弃,仅存档)

> **2026-08-09 更新**:下面这套 split-horizon DNS 土办法已经被真正的
> Ingress 方案替换掉了(`apps/keycloak-local-access/` 整个目录已删除),
> 现在浏览器和集群内部都走 `http://keycloak.local-lite.test`,经
> ingress-nginx 统一入口,差别只是浏览器经 `127.0.0.1`(colima 自动
> 转发 80/443),pod 内部经 `hostAliases` 指向
> ingress-nginx-controller 的 ClusterIP。这一段保留是因为如果以后哪个
> 组件暂时没法用 Ingress、又要面对同样的"浏览器和集群内部地址不一致"
> 问题,这个思路还有参考价值。

- **症状**:点 ArgoCD 的 "LOG IN VIA KEYCLOAK",跳转到类似
  `http://keycloak-keycloakx-http.keycloak.svc.cluster.local/...` 的
  地址,浏览器报 `DNS_PROBE_FINISHED_NXDOMAIN`(这个域名只有集群内部
  能解析)。
- **定位**:ArgoCD 的 OIDC 配置只有一个 `issuer` 字段,浏览器跳转登录
  页、和 ArgoCD server pod 自己做 token 交换,都是用这一个地
  址——不像 Grafana 的 `auth.generic_oauth` 那样能把 `auth_url`
  (浏览器用)和 `token_url`/`api_url`(后端用)分开配。本地用
  port-forward 访问集群时,浏览器能到达的地址(`localhost:端口`)和
  集群内部 pod 能到达的地址(service DNS)根本不是一回事,一个 issuer
  两头顾不上。
- **处置**:让两边用同一个域名、分别解析到各自能到达的地方
  (split-horizon DNS 的土办法)——
  1. `apps/keycloak-local-access/manifests/service.yaml` 建一个额外的
     Service(不是 Helm chart 管理的那个,避免冲突),暴露一个固定
     端口。
  2. ArgoCD 这边用 `global.hostAliases`(注意不是顶层的
     `hostAliases`,那个 key 在 chart 里不生效,必须是
     `global.hostAliases`)把这个域名在 pod 里解析到上面那个 Service
     的 ClusterIP。
  3. 自己的 Mac 上把同一个域名加进 `/etc/hosts` 指向 `127.0.0.1`:
     ```
     sudo sh -c 'echo "127.0.0.1 keycloak.local-lite.test" >> /etc/hosts'
     ```
  4. 浏览器这边的 `kubectl port-forward` 也转发到那个新 Service
     (端口和上面的域名对应上)。
  5. `issuer` 改成
     `http://keycloak.local-lite.test:8180/auth/realms/platform`。
  - **这是 local-lite 专属的临时方案**,不是架构的一部分——上了真实
    域名/ingress 之后,`apps/keycloak-local-access/` 整个目录、
    `global.hostAliases`、这条 issuer 配置全部可以删掉,换成真实的
    对外域名(浏览器和集群内部走同一个真实域名,天然没有这个问题)。
  - **域名问题解决之后还会踩一个协议不一致的坑**:Keycloak 报
    `We are sorry... Invalid parameter: redirect_uri`(注意这是
    Keycloak 自己吐的错,和上面 ArgoCD 吐的 "Invalid redirect URL"
    是两个不同的检查点)。原因是 client 在 Keycloak 里注册的
    `redirectUris` 写的是 `http://`,但 `configs.cm.url` 改成
    `https://` 之后 ArgoCD 实际发起的回调请求是 `https://`,两边对不
    上。用 `kcadm.sh update clients/<id>` 把 `redirectUris` 也改成
    `https://` 就好,`scripts/03-configure-keycloak.sh` 已经改成一
    开始就注册 https,不会再重现这个坑。

---

## K8s 资源生命周期 / NetworkPolicy

### Helm chart 的 envFromSecrets(复数)不一定覆盖所有容器,initContainer 可能读不到

- **症状**:用 `envFromSecrets: [my-secret]`(追加一个额外 Secret)想
  覆盖数据库连接信息,主容器能读到,但某个 initContainer(比如
  Superset chart 的 `wait-for-postgres`)一直卡在 `Init:0/1`、不断重试
  连接一个不存在的默认 host,像是完全没读到新配置。
- **定位**:改之前先跑
  `helm template <chart> --set ... | grep -B20 "name: <initContainer名>"`
  看它的 `envFrom` 实际引用的是哪个 Secret 名字,不要靠猜。
- **原因**:chart 的模板作者经常只在"主容器"或者"部分资源"上接了
  `envFromSecrets`(复数,追加语义),initContainer 的 `envFrom` 可能是
  硬编码只引用 `envFromSecret`(单数,chart 自己生成的那一个默认
  Secret)的名字,不会去遍历 `envFromSecrets` 列表。这不是 bug,是
  chart 实现细节,不同 chart 的处理方式可能不一样,不能默认"加一个
  secret 全局都能覆盖"。
- **处置**:直接**覆盖 `envFromSecret`(单数)这个值本身**,让它指向
  自己建的 Secret,而不是用复数形式"追加"——相当于完全替换 chart 默认
  生成的那个 Secret,而不是叠加一个新的。前提是自己的 Secret 要把默认
  Secret 里所有会被引用到的 key 都补全(哪怕用不上的功能对应的 key 也
  要给个占位值,比如关掉 Redis 后 `REDIS_*` 这几个 key 还是要存在,
  只是不会被用到)。

### 同一个命名空间里的 Job 连不上同命名空间的 pod,NetworkPolicy 报 "connection refused"

- **背景**:ADR-039,推倒重建集群时暴露。MinIO chart 的 `buckets:`
  声明式配置靠一个 Helm post-install hook Job(`minio-post-job`)执行
  `mc mb` 建 bucket,这个 Job 建在 `minio` 命名空间里,要连同一个命名
  空间里的 `minio` pod。
- **症状**:`mc: <ERROR> Unable to initialize new alias ...: connect:
  connection refused`,不是超时(NetworkPolicy 挡掉的连接通常表现为
  `connection refused`,不是 `timeout`——这个特征在这条和上面
  CoreDNS/ClusterIP 那条 DNS 问题之间是一个有用的区分信号)。
- **定位**:`kubectl get networkpolicy -n minio -o yaml` 看
  `allow-consumers-to-minio` 的允许来源 `namespaceSelector` 列表。仓库里
  对应的源文件是 `platform/network-policies/manifests/minio.yaml`。
- **原因**:允许来源列表只列了外部消费命名空间
  (`data`/`spark-operator`/`mlflow`/`trino`/`airflow`/`seatunnel`),
  漏了 `minio` 自己这个命名空间——默认 `default-deny-ingress` 把同
  命名空间的流量也一起挡了。之前没暴露是因为这个 Job 是 Helm 的
  `post-install`(不是 `post-upgrade`)hook,只在最初第一次装的时候
  跑,而那时候这条 NetworkPolicy 还没加上去。
- **处置**:把 `minio` 自己也加进允许来源的 `namespaceSelector` 列表。
  - **教训**:写 NetworkPolicy 的允许来源列表时,不要只想"谁会从
    **别的**命名空间连进来",同一个命名空间里如果有 Job/CronJob 之类
    的东西要连该命名空间自己的其他 pod(常见于 Helm chart 自带的
    post-install/post-upgrade hook),也要把自己的命名空间加进允许
    列表——这类同命名空间自连的需求容易被忽略,因为直觉上会觉得"同一个
    命名空间应该默认互通",但 `default-deny-ingress` 一旦生效,这个
    直觉是错的。

### 新建的 Job/CronJob 的 pod 刚起来第一次连接直接 Connection refused,但同样标签的 pod 手动测试是通的

- **症状**:`permission-request-app-escalation` 这个 CronJob 手动触发
  一次 Job,容器里的 `curl` 直接报 `Connection refused`(exit 7)。但用
  一个手动 `kubectl apply` 建的、带同样标签的 pod 测试同一个地址,连接
  完全正常。NetworkPolicy 规则本身核对过是对的(`kubectl get
  networkpolicy -o yaml` 确认 podSelector/标签都匹配)。
- **定位**:确认规则本身没错之后,怀疑时序竞态——用一个全新 pod 反复
  手动触发同一个连接测试,观察是不是"第一次必失败,之后都成功"这个
  模式。
- **原因**:NetworkPolicy 是靠 CNI 在这台机器上写底层规则(iptables
  之类)实现的,一个全新 pod 刚被调度、拿到 IP 之后,CNI 把对应的规则
  写好需要几秒钟——Job 的容器命令是"起来立刻执行",没有像手动测试那样
  天然多出几秒钟的间隔(先等 pod Running,再单独 exec 进去跑命令),
  所以精确踩中了这个规则生效前的窗口期,第一次连接必然失败。这不是
  NetworkPolicy 配错,是一个真实的、跟这台机器 CNI 实现相关的时序
  竞态。
- **处置**:Job 里但凡要连接受 NetworkPolicy 保护的目标,`curl` 要显式
  加 `--retry-connrefused`(普通 `--retry` 默认不重试"连接被拒"这种,
  只重试超时/5xx 这类)。这是比"在命令前面加个 `sleep N`"更靠谱的
  写法——重试次数和间隔是可预期的,不用去猜"到底要等几秒才够"。

### 给 ConfigMap 新增一个 key 之后,subPath 挂载这个新 key 的文件在 pod 里变成了一个空目录

- **背景**(2026-08-15,新增 `apps/airflow/dags/dbt_demo.py` 这个 DAG
  时撞到):ConfigMap 里确实有这个新 key(`kubectl get configmap ... -o
  jsonpath='{.data}'` 能看到),`extraVolumeMounts` 里 `subPath:
  dbt_demo.py` 这一条配置本身也没写错。
- **症状**:pod 起来之后 `/opt/airflow/dags/dbt_demo.py` 是一个空目录
  (`drwxrwsrwx`),不是文件,同一批挂载的另外两个已经存在很久的文件
  (`feast_materialize.py`/`seatunnel_device_events.py`)都正常。
  `airflow dags list-import-errors` 不会报这个错(它没有文件可解析,
  不是"解析失败",是"根本没看到这个文件")。
- **定位**:`kubectl exec ... -- ls -la <挂载路径>` 检查是文件还是
  目录,是最快的确认方式,不用去猜是不是代码/配置写错了。
- **原因**:`subPath` 挂载不走 K8s ConfigMap 卷常见的"`..data` 软链
  定期刷新"机制(这个项目已经在别处吃过"改了 ConfigMap 要等 ~1 分钟才
  生效"的亏,但那条讲的是整目录挂载),`subPath` 是 pod **创建那一刻**
  去 ConfigMap 里找对应 key、直接投影成一个文件——如果 pod 创建的那个
  时间点,底层 ConfigMap 的新版本还没有完全传播到这台节点的 kubelet
  (改 ConfigMap 和重启依赖它的 Deployment 这两个操作之间没有强制的
  先后等待,很容易连续执行时刚好撞上这个窗口),kubelet 找不到这个
  key,不会让 pod 起不来报错,而是**默默地建一个空目录**占位。这个
  空目录建立之后,`subPath` 挂载本身也不会像整目录挂载那样后续自动
  修复/更新——即使 ConfigMap 之后确实同步好了,这个 pod 里那个位置
  永远是空目录,直到这个 pod 被重新创建。
- **处置**:改 ConfigMap 之后如果重启依赖它的 Deployment 碰到这种
  "文件变目录"的情况,不要怀疑 ConfigMap 内容本身(先用
  `kubectl get configmap ... -o jsonpath='{.data}'` 确认 key 真的
  在),大概率是重启时机踩早了——再等几十秒到一分钟,重新
  `kubectl rollout restart` 一次就好。
  - **同一个坑的另一种表现,值得对照着看**:见下面"各组件专属故障"
    里"Trino 新建 service account 之后连接仍然报 Invalid credentials"
    那条——同样是 subPath 挂载不是活的,这次表现不是"变空目录",是
    "内容是启动那一刻的旧快照"。

---

## 各组件专属故障

### Superset 报 ModuleNotFoundError: No module named 'psycopg2'(或 'authlib')

- **症状**:Superset 主容器 CrashLoopBackOff,日志里是
  `ModuleNotFoundError: No module named 'psycopg2'`,发生在初始化
  数据库连接的时候。
- **定位**:进容器找官方有没有现成的安装脚本
  (`find /app -iname "*install*"`),不要瞎猜 pip 在哪。
- **原因**:官方 `apache/superset` 镜像不自带 Postgres 驱动,chart
  默认的 `bootstrapScript` 也不会装。用 Bitnami 那套(chart 默认依赖)
  时不会遇到,因为 Bitnami 的镜像/流程不一样;一旦按 ADR-008 换成外部
  Postgres,这个驱动缺失就暴露出来了——接外部 Postgres 时的已知通用
  问题,不是这个项目特有的配置错误。
- **处置**:不要猜 pip 路径(试过裸 `pip install`、试过
  `/app/.venv/bin/pip`,都不对——镜像实际用 `uv` 管理虚拟环境,不是
  标准 venv 布局)。**镜像自带官方脚本 `/app/docker/pip-install.sh`**,
  专门用来在这个环境里装额外的 Python 包(内部调用 `uv pip install`),
  用这个才是对的:`/app/docker/pip-install.sh psycopg2-binary`。
  - **2026-08-09 复现,换了个包**:接 Keycloak OAuth2 时(`AUTH_TYPE =
    AUTH_OAUTH`),同样的报错又出现一次,这次缺的是 `authlib`——
    Flask-AppBuilder 的 OAuth 支持依赖它,基础镜像同样不带,只有真正
    启用 `AUTH_TYPE = AUTH_OAUTH` 这条 import 路径才会暴露,启动阶段
    测不出来。处理方式一样,`pip-install.sh` 后面多加一个包名就行:
    `/app/docker/pip-install.sh psycopg2-binary authlib`。**教训**:
    这个镜像"按需装包"的模式下,每接一个新功能(数据库驱动、认证
    方式、以后可能的缓存后端)都可能暴露一个新的缺包,不是一次装完就
    一劳永逸,踩到就加,不用因为"上次刚修过一个"就觉得这次不该出现
    同类问题。
  - **2026-08-10 再复现,这次是"连接哪个数据源"层面**:同一个规律不
    只出现在 Superset 自己连元数据库/认证这些内建功能上——**给
    Superset 添加一个新的业务数据源(SQL Lab 里连 Trino/ClickHouse/
    MySQL 等)也是同一套"缺哪个 SQLAlchemy dialect 包就报哪个
    ModuleNotFoundError / Could not load database driver"**,同样要在
    `bootstrapScript` 里补对应的包(Trino 是 `trino`,ClickHouse 通常
    是 `clickhouse-connect`,MySQL 是 `mysqlclient` 或 `pymysql`,以此
    类推)。这是接入新数据源清单里必须提前想到的一步,不是等报错了
    才知道要加。

### 组件重新拉起来报 "password authentication failed",Postgres 密码"变了"

- **症状**:一个之前验证过、收进 `pending-definitions` 又重新启用的
  组件(OpenMetadata 和 MLflow 各出现一次),报 `FATAL: password
  authentication failed for user "xxx"`,但对应的 Secret(比如
  `mlflow-db-secret`)看起来内容正常,没有被意外改过。
- **定位**:确认报错信息里的用户名对得上、Secret 内容看着正常之后,
  怀疑密码漂移。
- **原因**:各组件的 `create-db-job.yaml` 都是"角色不存在才创建"
  (`SELECT ... FROM pg_roles ... || CREATE USER ...`),不会更新已存在
  角色的密码。这台共享 Postgres 从很早的会话开始就一直在跑、数据一直
  没清过,Postgres 里的用户角色早就存在了,是用**当时**Secret 里的
  密码创建的;如果那个组件后来因为任何原因(重新生成过 Secret、手动
  改过、或者这次同一类 bug)导致 Secret 里的密码值和 Postgres 里实际
  存的密码不一致,新 Pod 拿**当前** Secret 的密码去连接,自然连不
  上——这不是 Postgres 或者组件本身的 bug,是"创建型"幂等脚本天然
  覆盖不到"密码漂移"这种情况。
- **处置**:直接把 Postgres 里那个角色的密码改成和当前 Secret 一致:
  ```sql
  ALTER USER <角色名> WITH PASSWORD '<Secret 里当前的密码>';
  ```
  不用碰 Secret,也不用重建数据库(除非像 OpenMetadata 那次一样,问题
  根本不是密码,是数据库里存的应用设置本身就是旧的)。
  - **教训**:这次在 OpenMetadata 和 MLflow 上各踩了一次,同一个原因、
    同一个修法——凡是"组件在 pending-definitions 和 apps/definitions
    之间来回搬动、但共享 Postgres 从不重置"这种场景,都要预期可能撞上
    这个问题,不是特例。

### `KubernetesPodOperator` 拉起跨命名空间/自定义镜像的 Spark 任务,一路要闯好几关(RBAC、日志流、容器 UID、模板变量)

- **背景**(2026-08-14,`feast_materialize` DAG 排查过程完整记录,见
  [ADR-042](../decisions/042-feast-feature-store.md)):这是这个平台
  第一次用 `KubernetesPodOperator` 在 Airflow 自己的命名空间之外
  (`namespace="feast"`)拉起一个跑 Spark 的自定义镜像任务,一路暴露了
  好几个**通用的、以后任何同类 DAG 都可能重新撞上**的坑,不是 Feast
  独有:
  1. **跨命名空间要单独建 RBAC**:症状是 `403 Forbidden: cannot list
     pods`。定位:Airflow chart 默认的 `airflow-pod-launcher-role` 是
     `Role`(命名空间级),不是 `ClusterRole`,没开
     `multiNamespaceMode` 的话,目标 pod 建在别的命名空间会报这个错。
     处置:按最小权限原则,在目标命名空间单独建一份同权限的
     `Role`+`RoleBinding`,不要图省事开 `multiNamespaceMode`(会变成
     集群级 `ClusterRole`)。**这个坑后来又复现了两次**,见下面
     `dbt_demo`/`platform_sdk_demo` 那条。
  2. **`get_logs=True` 在这台机器上会把任务拖垮**:症状是任务反复
     `ApiException(500)` 重试两分半后放弃、直接把还在正常跑的 pod
     删掉判定失败。定位:内部读日志流走 kubelet `containerLogs`,被
     本机代理软件拦截(见"网络与 Ingress 层"的"Internal Privoxy
     Error"那条)。处置:关掉 `get_logs`,靠 Loki/Alloy 兜底日志,
     operator 只轮询 pod phase(走 K8s API,不受影响)。
  3. **容器用"任意 UID"镜像(如 `USER 1001`,`/etc/passwd` 没有对应
     条目)时,Spark/Hadoop 启动会崩**:症状是
     `JAVA_GATEWAY_EXITED`。定位:`UserGroupInformation` 走 JVM 的
     `UnixLoginModule` 查用户名查不到,直接抛 `KerberosAuthException:
     ... invalid null input: name`。`HADOOP_USER_NAME` 环境变量不够用
     (它在 `UnixLoginModule` 崩溃点之后才生效)。处置:要么用
     `security_context: run_as_user: 0` 跑 root(local-lite 阶段的
     务实选择),要么重新 build 镜像在 entrypoint 里给这个 UID 补一条
     `/etc/passwd`(更干净但要改镜像)。
  4. **`{{ ts }}`/`{{ ds }}` 这类基于调度时间的 Jinja 宏,在
     `schedule=None`、手动触发的 DAG Run 上是未定义的**:症状是
     `UndefinedError: 'ts' is undefined`。定位:没有 `data_interval`,
     不是 Airflow 3.x 废弃了这些宏(带 schedule 的 DAG 上还能正常用)。
     处置:需要"当前时间"的场景,改成在 shell 命令里直接取(比如
     `$(date -u +%Y-%m-%dT%H:%M:%S)`),不要依赖这类宏。
  - 同一次排查里还有两条不算通用、但值得记一笔的坑:K8s ConfigMap 卷
    整目录挂载会把 `..data` 软链背后带时间戳的隐藏目录名泄漏进 Python
    的 relative import 路径(和 Airflow 自己挂 DAG 目录踩过的坑同一个
    原因,解法一样——用 `subPath` 分别挂单个文件);Feast 的 S3
    registry 走 boto3,认标准 `AWS_*` 环境变量,不是给 Spark 用的
    `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`(Hadoop S3A 客户端专用),
    两套凭据变量名要分别配。
  - **2026-08-20 补充,第 1 条坑第三次复现**:`platform_sdk_demo` DAG
    第一次真正端到端跑时,又撞上同样的
    `403 Forbidden: pods is forbidden: User
    "system:serviceaccount:airflow:airflow-worker" cannot list
    resource "pods"...in the namespace "platform-sdk-demo"`。解法照抄
    `apps/dbt-demo/manifests/airflow-worker-rbac.yaml` 的模式,在
    `apps/platform-image/manifests/airflow-worker-rbac.yaml` 新增一份
    Role+RoleBinding。修完后 DagRun 真正 `state=success`。**这三次
    合起来的教训**:`dbt_demo`/`feast_materialize`/`platform_sdk_demo`
    三条 KubernetesPodOperator 起跨命名空间 pod 的 DAG,每一条第一次
    真正端到端跑的时候都撞上过这同一个坑——不是巧合,是这套 chart 的
    默认行为本来就不支持跨命名空间,每次接一条新的这种 DAG 都要记得
    主动加这份 RBAC,不要等 403 报出来才想起来查。

### Trino 新建 service account 之后连接仍然报 Invalid credentials,密码看着是对的

- **背景**:2026-08-20,`docs/project/current-work.md`,`platform_sdk_demo` DAG
  排查过程中撞到的第二个坑(第一个是上面的 RBAC 问题,第三个是下面的
  OPA 白名单问题)。
- **症状**:DAG 报 Trino "Invalid credentials",账号密码本身没错
  (bcrypt hash 手工验证过是对的)。
- **定位**:`trino-service-account` 这个 Secret 里的 `password.db` 是
  `subPath` 挂载进 Trino coordinator pod 的。
- **原因**:和"给 ConfigMap 新增一个 key,subPath 挂载变空目录"那条是
  **同一类根因**(subPath 挂载不是活的,是 pod 创建那一刻的快照),
  只是这次表现不是"变空目录",是"内容是旧密码"——新建账号后 Trino
  pod 不会自动看到 Secret 更新。
- **处置**:重启 Trino coordinator pod,让它重新挂载读到最新的
  `password.db`。确认修好:DAG 重新触发后能正常连上 Trino。
  - **举一反三**:任何账号/密码类的 Secret 只要是 `subPath` 挂载进
    长驻进程(不是 Job/CronJob 这种每次都重新创建 pod 的),新增/改动
    账号之后都要记得手动重启一次对应 Deployment/StatefulSet,不能
    假设它会自己感知到。

### 自建的 `python:3.12-slim` 薄应用 pip install 反复 exit 124,但 `curl` 从其他 pod 测同一个网络明明是通的

- **症状**:`table-registration-app` 这个自建薄应用(ConfigMap 挂源码
  + `pip install` 启动)反复 `CrashLoopBackoff`,`kubectl describe pod`
  显示 `exitCode: 124`(`timeout` 命令自己触发的,不是应用代码报错)。
  从另一个 pod 直接 `curl` `pypi.org`/`deb.debian.org` 都能秒回 200,
  说明这台机器当时的网络本身是通的,不是"完全连不上"这种直观的网络
  故障。
- **定位**:怀疑这类"网络间歇性故障"时,先用一个独立的、干净的 pod
  (比如 `curlimages/curl`)直接测目标地址通不通,能排除"网络整体故障"
  和"这个 Deployment 自己的网络配置缺失"这两种可能,不要一上来就怀疑
  DNS 或者代理软件本身出了新问题。
- **原因**:这个 Deployment 的 `env` 里完全没有配 `HTTP_PROXY`/
  `HTTPS_PROXY`——`apps/iam-sync/manifests/cronjob.yaml` 早就因为同一
  类问题配了这两个变量(colima 虚拟网络直连外网不稳定,需要走宿主机
  代理),但后来新建的 `table-registration-app`/
  `permission-request-app` 这两个自建应用的 Deployment 漏配了,当时
  想当然地认为"这个组件只用 pip 不用 git/apt-get,应该不会踩到同一个
  坑"——这个假设是错的,pip 连 PyPI 一样会受这台机器网络不稳定的影响,
  不是只有 apt-get/git 会。
- **处置**:两个 Deployment 都补上和 `iam-sync` 一致的 `HTTP_PROXY`/
  `HTTPS_PROXY`/`NO_PROXY` 三个环境变量(地址来自
  `colima ssh -- env | grep -i proxy`)。以后新建任何"`python:3.12-slim`
  镜像 + 启动时 `pip install`/`apt-get install`"这个模式的自建组件,
  直接照抄这三个环境变量,不要假设"这次不用 apt-get/git 应该没事"。
  - **同一个坑在 cloud-full 上又踩了一次(2026-08-19,journal)**:
    `iam-sync`/`opa-grants-sync` 两个 CronJob 硬编码的是 colima
    专用代理地址,cloud-full 上连不上——这次不是"漏配代理",是反过来
    "配了 local-lite 专属的代理,换了机器就不对"。处置改成运行时先
    探测这个代理还在不在,连不上就跳过、直连(cloud-full 直连
    pypi.org/mirrors.aliyun.com 本身是通的,实测确认)。**教训扩展**:
    "要不要配代理"不能写死为固定 IP,不同环境(local-lite 走宿主机
    代理 / cloud-full 直连)网络前提完全不同,配置要做成能自适应或者
    按环境区分,不能假设所有环境都和 local-lite 一样需要代理。

### `apt-get install` 卡死不动,`Acquire::Retries` 不管用:apt 自己的 "delayed item" 重试队列是另一套机制

- **症状**:`apt-get install` 挂着不动,`crictl exec` 进容器用
  `/proc/*/cmdline` 看,进程还在 `/usr/lib/apt/methods/http` 这个子
  进程里,和"CRD 太大""kubectl logs 报 Privoxy Error"这几条不是同一类
  问题——已经加了 `Acquire::http::Timeout`/`Acquire::Retries` 这几个
  选项(见 `apps/iam-sync/manifests/cronjob.yaml` 的教训),理论上应该
  会按重试次数失败退出,但实际上不会。
- **定位**:用 `kubectl run --rm -i` 单独复现、把完整输出重定向落地
  (不要用 `-qq`,或者至少留一份不加 `-qq` 的输出用于排查)才能看清楚:
  apt 在某个具体包下载失败之后,会把它放进一个叫 "delayed item" 的
  内部重试队列,一直刷 `W: Tried to start delayed item <包名> ...,
  but failed`。
- **原因**:这个循环**不受 `Acquire::Retries` 这个参数约束**——它是
  apt pipelining/并行下载机制里一个独立的子系统,和
  `Acquire::http::Timeout`(单个 HTTP 请求的空闲超时)、
  `Acquire::Retries`(单个 URI 的重试次数)都不是一回事,加再多这两个
  选项也管不到这个队列。哪个具体包会触发这个问题看起来是偶发的(这次
  是 `perl`),不是固定复现某一个包,不值得深究"为什么偏偏是这个包"。
- **处置**:不追究 apt 内部这个机制的细节,直接在外面套一层
  `timeout -k 10 N`(比如 `timeout -k 10 90 apt-get ... install
  ...`),配合脚本本身的 `set -e`,让整个命令在合理时间内快速失败
  退出,而不是无限期挂着。**`-k`(kill-after)这个参数不能省**——
  `timeout` 默认超时后只发一次 SIGTERM,如果目标进程不响应(实测确认
  过一次:加了单纯 `timeout 90` 之后,同一类卡死还是撑过了 90 秒没
  退出),`timeout` 不会自动升级成 SIGKILL,等于没起作用;加 `-k 10`
  之后,SIGTERM 发出 10 秒还没死就强制 SIGKILL,保证一定能退出。这样
  一次性 Job/CronJob 的正常重试机制(`backoffLimit`)才有机会在
  `activeDeadlineSeconds` 这个大限之内真正多试几次,而不是一次尝试就
  把整个时间窗口耗光在一个注定要失败的包上。
  - **教训**:这类"看着像网络卡住,加了标准的 HTTP 超时选项却没用"的
    情况,不要预设是网络层的问题、也不要预设是自己漏配了哪个 apt
    选项——先把完整、不省略的日志/进程状态拿到手(`crictl exec` 直接
    看 `/proc`,或者临时去掉 `-qq`/加输出重定向到文件),再判断问题
    到底出在哪一层。

### postgres 镜像版本升级后,下游建库 Job 在 postgres 真正就绪前就打满重试次数

- **背景**:2026-08-16,cloud-full 首次拉起,journal。
- **症状**:postgres 镜像版本升级后(16.6→16.15,版本审计的一部分),
  cloud-full 没有对应的镜像缓存,`hive-metastore-create-db`/
  `keycloak-create-db` 这些建库 Job 在 postgres 真正起来之前就已经
  耗尽重试次数报错。
- **定位**:`kubectl get job -n <ns>` 看这些建库 Job 是不是 Failed,
  对照 postgres pod 的启动时间(通常是因为新镜像还要重新拉,启动比
  平时慢)。
- **处置**:postgres 起来后手动删了这几个失败的 Job 触发重建,它们
  自己不会重试。确认修好:重新触发后建库 Job 成功。
  - **和"`airflow-migrate-db` 不会自愈"是同一类问题**(见"ArgoCD /
    GitOps 层"),都是"一次性 Job 的重试次数在依赖真正就绪之前就被
    耗光"这个模式,以后新增任何"等 Postgres 就绪才能跑"的 Job,最好都
    照抄 `airflow-migrate-db` 那次加的"先等依赖就绪再开始算重试"的
    等待循环,不要依赖 `backoffLimit` 本身。

### `trino:483` 收紧了配置校验,chart 生成的属性和我们的配置冲突,新版本直接拒绝启动

- **背景**:2026-08-19 前后,`docs/project/current-work.md`,cloud-full 首次
  拉起 Trino 时撞到。
- **症状**:Trino 用 `trino/trino:483` 这个版本直接拒绝启动。
- **定位**:`trino/trino:483` 对 `http-server.http.port` 收紧了配置
  校验,chart 无条件生成这行属性,和配置里关掉的
  `http-server.http.enabled` 冲突,483 版本会直接拒绝启动(旧版本不
  校验这个组合,能容忍这个"看似矛盾但实际上没用"的属性)。
- **处置**:回退到 chart 默认的 480 版本。确认修好:Trino 正常启动。
  - **教训**:`helm template` 渲染出来的 diff 一致(同一份 chart+values
    在不同机器上渲染结果逐字节相同),**不代表运行时行为一致**——
    组件自己的版本升级可能让原本"渲染出来但没实际影响"的配置组合
    突然变成硬性冲突,升级组件镜像版本时不能只看 chart/values 有没有
    变,还要看目标版本本身有没有收紧行为。

### dbt_demo DAG 三个连续的根因性 bug:只读挂载、NetworkPolicy 漏名单、catalog.json 缺步骤

- **背景**:2026-08-19 前后,cloud-full 首次真正端到端跑通
  `trino/superset/airflow` 核心链路时发现,journal。**这三个 bug 在
  local-lite 上大概率一直存在**,只是这个 DAG 之前从没有真的端到端
  跑完过一次,没人发现——不是"云端特有问题",是真正的历史遗留 bug
  第一次被暴露。
- **症状与定位(依次排查,每个都挡住过一次)**:
  1. `/project` 是只读 ConfigMap 挂载,dbt 没法写 `target/`/`logs/`,
     症状是 `exit code 2` 且没有任何输出(靠手动起调试 pod 才定位到,
     见下面"排查方法论"那条)。
  2. MinIO NetworkPolicy 消费者名单没有 `dbt` 命名空间(和
     2026-08-14 feast 那次是同一个模式的遗漏,见"K8s 资源生命周期"
     一节里 minio NetworkPolicy 那条)。
  3. `catalog.json` 不是 `dbt build` 的产物,是 `dbt docs generate`
     单独生成的,DAG 里没有这一步。
- **处置**:
  1. 复制到可写目录 `/workspace` 再跑,不直接在只读挂载目录里跑 dbt。
  2. 把 `dbt` 命名空间也加进 MinIO NetworkPolicy 的允许来源列表。
  3. DAG 里补 `dbt docs generate` 这一步。
  确认修好:`dbt_demo` DAG 手动触发,状态变成 `success`。

### 排查方法论:看不到真实错误时,起一个和 DAG 配置完全一致的调试 Pod 比反复触发整条 DAG 快得多

- **背景**:2026-08-19 前后,journal,`dbt_demo` 排查过程中总结出来的
  方法论,不是某个具体 bug,但值得单独记一条,以后遇到同类"黑盒故障"
  应该优先想到。
- **症状**:多次遇到"看不到真实错误"的情况——`get_logs=False` 是
  local-lite 专门绕过 Privoxy 问题的设置,继承到 cloud-full 上反而让
  人两眼一抹黑;`KubernetesPodOperator` 默认删除失败的 pod,连事后
  `kubectl describe` 都来不及看。
- **处置**:手动起一个和 DAG 里配置完全一致的调试 Pod(同样的
  ConfigMap 挂载/Secret/镜像),一步步交互执行每条命令,才能把三层
  叠加的 bug 一个个剥出来——这比反复改配置再触发真实 DAG 跑一轮
  (每轮几分钟)快得多。以后遇到"看不到日志"的黑盒故障应该优先想到
  这条路径,不要在"改一点、触发一次、等几分钟看结果"这个循环里反复
  横跳。

---

## 本机(colima)/ 云主机环境与脚本习惯

### colima 会自动把 k3s LoadBalancer 的 80/443 转发到 Mac 的 localhost

- **发现**:装完 ingress-nginx 后,`kubectl get svc -n ingress-nginx`
  显示 `EXTERNAL-IP` 是 `192.168.5.1`(colima VM 内部网关地址,Mac
  直接访问不通),一开始以为还要另外配端口转发才能从 Mac 访问
  Ingress。实测发现 `curl http://localhost/` 直接就有响应(404,
  ingress-nginx 默认后端)——colima 的 docker runtime 会自动把容器/
  k3s service 暴露的标准端口(80/443)转发到 Mac 的 `localhost`,不
  需要额外的 `kubectl port-forward` 或手动端口映射。
- **意义**:local-lite 可以用"真实 Ingress + `/etc/hosts` 静态域名"的
  方式访问所有走 Ingress 的组件,不再需要给每个组件单独开
  `port-forward`。域名约定是 `<组件>.local-lite.test`,在
  `/etc/hosts` 加一行 `127.0.0.1 <组件>.local-lite.test` 即可,见
  `docs/decisions/016-ingress-domains-local-lite.md`。
- **调试技巧**:不想每次都改 `/etc/hosts` 也能验证 Ingress 路由对不
  对,直接用 `curl -H "Host: <域名>" http://localhost/<path>` 伪造
  Host 头,效果和真的配了 DNS 一样,不影响 Mac 系统配置。

### bash 脚本用 `set -euo pipefail`,给不存在的东西 `grep` 会让脚本"悄悄卡住"

- **症状**:`scripts/03-configure-keycloak.sh` 跑到 `==> argocd
  client` 这一行之后就没有任何输出了,也不报错,脚本进程已经退出
  (`$?` 是 0 是因为外层套了 `| tee`,拿到的是 `tee` 的退出码,不是
  脚本自己的)。
- **定位**:`existing=$(kcadm get clients ... | grep -o
  '"[a-f0-9-]*"' | head -1 | tr -d '"')` 这种写法,在 client 还不存在
  时(全新 realm 第一次跑),`grep -o` 找不到匹配会返回非零。
- **原因**:`pipefail` 让整条管道的退出码变成非零,而这个管道又是在
  给变量赋值(`existing=$(...)`),`set -e` 对"命令替换赋值"整体是否
  失败也会生效——直接终止脚本,且不打印任何错误信息,表现就是"卡在
  某一行不动了"。
- **处置**:在这类"找不到是正常情况,不是错误"的管道末尾加
  `|| true`,把"没找到"和"真正的命令失败"区分开。这不是绕过错误
  检查,是本来就该有的容错——`grep` 找不到匹配本身就是预期会发生的
  正常分支,不该被当成脚本级别的致命错误。
  - **教训**:`set -euo pipefail` 是好习惯,但每次写
    `x=$(cmd_a | grep ... | cmd_b)` 这种"grep 可能合理地找不到东西"
    的管道时,要主动想一下"找不到"算不算失败,算的话让它正常报错
    退出,不算的话显式 `|| true`,不要让它随机地看运气。

### `03-configure-keycloak.sh` 遇到还没 unpark 的命名空间直接报错退出,后面的 client 全部没建成

- **背景**:2026-08-16,cloud-full 首次拉起,journal。
- **症状**:脚本跑到还没 unpark(仍在 `pending-definitions`)的
  `jupyterhub` 命名空间就直接报错退出(`set -euo pipefail`),后面
  trino/superset/openmetadata 等组件的 client 全部没建成。
- **定位**:看脚本在哪个命名空间检查处退出,对照
  `environments/<env>/pending-definitions/` 里当前还没启用的组件
  列表。
- **处置**:补了命名空间存在性检查,不存在就跳过继续,不中止整个
  脚本。确认修好:重跑后即使有组件仍在 pending,其余已启用组件的
  client 都能正常建成。

### cloud-full 上 pip/kubectl 下载被限速到几十 KB/s,换阿里云镜像站秒装

- **症状**:`table-registration-app` 云端 CrashLoopBackOff,根因是这台
  云主机到 PyPI 官方 CDN 的真实带宽瓶颈(约 22 kB/s,不是"抢带宽"的
  猜测)。同一批排查里,`iam-sync` 的 `fetch-kubectl` initContainer
  裸 curl 下 `dl.k8s.io` 的 kubectl 二进制(59MB)也被限速到约
  50kB/s。
- **定位**:`colima ssh`/云主机上直接 `curl -o /dev/null -w
  "%{speed_download}\n" <url>` 实测下载速度,确认是真实带宽瓶颈,不是
  连接失败或代理问题。
- **处置**:
  - `table-registration-app`:换阿里云 PyPI 镜像
    (`mirrors.aliyun.com/pypi`)后 8 秒装完。
  - `iam-sync`:`fetch-kubectl` 改成 apt 装 `mirrors.aliyun.com` 镜像
    的官方仓库,不再裸 curl 下二进制。
  确认修好:两个都手动触发过真实 Job/Pod 运行确认端到端生效。
  - **和 ADR-061"helm 120 秒超时"是同一类根因**(境内到 GitHub/PyPI
    官方 CDN 的出口带宽本身就慢,不是间歇性故障),遇到"云主机上装
    东西/拉东西异常慢但没报错"优先怀疑这个,不要先怀疑配置错误。

---

## 已废弃 / 仅存档

见"认证 SSO 层"一节的
[ArgoCD 接 Keycloak OIDC,登录跳转到集群内部域名](#argocd-接-keycloak-oidc登录跳转到集群内部域名浏览器打不开已废弃仅存档)
——这是目前唯一一条已废弃、仅作参考存档的记录,放在认证 SSO 层里是
因为它的技术背景(issuer 单点、split-horizon DNS)和现在的 SSO 条目
放在一起更方便对照阅读,这里只留一个指路,不重复内容。
