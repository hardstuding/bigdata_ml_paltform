# 常见问题排查

> 施工过程中遇到的真实问题按时间顺序往这里加,格式:现象 → 原因 → 处理方式。这份文档主要给未来的 AI Agent 和人类共同排障用,记录要具体(报错信息、命令、涉及的组件版本)。
>
> 下面正文条目仍按时间顺序排(新问题接着加在最后,不要为了分类插到中间——插入会打乱和 git blame/commit 的对应关系)。这份索引按"症状类别"分组,是排障时按现象快速定位用的入口,和正文顺序是两回事,2026-08-20 补上(此前这里只有一个空的 `## 索引` 标题,没有真正的索引内容,出事时只能整份文档 Ctrl+F)。

## 索引

### 网络 / 镜像拉取

- [kubectl logs / exec 在这台机器上直接报 "Internal Privoxy Error"](#kubectl-logs--exec-在这台机器上直接报-internal-privoxy-error)
- [某些镜像仓库(如 quay.io)在这个网络下连不上,但 docker hub 是通的](#某些镜像仓库如-quayio在这个网络下连不上但-docker-hub-是通的)
- [`apt-get install` 卡死不动,`Acquire::Retries` 不管用:apt 自己的 "delayed item" 重试队列是另一套机制](#apt-get-install-卡死不动acquireretries-不管用apt-自己的-delayed-item-重试队列是另一套机制)
- [自建的 `python:3.12-slim` 薄应用 pip install 反复 exit 124,但 `curl` 从其他 pod 测同一个网络明明是通的](#自建的-python312-slim-薄应用-pip-install-反复-exit-124但-curl-从其他-pod-测同一个网络明明是通的)
- [推倒重建集群之后,ArgoCD/Trino/Superset/OpenMetadata/MLflow 这类做 OIDC discovery 的组件全部连超时](#推倒重建集群之后argocdtrinosupersetopenmetadatamlflow-这类做-oidc-discovery-的组件全部连超时)
- [Alloy 采不到日志:`loki.source.kubernetes` 拉不到数据,换成 hostPath 又报 "no such file or directory"](#alloy-采不到日志lokisourcekubernetes-拉不到数据换成-hostpath-又报-no-such-file-or-directory)

### ArgoCD / GitOps

- [kube-prometheus-stack 的 CRD 一直 OutOfSync,Prometheus 资源起不来](#kube-prometheus-stack-的-crd-一直-outofsyncprometheus-资源起不来)
- [prometheus-operator 起来了但一直不创建 StatefulSet,Prometheus CR 的 status 一直是空的](#prometheus-operator-起来了但一直不创建-statefulsetprometheus-cr-的-status-一直是空的)
- [Helm Application 手动 patch 过 Deployment 之后,ArgoCD 卡住不再应用新的 git 变更](#helm-application-手动-patch-过-deployment-之后argocd-卡住不再应用新的-git-变更)
- [从 apps/definitions 挪走一个组件后,ArgoCD 里那个 Application 卡在 Missing 删不掉](#从-appsdefinitions-挪走一个组件后argocd-里那个-application-卡在-missing-删不掉)
- [手动 `helm template | kubectl apply` 绕过 ArgoCD 之后,命名空间删不掉,卡在 Terminating](#手动-helm-template--kubectl-apply-绕过-argocd-之后命名空间删不掉卡在-terminating)
- [git push 之后,ArgoCD 迟迟不应用新配置——标准排查步骤](#git-push-之后argocd-迟迟不应用新配置标准排查步骤)
- [ArgoCD Application 显示 Healthy,但里面唯一的 Job 其实从来没跑过](#argocd-application-显示-healthy但里面唯一的-job-其实从来没跑过)
- [ArgoCD 卡在 "waiting for healthy state of ..." 不动,手动改了 values 也没用](#argocd-卡在-waiting-for-healthy-state-of--不动手动改了-values-也没用)
- [CRD 太大报 "annotations too long",`ServerSideApply=true` 不是每次都管用](#crd-太大报-annotations-too-longserversideapplytrue-不是每次都管用)
- [Airflow scheduler 反复长出两个并存 ReplicaSet、ArgoCD 子 Application spec 一度没跟上 git——根因是 ArgoCD 控制面自己被 OOMKilled,不是 Airflow chart 的 bug](#airflow-scheduler-反复长出两个并存-replicasetargocd-子-application-spec-一度没跟上-git根因是-argocd-控制面自己被-oomkilled不是-airflow-chart-的-bug)

### 数据平台组件(Hive / Trino / Iceberg / Postgres / Keycloak / OpenMetadata / Superset)

- [Hive Metastore 建 Iceberg schema 报错 "Failed to create external path ... : null"](#hive-metastore-建-iceberg-schema-报错-failed-to-create-external-path---null)
- [Trino 建 Iceberg schema 时指定 location 会报错,不指定就正常](#trino-建-iceberg-schema-时指定-location-会报错不指定就正常)
- [Superset 报 ModuleNotFoundError: No module named 'psycopg2'(或 'authlib')](#superset-报-modulenotfounderror-no-module-named-psycopg2或-authlib)
- [Keycloak start-dev 自带的 H2 是内存/临时数据库,pod 重启就把 realm 全部丢光](#keycloak-start-dev-自带的-h2-是内存临时数据库pod-重启就把-realm-全部丢光)
- [改 coredns-custom 加自定义域名解析,CoreDNS 直接 CrashLoopBackOff(集群 DNS 短暂中断)](#改-coredns-custom-加自定义域名解析coredns-直接-crashloopbackoff集群-dns-短暂中断)
- [OpenMetadata 改了 OIDC 环境变量,`/api/v1/system/config/auth` 还是显示旧的 basic 认证](#openmetadata-改了-oidc-环境变量apiv1systemconfigauth-还是显示旧的-basic-认证)
- [组件重新拉起来报 "password authentication failed",Postgres 密码"变了"](#组件重新拉起来报-password-authentication-failedpostgres-密码变了)
- [OpenMetadata SSO 登录报 "Account already exists. Please contact administrator."——不是配置问题,是数据库里一条半损坏的用户记录](#openmetadata-sso-登录报-account-already-exists-please-contact-administrator不是配置问题是数据库里一条半损坏的用户记录)

### K8s 资源生命周期 / NetworkPolicy

- [Helm chart 的 envFromSecrets(复数)不一定覆盖所有容器,initContainer 可能读不到](#helm-chart-的-envfromsecrets复数不一定覆盖所有容器initcontainer-可能读不到)
- [同一个命名空间里的 Job 连不上同命名空间的 pod,NetworkPolicy 报 "connection refused"](#同一个命名空间里的-job-连不上同命名空间的-podnetworkpolicy-报-connection-refused)
- [`kubectl delete pod` 删 CNPG 的 Postgres pod 卡在 `Terminating` 十几分钟不退出](#kubectl-delete-pod-删-cnpg-的-postgres-pod-卡在-terminating-十几分钟不退出)
- [`KubernetesPodOperator` 拉起跨命名空间/自定义镜像的 Spark 任务,一路要闯好几关(RBAC、日志流、容器 UID、模板变量)](#kubernetespodoperator-拉起跨命名空间自定义镜像的-spark-任务一路要闯好几关rbac日志流容器-uid模板变量)
- [新建的 Job/CronJob 的 pod 刚起来第一次连接直接 Connection refused,但同样标签的 pod 手动测试是通的](#新建的-jobcronjob-的-pod-刚起来第一次连接直接-connection-refused但同样标签的-pod-手动测试是通的)
- [给 ConfigMap 新增一个 key 之后,subPath 挂载这个新 key 的文件在 pod 里变成了一个空目录](#给-configmap-新增一个-key-之后subpath-挂载这个新-key-的文件在-pod-里变成了一个空目录)

### 本机(colima)环境 / 脚本习惯

- [colima 会自动把 k3s LoadBalancer 的 80/443 转发到 Mac 的 localhost](#colima-会自动把-k3s-loadbalancer-的-80443-转发到-mac-的-localhost)
- [bash 脚本用 `set -euo pipefail`,给不存在的东西 `grep` 会让脚本"悄悄卡住"](#bash-脚本用-set--euo-pipefail给不存在的东西-grep-会让脚本悄悄卡住)

### 已废弃 / 仅存档

- [ArgoCD 接 Keycloak OIDC,登录跳转到集群内部域名,浏览器打不开(已废弃,仅存档)](#argocd-接-keycloak-oidc登录跳转到集群内部域名浏览器打不开已废弃仅存档)
### kube-prometheus-stack 的 CRD 一直 OutOfSync,Prometheus 资源起不来

- **现象**:ArgoCD 里 `kube-prometheus-stack` Application 长期 `OutOfSync`,`kubectl get crd prometheuses.monitoring.coreos.com` 报 NotFound,Prometheus 的 Pod/StatefulSet 一直没创建出来。
- **原因**:prometheus-operator 的 CRD(尤其是 `prometheuses.monitoring.coreos.com`)体积很大,超过了 kubectl client-side apply 用来记录 `kubectl.kubernetes.io/last-applied-configuration` 的 annotation 大小上限(262144 字节),ArgoCD 默认走 client-side apply,导致这几个 CRD 应用失败。
- **处理(第一步,不够)**:给这个 Application 的 `syncPolicy.syncOptions` 加 `ServerSideApply=true`。**实测这一步不够** —— 即使开了 SSA,ArgoCD 在这几个 CRD 上还是会踩到同样的 "annotations too long" 校验错误(具体是 ArgoCD 内部哪个环节导致的还没深究,推测和它渲染/diff 时的某种 dry-run 行为有关)。
- **实际有效的处理**:把 CRD 从 ArgoCD 的管理范围里摘出去,单独用原生 `kubectl apply --server-side` 装:
  ```bash
  ./scripts/04-install-kube-prometheus-crds.sh
  ```
  然后在 chart 的 values 里设 `crds.enabled: false`,让 ArgoCD 只管 chart 本体(Deployment/Prometheus CR 等),不再插手 CRD 的创建。这是和 ArgoCD 本身、`platform/root-app.yaml` 一样的"允许手动执行"的例外(见 ADR-005),升级 chart 版本、CRD schema 变化时需要重新跑一遍这个脚本。
- **涉及文件**:`platform/apps/kube-prometheus-stack.yaml`、`scripts/04-install-kube-prometheus-crds.sh`

### prometheus-operator 起来了但一直不创建 StatefulSet,Prometheus CR 的 status 一直是空的

- **现象**:`kube-prometheus-stack-operator` Pod 是 `Running`/`Ready`,`kubectl top` 看 CPU 几乎是 0(说明它没在干活,不是卡在重活里)。`kubectl get prometheus` 能看到 CR,但 `.status` 一直是空对象,迟迟不出现对应的 StatefulSet/Pod。RBAC(`kubectl auth can-i`)检查全部正常,不是权限问题。
- **原因不确定**:大概率是 operator 进程在某个初始化步骤(比如 informer 首次 List/Watch)卡死了,但因为没配置 liveness probe,k8s 没检测出来去重启它,readiness probe 又恰好能过,所以外部看着"Running/Ready"其实内部没在正常工作。具体卡在哪一步没查(受限于下面这条本机网络问题,拿不到它的日志)。
- **处理**:直接 `kubectl delete pod -l app=kube-prometheus-stack-operator` 让它重建,新 Pod 起来后几十秒内就正常创建出 StatefulSet 了。以后再遇到"资源部署了但一直没有下游对象、CPU 几乎为 0"这种症状,先怀疑组件卡死,重启对应 Pod 试试,不用一直等。

### kubectl logs / exec 在这台机器上直接报 "Internal Privoxy Error"

- **现象**:`kubectl -n <ns> logs ...`、`kubectl -n <ns> exec ...` 这类需要直连 kubelet(端口 10250)的操作,无论有没有设置 `HTTP_PROXY`/`NO_PROXY` 环境变量都会报错,错误里能看到实际在请求 `https://192.168.5.1:10250/...`,返回 `Internal Privoxy Error`。就算从 colima 虚拟机内部执行同样命令也一样报错。
- **原因**:这台 Mac 上装的代理工具(从报错特征看是走 Privoxy 的那一类,比如 Surge / ClashX 的增强模式)在**系统网络层**做透明拦截,不是靠进程读 `HTTP_PROXY` 环境变量生效的,所以在 shell 里 unset 代理变量没用——连虚拟机自己发出的流量都被拦了。它把发往 `192.168.5.0/24`(colima 虚拟网络的私网段)这种内网地址的流量也当成"要走代理"处理,但代理软件自己又不知道怎么路由到这种私网地址,所以报错。
- **需要用户处理的部分(我这边无法从命令行修复)**:在代理工具里给 `192.168.5.0/24`(colima 的虚拟网络段,如果之后网段变了以此类推)、`10.0.0.0/8` 这类私网地址加"直连/不代理"的规则,或者调试期间临时关掉增强模式/TUN 模式。加完规则后 `kubectl logs`/`kubectl exec` 应该就正常了。
- **临时绕过办法(不需要用户处理时)**:改用不经过这条路径的诊断方式——`kubectl describe`、`kubectl get -o yaml`(看 `.status`/`.status.conditions`)、`kubectl get events`、`kubectl top`、`kubectl auth can-i`,这些都走 API server 的常规请求路径,不受影响。真的需要看容器内部日志时,先怀疑"组件是不是卡死了",直接重启 Pod 往往比死磕日志更快。更彻底的绕过:`colima ssh -- sudo crictl ps -a` 找到容器 ID,`colima ssh -- sudo crictl logs <id>` 直接走本地 containerd socket,完全不碰网络。
- **2026-08-09 补充**:装 Loki+Alloy 集中日志时,Alloy 的 `loki.source.kubernetes` 组件(通过 K8s API 的 `containerLogs` 接口拉日志,原理和 `kubectl logs` 完全一样)在集群**内部**照样踩了同一个坑——不是只有我本机的 `kubectl` 会被拦,任何组件只要走 API server -> kubelet 这条 `containerLogs` 代理路径,都会被同样拦截。这进一步确认了上面"连虚拟机自己发出的流量都被拦"的判断。处理方式是从设计上完全绕开这条路径,见下面 Alloy 那条记录,不是加代理白名单就能一劳永逸解决的(因为拦截规则的具体网段/范围不受我们控制)。

### 某些镜像仓库(如 quay.io)在这个网络下连不上,但 docker hub 是通的

- **现象**:Pod 一直 `ImagePullBackOff`,事件里是 `TLS handshake timeout` 或下载到一半 `EOF`。`colima ssh` 里直接 `curl` 目标 registry 也是 `Connection timed out`,换成 `https://registry-1.docker.io/v2/` 测试却能正常返回(401 也算通,那是正常的匿名访问被拒)。
- **原因**:本机代理对不同站点的连通性不一致,quay.io 这类站点即使走代理也可能连不上,不是配置错误,是这条网络对特定站点没有稳定路由。
- **处理**:很多镜像(包括 MinIO)官方会同时发布到 docker hub 和 quay.io,遇到这种情况直接把 chart/manifest 里的 `image.repository` 换成 docker hub 上的同名镜像,不用死磕代理配置。以后新组件如果也卡在拉镜像,先用 `colima ssh -- curl` 测一下目标 registry 通不通,别默认怀疑是 k8s 配置问题。

### Hive Metastore 建 Iceberg schema 报错 "Failed to create external path ... : null"

- **现象**:Trino 里 `CREATE SCHEMA iceberg.xxx WITH (location = 's3://...')` 报
  `Failed to create external path s3://... for database xxx. This may result in
  access not being allowed if the StorageBasedAuthorizationProvider is enabled: null`,
  错误信息里那个 `null` 没有任何有用的堆栈信息。
- **原因**:以为给 Hive Metastore 的 `SERVICE_OPTS` 加 `-Dfs.s3a.endpoint=...`
  这类 JVM 系统属性就能配置 S3A 客户端,结果完全不生效——**Hadoop 的
  `Configuration` 类只从 XML 配置文件(`core-site.xml`)读取 `fs.s3a.*` 这类
  属性,不会读 JVM 的 `-D` 系统属性**。SQL 层面的报错信息把真正原因盖住了,
  得去 Hive Metastore 自己的日志(`grep -i "s3a\|MetaException"`)才能看到
  `Failed to create external path` 这条更具体的 metastore 侧报错。
- **处理**:改用 ConfigMap 挂载真正的 `core-site.xml` 到
  `/opt/hive/conf/core-site.xml`,`fs.s3a.access.key`/`fs.s3a.secret.key`
  这类敏感值用 `${env.VAR}` 语法引用容器环境变量(Hadoop 配置支持这个
  插值语法),不直接写死在 ConfigMap 里。
- **涉及文件**:`apps/hive-metastore/manifests/deployment.yaml`、
  `apps/hive-metastore/manifests/core-site-configmap.yaml`

### Trino 建 Iceberg schema 时指定 location 会报错,不指定就正常

- **现象**:`CREATE SCHEMA iceberg.xxx WITH (location = 's3://lakehouse/xxx/')`
  报和上一条一样的 `Failed to create external path ... : null`,但把
  `WITH (location = ...)` 去掉、直接 `CREATE SCHEMA iceberg.xxx`(用 Hive
  Metastore 默认的 warehouse 路径)就能成功建表、写入、读出,完全正常。
- **结论**:S3A 连接本身没问题(能证明,因为默认路径的读写全部成功,数据
  真的落到了 MinIO 的 `opt/hive/data/warehouse/xxx.db/...` 下),问题窄化到
  "显式指定 external location"这一条特定路径上,具体是 HMS 侧对显式路径
  多做的存在性校验/权限检查(错误信息里提到的
  `StorageBasedAuthorizationProvider`)出了什么问题,还没深挖。
- **现状**:**不指定 location,用默认路径** 是当前的可行方案,没有继续深究
  显式 location 这条路径——已经达成"验证 Trino/Iceberg/Hive Metastore/MinIO
  能端到端工作"这个目标,显式指定 bucket 内子路径这个需求不紧急,不值得
  为此无限排查下去。真的需要显式控制表的物理路径时再回来查。
- **附带发现**:默认路径会带上 `opt/hive/data/warehouse/` 这一截前缀(来自
  Hive 的 `hive.metastore.warehouse.dir` 默认值,被当成 s3a 路径里的 key
  前缀),不是干净的 `s3://lakehouse/xxx.db/...`。能用,但不好看,以后可以
  通过显式设置 `hive.metastore.warehouse.dir=s3a://lakehouse/warehouse/`
  来清理,不紧急。

### Helm chart 的 envFromSecrets(复数)不一定覆盖所有容器,initContainer 可能读不到

- **现象**:用 `envFromSecrets: [my-secret]`(追加一个额外 Secret)想覆盖数据库连接信息,
  主容器能读到,但某个 initContainer(比如 Superset chart 的 `wait-for-postgres`)
  一直卡在 `Init:0/1`、不断重试连接一个不存在的默认 host,像是完全没读到新配置。
- **原因**:chart 的模板作者经常只在"主容器"或者"部分资源"上接了
  `envFromSecrets`(复数,追加语义),initContainer 的 `envFrom` 可能是硬编码
  只引用 `envFromSecret`(单数,chart 自己生成的那一个默认 Secret)的名字,
  不会去遍历 `envFromSecrets` 列表。这不是 bug,是 chart 实现细节,不同 chart
  的处理方式可能不一样,不能默认"加一个 secret 全局都能覆盖"。
- **处理**:遇到这种情况,直接**覆盖 `envFromSecret`(单数)这个值本身**,让它
  指向自己建的 Secret,而不是用复数形式"追加"——相当于完全替换 chart 默认
  生成的那个 Secret,而不是叠加一个新的。前提是自己的 Secret 要把默认 Secret
  里所有会被引用到的 key 都补全(哪怕用不上的功能对应的 key 也要给个占位值,
  比如关掉 Redis 后 `REDIS_*` 这几个 key 还是要存在,只是不会被用到)。
- **排查方法**:改之前先跑 `helm template <chart> --set ... | grep -B20 "name: <initContainer名>"`
  看它的 `envFrom` 实际引用的是哪个 Secret 名字,不要靠猜。

### Superset 报 ModuleNotFoundError: No module named 'psycopg2'(或 'authlib')

- **现象**:Superset 主容器 CrashLoopBackOff,日志里是
  `ModuleNotFoundError: No module named 'psycopg2'`,发生在初始化数据库连接的时候。
- **原因**:官方 `apache/superset` 镜像不自带 Postgres 驱动,chart 默认的
  `bootstrapScript` 也不会装。用 Bitnami 那套(chart 默认依赖)时不会遇到,因为
  Bitnami 的镜像/流程不一样;一旦按 ADR-008 换成外部 Postgres,这个驱动缺失
  就暴露出来了——接外部 Postgres 时的已知通用问题,不是这个项目特有的配置错误。
- **处理**:不要猜 pip 路径(试过裸 `pip install`、试过 `/app/.venv/bin/pip`,
  都不对——镜像实际用 `uv` 管理虚拟环境,不是标准 venv 布局)。**镜像自带官方
  脚本 `/app/docker/pip-install.sh`**,专门用来在这个环境里装额外的 Python
  包(内部调用 `uv pip install`),用这个才是对的:
  `/app/docker/pip-install.sh psycopg2-binary`。排查这类"装了但还是
  ModuleNotFoundError"的问题时,先进容器找官方有没有现成的安装脚本
  (`find /app -iname "*install*"`),不要瞎猜 pip 在哪。
- **2026-08-09 复现,换了个包**:接 Keycloak OAuth2 时(`AUTH_TYPE =
  AUTH_OAUTH`),同样的报错又出现一次,这次缺的是 `authlib`——
  Flask-AppBuilder 的 OAuth 支持依赖它,基础镜像同样不带,只有真正启用
  `AUTH_TYPE = AUTH_OAUTH` 这条 import 路径才会暴露,启动阶段测不出来。
  处理方式一样,`pip-install.sh` 后面多加一个包名就行:
  `/app/docker/pip-install.sh psycopg2-binary authlib`。**教训**:这个镜像
  "按需装包"的模式下,每接一个新功能(数据库驱动、认证方式、以后可能的
  缓存后端)都可能暴露一个新的缺包,不是一次装完就一劳永逸,踩到就加,
  不用因为"上次刚修过一个"就觉得这次不该出现同类问题。
- **2026-08-10 再复现,这次是"连接哪个数据源"层面**:同一个规律不只出现在
  Superset 自己连元数据库/认证这些内建功能上——**给 Superset 添加一个新的业务
  数据源(SQL Lab 里连 Trino/ClickHouse/MySQL 等)也是同一套"缺哪个 SQLAlchemy
  dialect 包就报哪个 ModuleNotFoundError / Could not load database driver"**,
  同样要在 `bootstrapScript` 里补对应的包(Trino 是 `trino`,ClickHouse 通常是
  `clickhouse-connect`,MySQL 是 `mysqlclient` 或 `pymysql`,以此类推)。这是
  接入新数据源清单里必须提前想到的一步,不是等报错了才知道要加。

### Helm Application 手动 patch 过 Deployment 之后,ArgoCD 卡住不再应用新的 git 变更

- **现象**:直接 `kubectl apply` 手动改过一个由 ArgoCD 管理的 Deployment(比如
  为了打破"等待健康"死锁),之后即使改了 git 里的配置、hard refresh、强制
  sync,Application 一直卡在 `OutOfSync`/`Progressing`,Deployment 的实际内容
  长时间不更新,新的修复迟迟不生效。
- **原因**:手动 patch 制造的"实际状态"和 Git 期望状态之间的 diff,再加上
  Deployment 本身处于不健康状态(旧问题还没解决),会让 ArgoCD 的多阶段同步
  逻辑卡在评估"这一步健康了没"上,新变更迟迟排不上号——是前面几条"死锁"类
  问题的复合叠加,不是单一原因。
- **处理**:与其反复等 ArgoCD 自己收敛,不如直接把"这一轮手动 patch 应该长
  什么样子"想清楚,一次性 `kubectl apply` 到位(包括所有相关的 env/volume 引用
  都要改对,不要只改一半),用真实运行的报错(`crictl logs`)一步步验证,
  而不是干等 ArgoCD 的状态機自己转过来。

### 从 apps/definitions 挪走一个组件后,ArgoCD 里那个 Application 卡在 Missing 删不掉

- **现象**:把某个组件的 `.yaml` 从 `apps/definitions/` 挪到
  `environments/cloud-full/pending-definitions/`(按标准流程收起来)之后,
  `kubectl get applications -n argocd` 里那个 Application 一直显示
  `OutOfSync`/`Missing`,删不掉,`apps-root` 自己也跟着卡在 `OutOfSync`/
  `Progressing` 不收敛,hard refresh、重启 repo-server 都没用。
- **原因**:如果在组件还没在 git 里正式移除之前,已经手动清理过它的命名空间
  (比如 `kubectl delete namespace xxx`),这个 Application 的 `finalizers`
  (`resources-finalizer.argocd.argoproj.io`)在真正被 apps-root 判定要删除时,
  会尝试去确认"所管理的资源都清理干净了"才肯放行删除——但它认的资源列表
  可能是不完整/过期的,导致这个确认过程卡住,finalizer 一直移不掉,对象就
  一直卡在"正在删除"状态出不来。
- **处理**:确认这个组件真的没有任何残留资源在集群里之后(不确定就
  `kubectl get all -n <那个命名空间>` 查一遍),直接把这个 Application 的
  finalizers 清空来放行:
  `kubectl -n argocd patch application <名字> --type merge -p '{"metadata":{"finalizers":[]}}'`。
  这是"确认没有东西要清理,只是卡住了"这个前提下的合理操作,不是绕过安全检查
  ——如果还没确认清楚集群里没有残留资源就这么干,可能会漏删东西。

### 手动 `helm template | kubectl apply` 绕过 ArgoCD 之后,命名空间删不掉,卡在 Terminating

- **现象**:为了绕开 ArgoCD 卡住的同步问题(前面几条提到的死锁),直接用
  `helm template | kubectl apply` 手动把资源怼上去。后续要清理这个命名空间时,
  一直卡在 `Terminating`,`kubectl get all -n <ns>` 显示还有个 Job 也卡在
  `Terminating`,而且这个 Job 根本不是我们自己写的(是 chart 自带的 init job)。
- **原因**:chart 自带的资源里,有些标了 `helm.sh/hook: post-install,post-upgrade`
  这类注解(通常配合 `init.jobAnnotations` 这种字段,本意是给 ArgoCD/Helm 的
  hook 机制用)。手动 `kubectl apply` 把这种资源怼进一个 ArgoCD 正在管理的
  命名空间时,ArgoCD 会给它加上 `argocd.argoproj.io/hook-finalizer` 这个
  finalizer,但因为这个资源根本没有经过 ArgoCD 自己的 sync 流程,ArgoCD 的
  控制器永远不会去"确认 hook 执行完毕"从而清掉这个 finalizer——变成一个
  永久卡住、删不掉的资源,拖着整个命名空间没法终止。
- **处理**:确认没有需要保留的东西之后,直接清空这个资源的 finalizers 放行:
  `kubectl -n <ns> patch job <name> --type merge -p '{"metadata":{"finalizers":[]}}'`。
- **更根本的教训**:手动 `helm template | kubectl apply` 是排查问题时的应急
  手段,不是常规操作——用完之后要意识到可能留下这类"ArgoCD 认识但没法正常
  管理"的资源,清理的时候要连带检查有没有卡住的 finalizer,不能假设
  `kubectl delete namespace` 一定能干净收尾。

### git push 之后,ArgoCD 迟迟不应用新配置——标准排查步骤

这个问题这次反复出现了好几次(不只是某一个组件的特例),整理成一套标准检查
顺序,以后遇到直接按这个走,不用每次重新摸索:

1. `git -C <repo> log -1 --format=%H` 拿到本地最新 commit
2. `kubectl -n argocd get application apps-root -o jsonpath='{.status.sync.revision}'`
   看 app-of-apps 本身同步到了哪个 commit——**先确认这一层对了,再往下查**,
   这一层没追上,底下所有子 Application 的配置都不可能是最新的
3. 如果 apps-root 没追上:hard refresh(
   `kubectl -n argocd patch application apps-root --type merge -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'`),
   等几秒后重新比对 revision;如果连着刷新几次都不动,重启 repo-server
   (`kubectl -n argocd delete pod -l app.kubernetes.io/name=argocd-repo-server`)
4. apps-root 追上之后,再检查具体那个子 Application 的 spec 是不是最新值:
   `kubectl -n argocd get application <name> -o jsonpath='{.spec.source.helm.valuesObject.<字段路径>}'`
   ——**这一步经常被跳过,以为 apps-root 追上了子 Application 就一定跟着更新了,
   实际不是,子 Application 自己也可能要再刷新一次**
5. 确认 Application 的 spec 是最新的之后,再检查实际部署的 Deployment/StatefulSet
   有没有跟上(`kubectl get deploy <name> -o jsonpath='{.spec.template.spec...}'`)——
   同样可能卡在"等待健康"死锁(前面几条已经讲过),需要手动删掉旧的
   Deployment/Pod 强制重建

不要只做"改一下 git、hard refresh 一次"就假设生效了去看 Pod 状态——Pod 起不来
的时候,先按上面 1-5 步确认问题出在哪一层,再决定下一步怎么修,能省很多来回。

### ArgoCD 接 Keycloak OIDC,登录跳转到集群内部域名,浏览器打不开(已废弃,仅存档)

> **2026-08-09 更新**:下面这套 split-horizon DNS 土办法已经被真正的 Ingress
> 方案替换掉了(`apps/keycloak-local-access/` 整个目录已删除),现在浏览器和
> 集群内部都走 `http://keycloak.local-lite.test`,经 ingress-nginx 统一入口,
> 差别只是浏览器经 `127.0.0.1`(colima 自动转发 80/443,见下面新增的条目),
> pod 内部经 `hostAliases` 指向 ingress-nginx-controller 的 ClusterIP。这一段
> 保留是因为如果以后哪个组件暂时没法用 Ingress、又要面对同样的"浏览器和
> 集群内部地址不一致"问题,这个思路还有参考价值。

- **现象**:点 ArgoCD 的 "LOG IN VIA KEYCLOAK",跳转到类似
  `http://keycloak-keycloakx-http.keycloak.svc.cluster.local/...` 的地址,浏览器报
  `DNS_PROBE_FINISHED_NXDOMAIN`(这个域名只有集群内部能解析)。
- **原因**:ArgoCD 的 OIDC 配置只有一个 `issuer` 字段,浏览器跳转登录页、和
  ArgoCD server pod 自己做 token 交换,都是用这一个地址——不像 Grafana 的
  `auth.generic_oauth` 那样能把 `auth_url`(浏览器用)和 `token_url`/`api_url`
  (后端用)分开配。本地用 port-forward 访问集群时,浏览器能到达的地址
  (`localhost:端口`)和集群内部 pod 能到达的地址(service DNS)根本不是一回事,
  一个 issuer 两头顾不上。
- **处理**:让两边用同一个域名、分别解析到各自能到达的地方(split-horizon DNS 的土办法)——
  1. `apps/keycloak-local-access/manifests/service.yaml` 建一个额外的 Service(不是
     Helm chart管理的那个,避免冲突),暴露一个固定端口。
  2. ArgoCD 这边用 `global.hostAliases`(注意不是顶层的 `hostAliases`,那个 key
     在 chart 里不生效,必须是 `global.hostAliases`)把这个域名在 pod 里解析到
     上面那个 Service 的 ClusterIP。
  3. 你自己的 Mac 上把同一个域名加进 `/etc/hosts` 指向 `127.0.0.1`:
     ```
     sudo sh -c 'echo "127.0.0.1 keycloak.local-lite.test" >> /etc/hosts'
     ```
  4. 浏览器这边的 `kubectl port-forward` 也转发到那个新 Service(端口和上面的域名对应上)。
  5. `issuer` 改成 `http://keycloak.local-lite.test:8180/auth/realms/platform`。
- **这是 local-lite 专属的临时方案**,不是架构的一部分——上了真实域名/ingress 之后,
  `apps/keycloak-local-access/` 整个目录、`global.hostAliases`、这条 issuer 配置全部
  可以删掉,换成真实的对外域名(浏览器和集群内部走同一个真实域名,天然没有这个问题)。
- **域名问题解决之后还会踩一个协议不一致的坑**:Keycloak 报 `We are sorry... Invalid
  parameter: redirect_uri`(注意这是 Keycloak 自己吐的错,和上面 ArgoCD 吐的
  "Invalid redirect URL" 是两个不同的检查点)。原因是 client 在 Keycloak 里注册的
  `redirectUris` 写的是 `http://`,但 `configs.cm.url` 改成 `https://` 之后 ArgoCD
  实际发起的回调请求是 `https://`,两边对不上。用 `kcadm.sh update clients/<id>`
  把 `redirectUris` 也改成 `https://` 就好,`scripts/03-configure-keycloak.sh`
  已经改成一开始就注册 https,不会再重现这个坑。

### ArgoCD Application 显示 Healthy,但里面唯一的 Job 其实从来没跑过

- **现象**:一个 Application 只包含一个 Job(比如 `airflow-db-init`),Application 状态是
  `Synced`/`Healthy`,但 `kubectl get jobs -n <ns>` 什么都没有,该 Job 要做的事
  (比如建数据库用户)根本没发生。下游依赖这个 Job 结果的组件(比如 Airflow 的
  Postgres 认证)会报"密码认证失败"之类的错,排查半天以为是密码不对,其实是
  Job 压根没跑过。
- **原因**:给这个 Job 加了 `argocd.argoproj.io/hook: PostSync` 注解。PostSync
  hook 的触发条件是"这个 Application 里的常规(非 hook)资源先同步完成",但如果
  Application 里**只有这一个 hook 资源、没有别的常规资源**,这个触发条件永远
  不成立,hook 实际上从来不会执行。ArgoCD 判断 Application 是否 Healthy 时,
  没有常规资源可评估,于是直接报 Healthy——一个空转的假健康状态,不代表任何
  东西真的跑成功了。
- **处理**:如果一个 Application 就是为了跑一个独立的 Job(不依赖同一
  Application 里其他资源的同步顺序),不要加 hook 注解,当成普通资源交给
  ArgoCD 管理就行,靠 Job 自身的幂等逻辑 + `backoffLimit` 重试保证正确性。
  hook 只在"这个动作必须发生在同一 Application 内其他资源同步之前/之后"
  这种真实依赖关系时才需要。
- **涉及文件**:`apps/airflow/manifests/create-db-job.yaml`

### ArgoCD 卡在 "waiting for healthy state of ..." 不动,手动改了 values 也没用

- **现象**:改了 Application 的 `helm.valuesObject`(比如换镜像源)、push 到 git、hard refresh、甚至手动触发 sync,Application 状态一直是 `OutOfSync` + `Running`,`operationState.message` 显示 `waiting for healthy state of apps/Deployment/xxx`,但 `kubectl get deploy -o yaml` 看那个 Deployment 的镜像还是旧的,根本没被更新过。
- **原因**:组件的 Helm chart 里有依赖 Deployment 先变健康才继续执行的 hook(比如 MinIO 的 `makeBucketJob`,建 bucket 前要等 MinIO 本身跑起来)。如果 Deployment 当前就是坏的(比如镜像拉不下来),ArgoCD 的多阶段同步会卡在"等这一步健康"上,永远等不到,新的镜像值也就没机会被应用下去——典型的先有鸡还是先有蛋。
- **处理**:先用 `kubectl set image deployment/<name> <container>=<新镜像>` 手动把 Deployment 改成能跑起来的状态,打破这个死锁。等它变健康,ArgoCD 的 selfHeal 会自动接管,后续的 hook(如建 bucket 的 Job)也能正常触发,不用手动全部做完——通常再手动 sync 一次让 hook Job 重跑就行,不需要额外处理。

### colima 会自动把 k3s LoadBalancer 的 80/443 转发到 Mac 的 localhost

- **发现**:装完 ingress-nginx 后,`kubectl get svc -n ingress-nginx` 显示
  `EXTERNAL-IP` 是 `192.168.5.1`(colima VM 内部网关地址,Mac 直接访问不通),
  一开始以为还要另外配端口转发才能从 Mac 访问 Ingress。实测发现 `curl
  http://localhost/` 直接就有响应(404,ingress-nginx 默认后端)——colima 的
  docker runtime 会自动把容器/k3s service 暴露的标准端口(80/443)转发到 Mac
  的 `localhost`,不需要额外的 `kubectl port-forward` 或手动端口映射。
- **意义**:local-lite 可以用"真实 Ingress + `/etc/hosts` 静态域名"的方式访问
  所有走 Ingress 的组件,不再需要给每个组件单独开 `port-forward`。域名约定是
  `<组件>.local-lite.test`,在 `/etc/hosts` 加一行 `127.0.0.1 <组件>.local-lite.test`
  即可,见 `docs/decisions/016-ingress-domains-local-lite.md`。
- **调试技巧**:不想每次都改 `/etc/hosts` 也能验证 Ingress 路由对不对,直接用
  `curl -H "Host: <域名>" http://localhost/<path>` 伪造 Host 头,效果和真的
  配了 DNS 一样,不影响 Mac 系统配置。

### Keycloak start-dev 自带的 H2 是内存/临时数据库,pod 重启就把 realm 全部丢光

- **现象**:colima 停机重启一次(比如隔天再打开电脑),ArgoCD 显示所有
  Application 都是 `Synced`/`Healthy`,包括 `keycloak`,但打开 ArgoCD/Grafana
  的登录页,Keycloak 登录选项要么消失要么点了报错。用 `kcadm.sh get realms/platform`
  查发现 realm 直接不存在了。
- **原因**:`platform/apps/keycloak.yaml` 一开始为了图省事,只写了
  `args: ["start-dev"]`,没配 `database`,keycloakx chart 默认落到自带的 H2
  数据库,数据存在容器内存/临时文件系统里,pod 一重启(不管是 OOM、节点重启,
  还是简单的 `colima stop` 再 `colima start`)就清空。ArgoCD 只关心
  Deployment/StatefulSet 是不是 Ready,不知道"里面的业务数据被清空了"这种事,
  所以看着一直是 Healthy,具有很强的欺骗性。
- **处理**:改成接共享 Postgres(和 hive-metastore/mlflow 等组件一样,见
  `apps/postgres/`),`database.vendor: postgres` + `existingSecret` 指向
  `keycloak-db`(`apps/keycloak-db-init/` 负责建库建用户,模式和
  `apps/mlflow/manifests/create-db-job.yaml` 一样)。落盘之后 pod 重启不再丢数据。
- **教训**:任何"看起来只是跑个 demo"的组件,只要它自己攒了状态(realm、
  用户、配置),就不能假设临时/内存存储没关系——`ArgoCD Healthy` 只保证
  进程活着,不保证数据还在,这两者是完全不同层面的健康。

### bash 脚本用 `set -euo pipefail`,给不存在的东西 `grep` 会让脚本"悄悄卡住"

- **现象**:`scripts/03-configure-keycloak.sh` 跑到 `==> argocd client` 这一行
  之后就没有任何输出了,也不报错,脚本进程已经退出(`$?` 是 0 是因为外层套了
  `| tee`,拿到的是 `tee` 的退出码,不是脚本自己的)。
- **原因**:`existing=$(kcadm get clients ... | grep -o '"[a-f0-9-]*"' | head -1 | tr -d '"')`
  这种写法,在 client 还不存在时(全新 realm 第一次跑),`grep -o` 找不到匹配会
  返回非零。`pipefail` 让整条管道的退出码变成非零,而这个管道又是在给变量赋值
  (`existing=$(...)`),`set -e` 对"命令替换赋值"整体是否失败也会生效——直接
  终止脚本,且不打印任何错误信息,表现就是"卡在某一行不动了"。
- **处理**:在这类"找不到是正常情况,不是错误"的管道末尾加 `|| true`,把
  "没找到"和"真正的命令失败"区分开。这不是绕过错误检查,是本来就该有的
  容错——`grep` 找不到匹配本身就是预期会发生的正常分支,不该被当成脚本级别
  的致命错误。
- **教训**:`set -euo pipefail` 是好习惯,但每次写 `x=$(cmd_a | grep ... | cmd_b)`
  这种"grep 可能合理地找不到东西"的管道时,要主动想一下"找不到"算不算失败,
  算的话让它正常报错退出,不算的话显式 `|| true`,不要让它随机地看运气。

### 改 coredns-custom 加自定义域名解析,CoreDNS 直接 CrashLoopBackOff(集群 DNS 短暂中断)

- **现象**:给 `kube-system/coredns-custom` 这个 ConfigMap(k3s 官方留的自定义
  DNS 扩展点)加一段 `hosts { ... }` 配置,重启 coredns 之后整个 CoreDNS
  Deployment 起不来,一直 `CrashLoopBackOff`,期间集群里所有 DNS 解析
  (包括 `xxx.svc.cluster.local`)全部失效,是一次真正会波及全局的中断,
  不是某个业务组件自己的问题。
- **原因**:`crictl logs` 显示 `plugin/hosts: this plugin can only be used
  once per Server Block`。k3s 默认的 Corefile 主 `.:53` block 里已经有一个
  `hosts /etc/coredns/NodeHosts {...}`,我们的自定义配置又在同一个 block 里
  加了第二个 `hosts {...}`,CoreDNS 不允许这样。
- **处理**:k3s 的自定义扩展点其实有两种导入方式,效果完全不同——
  `*.override` 文件是导入到主 `.:53` block **里面**(会和已有插件冲突);
  `*.server` 文件是导入到主 block **外面**,相当于新开一个独立的 server
  block。要新增 `hosts` 这类"整个 server block 只能有一份"的插件,必须用
  `*.server`,给它配一个专门的 zone(比如 `local-lite.test:53 { hosts {...}
  fallthrough } }`),不要用 `*.override`。
- **教训**:改跟 CoreDNS/DNS 相关的集群基础设施配置,风险等级和改业务组件的
  配置不是一个量级——一旦搞错会让整个集群短暂失明(所有靠 service DNS
  互相找对方的组件都会连不上),动手前最好先本地/文档确认清楚扩展机制,
  改完立刻验证(`kubectl get pods -n kube-system -l k8s-app=kube-dns`),
  别的组件跟着重启排查之前先确认 CoreDNS 自己是不是先健康的。

### OpenMetadata 改了 OIDC 环境变量,`/api/v1/system/config/auth` 还是显示旧的 basic 认证

- **现象**:`apps/definitions/openmetadata.yaml` 里 `openmetadata.config.authentication.*`
  改成了 Keycloak OIDC 配置,ArgoCD 也确认 Synced,进容器 `crictl exec ... env`
  也能看到 `AUTHENTICATION_PROVIDER=custom-oidc` 等环境变量都是对的,但打
  `/api/v1/system/config/auth` 这个 API,返回的还是 `"provider":"basic"`、
  `"authority":"https://accounts.google.com"` 这些默认值,而且不是偶发,
  反复请求结果一样。
- **原因**:OpenMetadata 只在 `openmetadata_db` 这个数据库**第一次初始化**时,
  把 env var 算出来的认证配置写进 `openmetadata_settings` 表(`configType =
  'authenticationConfiguration'` 那一行,存的是一整块 JSON)。**之后每次
  启动都是数据库里的这份 JSON 说了算,不会再重新读 env var**。这台机器上
  `openmetadata_db` 是很早一次会话验证 basic 认证时建的,里面已经有数据,
  这次改 env var 只是改了"新建时的默认值",对已存在的数据库不生效——查
  `SELECT json FROM openmetadata_settings WHERE configType='authenticationConfiguration'`
  能直接看到数据库里存的是哪份配置,不用猜。
- **处理**:这台机器上是纯测试数据,直接 `DROP DATABASE openmetadata_db` +
  重建(**这是破坏性操作,执行前问过用户**,见 ADR 相关记录),让它从空库
  重新初始化。生产环境如果真的要改认证方式,不能这么干,需要研究 OpenMetadata
  有没有提供"强制用 env var 重新覆盖 settings 表"的 CLI/API,这次没往这个
  方向查(local-lite 阶段直接重建更快),留给以后真的要在有数据的库上切换
  认证方式时再查。
- **连带的坑**:重建空库之后,`openmetadata-ops.sh migrate`(建 OpenMetadata
  自己的表)会跑成功,但 App 主进程启动时可能报
  `relation "act_ge_property" does not exist`(Flowable 治理工作流引擎自己的
  表,不属于 OpenMetadata 自己的 schema migration 范围)导致 CrashLoopBackOff。
  实测这次重试(删 Pod 重建)后就自己好了,像是 Flowable 自己的 schema
  自动建表在第一次启动时偶发没跟上(不确定是不是这台机器内存紧张、
  swap 严重时 JVM 启动变慢导致的时序问题),不是每次都复现,遇到先重启
  一次 Pod 看看,不用一开始就当成需要深入排查的硬故障。

### 组件重新拉起来报 "password authentication failed",Postgres 密码"变了"

- **现象**:一个之前验证过、收进 `pending-definitions` 又重新启用的组件
  (这次是 OpenMetadata 和 MLflow,各出现一次),报
  `FATAL: password authentication failed for user "xxx"`,但对应的 Secret
  (比如 `mlflow-db-secret`)看起来内容正常,没有被意外改过。
- **原因**:各组件的 `create-db-job.yaml` 都是"角色不存在才创建"
  (`SELECT ... FROM pg_roles ... || CREATE USER ...`),不会更新已存在角色的
  密码。这台共享 Postgres 从很早的会话开始就一直在跑、数据一直没清过,
  Postgres 里的用户角色早就存在了,是用**当时**Secret 里的密码创建的;如果
  那个组件后来因为任何原因(重新生成过 Secret、手动改过、或者这次同一类
  bug)导致 Secret 里的密码值和 Postgres 里实际存的密码不一致,新 Pod 拿
  **当前** Secret 的密码去连接,自然连不上——这不是 Postgres 或者组件本身
  的 bug,是"创建型"幂等脚本天然覆盖不到"密码漂移"这种情况。
- **处理**:确认是这个原因后(报错信息里的用户名对得上,Secret 内容看着
  正常),直接把 Postgres 里那个角色的密码改成和当前 Secret 一致:
  ```sql
  ALTER USER <角色名> WITH PASSWORD '<Secret 里当前的密码>';
  ```
  不用碰 Secret,也不用重建数据库(除非像 OpenMetadata 那次一样,问题根本
  不是密码,是数据库里存的应用设置本身就是旧的)。
- **教训**:这次在 OpenMetadata 和 MLflow 上各踩了一次,同一个原因、同一个
  修法——凡是"组件在 pending-definitions 和 apps/definitions 之间来回搬动、
  但共享 Postgres 从不重置"这种场景,都要预期可能撞上这个问题,不是特例。

### Alloy 采不到日志:`loki.source.kubernetes` 拉不到数据,换成 hostPath 又报 "no such file or directory"

- **背景**:见 ADR-020。装 Loki + Grafana Alloy 做集中日志采集时连续踩了两个坑。
- **坑一**:官方更推荐的 `loki.source.kubernetes` 组件一条数据都拉不到,报
  `Internal Privoxy Error`——这是本机代理拦截问题(见上面那条记录),不是
  Alloy 配置错误。处理:改用 `discovery.kubernetes`(只拿 pod 元数据做
  relabel)+ `local.file_match` + `loki.source.file`,日志内容直接从
  hostPath 挂载的宿主机文件读,不经过 K8s API 的 `containerLogs` 接口。
- **坑二**:换成 hostPath 之后,虽然 `/var/log/pods/<ns>_<pod>_<uid>/<container>/0.log`
  这个路径本身是存在的,但 Alloy 报 `stat ...: no such file or directory`。
  用 `crictl exec ... ls -la` 进容器实际查看才发现:这个 `0.log` 是个
  **符号链接**,指向 `/var/lib/docker/containers/<容器id>/<容器id>-json.log`——
  colima 这个 profile 的容器运行时是 docker(用 docker 的 json-file 日志
  驱动写日志),不是纯 containerd 直接写文件。只挂了 `/var/log` 的话,这条
  符号链接在容器里指向一个够不着的路径,`stat` 自然失败。处理:Alloy chart
  的 `alloy.mounts.dockercontainers: true` 把 `/var/lib/docker/containers`
  也挂进去,两个 mount 都要开(`varlog` + `dockercontainers`),缺一个都不行。
- **教训**:这类"文件路径存在,但读不到内容"的问题,先用 `crictl exec ...
  ls -la <path>` 进容器实际看一眼(不要只看宿主机上文件存不存在),符号
  链接指向哪里一目了然,比看 chart 文档去猜"是不是该开某个 mount 开关"
  快得多。

### CRD 太大报 "annotations too long",`ServerSideApply=true` 不是每次都管用

- **现象**:`CustomResourceDefinition.apiextensions.k8s.io "xxx" is invalid:
  metadata.annotations: Too long: may not be more than 262144 bytes`。这
  个仓库里至少踩到过三次,都是同一个根因:CRD 内嵌的 OpenAPI schema 太大,
  ArgoCD 默认走 client-side apply 会把整份 manifest 写进
  `kubectl.kubernetes.io/last-applied-configuration` 这个注解,超过 k8s
  单个注解 262144 字节的硬限制。
  - **kube-prometheus-stack**(`prometheuses.monitoring.coreos.com` 等):
    加了 `ServerSideApply=true` **没用**,还是报一样的错("具体是 ArgoCD
    内部哪个环节导致的还没深究" ——见本文件靠前的那一条)。
  - **KServe**(`inferenceservices.serving.kserve.io`,ADR-027):加
    `ServerSideApply=true` **有用**,官方文档也推荐这么做,这是唯一一次
    这个选项真的解决了问题。
  - **CloudNativePG**(`clusters.postgresql.cnpg.io` /
    `poolers.postgresql.cnpg.io`,ADR-038):加了 `ServerSideApply=true`
    **没用**,还是一样的错。
- **结论**:`ServerSideApply=true` 值得先试(免费、不会有副作用),但**不能
  假设它总能解决这类问题**——三次里只有一次真的管用,具体是 ArgoCD 处理
  Helm chart `crds/` 目录这条路径本身不完全遵守这个 sync option、还是
  CRD 大小已经超出 server-side apply 本身能处理的上限,这几次都没有深挖,
  经验上也分不出规律(比如猜"越大的 CRD 越容易失败"目前看不出来,KServe
  和 CNPG 的 CRD 大小同一个量级)。
- **实际总是有效的处理**:把 CRD 从 Helm/ArgoCD 的管理范围里摘出去——
  chart 的 `crds.enabled: false`(kube-prometheus-stack)或
  `crds.create: false`(CloudNativePG),用一次性脚本
  `kubectl apply --server-side --force-conflicts` 直接装(KServe 的
  ClusterServingRuntime 走的是同一个"GitOps 这条路走不通,退回一次性
  脚本"模式,虽然那个不是 annotations-too-long 这个具体问题,但处理思路
  一样)。涉及脚本:`scripts/04-install-kube-prometheus-crds.sh`、
  `scripts/16-install-cloudnative-pg-crds.sh`。
- **教训**:遇到这个报错,先加 `ServerSideApply=true` 试一下,但**不要
  卡在"为什么这个选项不管用"上深挖太久**——三次里两次都是不管用的,
  与其排查 ArgoCD 内部机制,不如直接跳到"摘出 Helm 管理范围、走一次性
  脚本"这个总是有效的方案,省时间。

### `apt-get install` 卡死不动,`Acquire::Retries` 不管用:apt 自己的 "delayed item" 重试队列是另一套机制

- **现象**:`apt-get install` 挂着不动,`crictl exec` 进容器用
  `/proc/*/cmdline` 看,进程还在 `/usr/lib/apt/methods/http` 这个子进程
  里,和"CRD 太大"、"kubectl logs 报 Privoxy Error"这几条不是同一类
  问题——已经加了 `Acquire::http::Timeout`/`Acquire::Retries` 这几个选项
  (见 `apps/iam-sync/manifests/cronjob.yaml` 的教训),理论上应该会按
  重试次数失败退出,但实际上不会。
- **原因**:用 `kubectl run --rm -i` 单独复现、把完整输出重定向落地(不要
  用 `-qq`,或者至少留一份不加 `-qq` 的输出用于排查)才能看清楚:apt 在
  某个具体包下载失败之后,会把它放进一个叫 "delayed item" 的内部重试
  队列,一直刷 `W: Tried to start delayed item <包名> ..., but failed`,
  这个循环**不受 `Acquire::Retries` 这个参数约束**——它是 apt pipelining/
  并行下载机制里一个独立的子系统,和 `Acquire::http::Timeout`(单个 HTTP
  请求的空闲超时)、`Acquire::Retries`(单个 URI 的重试次数)都不是一回事,
  加再多这两个选项也管不到这个队列。哪个具体包会触发这个问题看起来是
  偶发的(这次是 `perl`),不是固定复现某一个包,大概率和当时那个包的
  连接/传输状态有关,不值得深究"为什么偏偏是这个包"。
- **处理**:不追究 apt 内部这个机制的细节,直接在外面套一层
  `timeout -k 10 N`(比如 `timeout -k 10 90 apt-get ... install ...`),
  配合脚本本身的 `set -e`,让整个命令在合理时间内快速失败退出,而不是
  无限期挂着。**`-k`(kill-after)这个参数不能省**——`timeout` 默认超时
  后只发一次 SIGTERM,如果目标进程不响应(实测确认过一次:加了单纯
  `timeout 90` 之后,同一类卡死还是撑过了 90 秒没退出,没深究是不是
  apt/dpkg 本身在忽略 SIGTERM),`timeout` 不会自动升级成 SIGKILL,等于
  没起作用;加 `-k 10` 之后,SIGTERM 发出 10 秒还没死就强制 SIGKILL,
  保证一定能退出。这样一次性 Job/CronJob 的正常重试机制(`backoffLimit`)
  才有机会在 `activeDeadlineSeconds` 这个大限之内真正多试几次,而不是
  一次尝试就把整个时间窗口耗光在一个注定要失败的包上。
- **教训**:这类"看着像网络卡住,加了标准的 HTTP 超时选项却没用"的情况,
  不要预设是网络层的问题、也不要预设是自己漏配了哪个 apt 选项——先把
  完整、不省略的日志/进程状态拿到手(`crictl exec` 直接看 `/proc`,或者
  临时去掉 `-qq`/加输出重定向到文件),再判断问题到底出在哪一层。这次
  一开始猜错了方向(以为是 initContainer 没配代理),多花了一轮才找到
  真正原因。

### 推倒重建集群之后,ArgoCD/Trino/Superset/OpenMetadata/MLflow 这类做 OIDC discovery 的组件全部连超时

- **背景**:ADR-039,真的删掉本机 colima VM 重建一次才第一次暴露。
- **现象**:`curl http://keycloak.local-lite.test/...` 之类的请求 5 秒
  超时(`Connection timed out`),不是报错,是单纯连不上——但 DNS 解析
  本身"成功"了,能拿到一个 IP。
- **原因**:`platform/coredns-custom/` 把 `*.local-lite.test` 硬编码指向
  `ingress-nginx-controller` 这个 Service 的 ClusterIP 字面量。ClusterIP
  是集群创建时按 Service CIDR 分配的,不是固定不变的值——重建集群后
  `ingress-nginx-controller` 分到了一个新的 ClusterIP,旧的硬编码值就是
  一个查得到但完全连不上的废 IP。
- **处理**:改用 CoreDNS 的 `rewrite name regex ... answer auto` 把查询
  重写成 `ingress-nginx-controller.ingress-nginx.svc.cluster.local`,交给
  同一个 server block 里的 `kubernetes` 插件动态解析,不管 ClusterIP
  怎么变、集群重建几次都不用再改这份配置。
- **教训**:任何写死 ClusterIP 字面量的配置都是定时炸弹,只是触发条件
  (集群重建/该 Service 被删重建)平时很少见——`kubectl get svc` 之类的
  命令确认过"IP 对不对"不代表这份配置本身是对的,要看它是不是把 IP
  写死进了另一份配置文件里。

### 同一个命名空间里的 Job 连不上同命名空间的 pod,NetworkPolicy 报 "connection refused"

- **背景**:ADR-039,推倒重建集群时暴露。MinIO chart 的
  `buckets:` 声明式配置靠一个 Helm post-install hook Job(`minio-post-job`)
  执行 `mc mb` 建 bucket,这个 Job 建在 `minio` 命名空间里,要连同一个
  命名空间里的 `minio` pod。
- **现象**:`mc: <ERROR> Unable to initialize new alias ...: connect:
  connection refused`,不是超时(NetworkPolicy 挡掉的连接通常表现为
  `connection refused`,不是 `timeout`——这个特征在这次和上一条 DNS 问题
  之间是一个有用的区分信号)。
- **原因**:`platform/network-policies/manifests/minio.yaml` 里
  `allow-consumers-to-minio` 的允许来源列表只列了外部消费命名空间
  (`data`/`spark-operator`/`mlflow`/`trino`/`airflow`/`seatunnel`),漏了
  `minio` 自己这个命名空间——默认 `default-deny-ingress` 把同命名空间的
  流量也一起挡了。之前没暴露是因为这个 Job 是 Helm 的 `post-install`
  (不是 `post-upgrade`)hook,只在最初第一次装的时候跑,而那时候这条
  NetworkPolicy 还没加上去。
- **处理**:把 `minio` 自己也加进允许来源的 `namespaceSelector` 列表。
- **教训**:写 NetworkPolicy 的允许来源列表时,不要只想"谁会从**别的**
  命名空间连进来",同一个命名空间里如果有 Job/CronJob 之类的东西要连
  该命名空间自己的其他 pod(常见于 Helm chart 自带的 post-install/
  post-upgrade hook),也要把自己的命名空间加进允许列表——这类同命名空间
  自连的需求容易被忽略,因为直觉上会觉得"同一个命名空间应该默认互通",
  但 `default-deny-ingress` 一旦生效,这个直觉是错的。

### `kubectl delete pod` 删 CNPG 的 Postgres pod 卡在 `Terminating` 十几分钟不退出

- **背景**:ADR-041,给 Postgres 加 `priorityClassName` 之后,想通过
  删 pod 触发重建来让字段生效,结果卡住了。
- **现象**:`kubectl delete pod postgres-cnpg-1 -n data` 之后,pod 一直
  停在 `Terminating`,`kubectl get events` 能看到 17 分钟前就有
  `Killing: Stopping container postgres` 这条事件,但迟迟没有真正终止。
  `crictl logs` 进去看,postgres 进程本身已经在响应停止信号(持续拒绝
  新连接,报 `FATAL: the database system is shutting down`),不是进程
  完全没反应,只是没有真正完成关闭流程。
- **原因**:没有深挖到底(不确定是 CNPG 自己的 shutdown 钩子卡住,还是
  这台机器磁盘 I/O 慢导致 checkpoint flush 慢),但确认了一个关键背景:
  CNPG 默认的 `terminationGracePeriodSeconds` 是 **1800 秒(30 分钟)**
  ——`kubectl delete pod`(不加 `--force`)默认会一直等到这个宽限期
  结束才会强制杀掉,这台机器上单实例、数据量很小的 Postgres 实测都能
  卡这么久,不是配置错误导致的异常长等待,是这个默认值本来就很宽松。
- **处理**:`kubectl delete pod <name> -n <ns> --grace-period=0 --force`
  跳过优雅关闭直接强杀。Postgres 自身的 WAL 崩溃恢复机制是为这种场景
  设计的,不是赌运气——实测这台机器上新 pod 23 秒就变成 `1/1 Ready`、
  Cluster 状态回到 healthy,真实数据(`keycloak.user_entity` 表的行数)
  核对过和崩溃前一致,没有丢失或损坏。全程也确认了下游组件
  (Keycloak/Hive Metastore)的 pod 重启次数在这次操作前后没有变化,
  说明它们的连接重试机制扛住了这段 Postgres 不可用的窗口,没有级联
  故障。
- **教训**:CNPG(或者任何设了很长 `terminationGracePeriodSeconds` 的
  有状态组件)卡在 `Terminating` 不一定是真的出问题了,先查一下这个
  字段的值,别死等——但也别一遇到"卡住"就本能地强杀,Postgres 这类
  数据库能这么干是因为它有崩溃恢复机制托底,是这次先确认了这一点才
  敢这么做,不是所有卡在 `Terminating` 的组件都能安全地这样处理。

### Airflow scheduler 反复长出两个并存 ReplicaSet、ArgoCD 子 Application spec 一度没跟上 git——根因是 ArgoCD 控制面自己被 OOMKilled,不是 Airflow chart 的 bug

- **现象**(2026-08-14,Feast 集成那一轮资源紧张期间发现):`airflow-scheduler`
  这个 Deployment 反复出现两个并存的 ReplicaSet(都 `DESIRED=1`,CPU
  request 叠加顶到 99%,手动 `kubectl scale --replicas=0` 也会被立刻纠正
  回来)。同一时期还发现子 Application 显式触发 sync、`status.sync.status`
  显示 `Synced`,但 `.spec.source` 没有真的更新成最新 git 内容,要
  `kubectl replace -f` 整个替换才生效——比这份文档"git push 之后 ArgoCD
  迟迟不应用新配置"那条(见上面)更严重一层,这次连 ArgoCD **自己的**
  Application 对象本身都没跟上。
- **排查过程**:先怀疑是 Helm chart 渲染非确定性(`helm template` 同一份
  chart+values 本地连续渲染 8 次,逐字节 diff,**完全没有发现任何差异**
  ——排除了这个假设,不是 chart 的锅)。转向检查 ArgoCD 控制面本身状态:
  `kubectl get pod argocd-application-controller-0 -o jsonpath=
  '{.status.containerStatuses[0].lastState}'` 显示 `reason: OOMKilled,
  exitCode: 137`——**真实发生过**,不是猜测。更关键的是,即使在"相对安静"
  (Airflow/Trino 都已经 park 掉)的状态下,`kubectl top pod` 实测
  controller 常驻内存高达 **1814Mi**,已经是当时 2048Mi 限制的 88.6%
  ——这个组件本身常驻基线就已经很接近上限,`argocd-repo-server` 同样
  查到过 OOMKilled 记录(`lastState.reason` 是 `OOMKilled`,即使
  `exitCode` 显示 0——k8s 对这类情况的上报本身不总是一致,不能只信
  `exitCode` 字段),但它的 limits 一直只有 512Mi,且从未有实测数据支撑
  过这个数字(纯粹是"先给个安全下限")。
- **结论**:两个现象(Airflow ReplicaSet churn、子 Application spec
  drift)大概率是**同一个根因的两种表现**——ArgoCD 控制面(controller +
  repo-server)在这台机器 25+ 个 Application 的真实规模下持续吃紧,批量
  sync/渲染大 chart(Airflow 官方 chart 不小)时的内存峰值远超之前配的
  上限,被自己的资源限制误杀,导致 in-flight 的状态更新/渲染被中断,
  表现出各种"不一致"的症状,不是某个具体组件的 bug。
- **处理**:`platform/bootstrap/argocd-values.yaml` 里 controller 的
  limits 从 2048Mi 调到 3072Mi、repoServer 从 512Mi 调到 1024Mi(只调
  limits,不调 requests——这台机器整体内存基线已经在 86% 上下,大幅调高
  requests 会挤占其他组件的可调度余量,风险更大)。改完用
  `NEEDS_LOCAL_PROXY=1 ./scripts/01-bootstrap-argocd.sh` 重新
  `helm upgrade`(**不要手动裸跑 `helm upgrade` 命令,容易漏带这台机器
  必需的代理 overlay 参数——这次排查过程中就真的漏带过一次,虽然后来
  验证发现即使没有代理这台机器当时也能连上 GitHub,但那只是巧合,不能
  当成可以跳过这个参数的依据,一切以脚本记录的标准流程为准**)。升级后
  验证过:所有 Application 仍是 `Synced/Healthy`,repo-server 新 pod 强制
  hard-refresh 后能正确同步到最新 git commit,代理 env 也确认还在。
- **没有解决的部分**:这只是把 ArgoCD 自己"被自己的资源上限误杀"这个
  问题缓解了,**不是把这台机器物理内存不够的结构性问题解决了**——colima
  当时 11GB 的空载基线就已经 86%+,以后如果同时启用的重量级组件更多
  (比如 Feast+Airflow+Trino 三个一起验证的场景就真实撞过一次控制面
  OOM),这两个数字大概率还要继续往上调。**后续更新**:2026-08-14 当天
  colima 从 11G/4vCPU 扩到 13G/6vCPU,`feast_materialize` 这个 DAG 最终
  在稳定资源下跑出了成功记录,完整的排查链路(一共 9 个独立的坑,资源
  抢占只是其中一个)见 [ADR-042](../decisions/042-feast-feature-store.md)
  "2026-08-14:端到端验证真正跑通"那一节。

### `KubernetesPodOperator` 拉起跨命名空间/自定义镜像的 Spark 任务,一路要闯好几关(RBAC、日志流、容器 UID、模板变量)

- **背景**(2026-08-14,`feast_materialize` DAG 排查过程完整记录,见
  [ADR-042](../decisions/042-feast-feature-store.md)):这是这个平台第一次
  用 `KubernetesPodOperator` 在 Airflow 自己的命名空间之外(`namespace=
  "feast"`)拉起一个跑 Spark 的自定义镜像任务,一路暴露了好几个**通用的、
  以后任何同类 DAG 都可能重新撞上**的坑,不是 Feast 独有:
  1. **跨命名空间要单独建 RBAC**:Airflow chart 默认的
     `airflow-pod-launcher-role` 是 `Role`(命名空间级),不是
     `ClusterRole`,没开 `multiNamespaceMode` 的话,目标 pod 建在别的
     命名空间会报 `403 Forbidden: cannot list pods`。按最小权限原则,在
     目标命名空间单独建一份同权限的 `Role`+`RoleBinding`,不要图省事开
     `multiNamespaceMode`(会变成集群级 `ClusterRole`)。
  2. **`get_logs=True` 在这台机器上会把任务拖垮**:内部读日志流走 kubelet
     `containerLogs`,被本机代理软件拦截(见上面"Internal Privoxy Error"
     那条),反复 `ApiException(500)` 重试两分半后放弃、直接把还在正常跑
     的 pod 删掉判定失败。关掉 `get_logs`,靠 Loki/Alloy 兜底日志,
     operator 只轮询 pod phase(走 K8s API,不受影响)。
  3. **容器用"任意 UID"镜像(如 `USER 1001`,`/etc/passwd` 没有对应
     条目)时,Spark/Hadoop 启动会崩**:`UserGroupInformation` 走 JVM 的
     `UnixLoginModule` 查用户名查不到,直接抛
     `KerberosAuthException: ... invalid null input: name`,表现成
     `JAVA_GATEWAY_EXITED`。`HADOOP_USER_NAME` 环境变量不够用(它在
     `UnixLoginModule` 崩溃点之后才生效),要么用
     `security_context: run_as_user: 0` 跑 root(local-lite 阶段的务实
     选择),要么重新 build 镜像在 entrypoint 里给这个 UID 补一条
     `/etc/passwd`(更干净但要改镜像)。
  4. **`{{ ts }}`/`{{ ds }}` 这类基于调度时间的 Jinja 宏,在
     `schedule=None`、手动触发的 DAG Run 上是未定义的**
     (`UndefinedError: 'ts' is undefined`)——没有 `data_interval`,不是
     Airflow 3.x 废弃了这些宏(带 schedule 的 DAG 上还能正常用)。需要
     "当前时间"的场景,改成在 shell 命令里直接取(比如
     `$(date -u +%Y-%m-%dT%H:%M:%S)`),不要依赖这类宏。
- 同一次排查里还有两条不算通用、但值得记一笔的坑:K8s ConfigMap 卷整目录
  挂载会把 `..data` 软链背后带时间戳的隐藏目录名泄漏进 Python 的
  relative import 路径(和 Airflow 自己挂 DAG 目录踩过的坑同一个原因,
  解法一样——用 `subPath` 分别挂单个文件);Feast 的 S3 registry 走
  boto3,认标准 `AWS_*` 环境变量,不是给 Spark 用的
  `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`(Hadoop S3A 客户端专用),两套
  凭据变量名要分别配。

### 自建的 `python:3.12-slim` 薄应用 pip install 反复 exit 124,但 `curl` 从其他 pod 测同一个网络明明是通的

- **现象**:`table-registration-app` 这个自建薄应用(ConfigMap 挂源码 +
  `pip install` 启动)反复 `CrashLoopBackoff`,`kubectl describe pod` 显示
  `exitCode: 124`(`timeout` 命令自己触发的,不是应用代码报错)。从另一个
  pod 直接 `curl` `pypi.org`/`deb.debian.org` 都能秒回 200,说明这台机器
  当时的网络本身是通的,不是"完全连不上"这种直观的网络故障。
- **原因**:这个 Deployment 的 `env` 里完全没有配 `HTTP_PROXY`/
  `HTTPS_PROXY`——`apps/iam-sync/manifests/cronjob.yaml` 早就因为同一类
  问题配了这两个变量(colima 虚拟网络直连外网不稳定,需要走宿主机代理),
  但后来新建的 `table-registration-app`/`permission-request-app` 这两个
  自建应用的 Deployment 漏配了,当时想当然地认为"这个组件只用 pip 不用
  git/apt-get,应该不会踩到同一个坑"——这个假设是错的,pip 连 PyPI 一样
  会受这台机器网络不稳定的影响,不是只有 apt-get/git 会。
  `permission-request-app` 当时侥幸第一次就装成功了,只是运气好,不代表
  它没有同样的隐患。
- **处理**:两个 Deployment 都补上和 `iam-sync` 一致的
  `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` 三个环境变量(地址来自
  `colima ssh -- env | grep -i proxy`)。以后新建任何"`python:3.12-slim`
  镜像 + 启动时 `pip install`/`apt-get install`"这个模式的自建组件,直接
  照抄这三个环境变量,不要假设"这次不用 apt-get/git 应该没事"——判断
  错过一次,不要再错第二次。
- **排查方法留档**:怀疑这类"网络间歇性故障"时,先用一个独立的、干净的
  pod(比如 `curlimages/curl`)直接测目标地址通不通,能排除"网络整体故障"
  和"这个 Deployment 自己的网络配置缺失"这两种可能,不要一上来就怀疑
  DNS 或者代理软件本身出了新问题。

### 新建的 Job/CronJob 的 pod 刚起来第一次连接直接 Connection refused,但同样标签的 pod 手动测试是通的

- **现象**:`permission-request-app-escalation` 这个 CronJob 手动触发一次
  Job,容器里的 `curl` 直接报 `Connection refused`(exit 7)。但用一个
  手动 `kubectl apply` 建的、带同样标签的 pod 测试同一个地址,连接完全
  正常。NetworkPolicy 规则本身核对过是对的(`kubectl get networkpolicy
  -o yaml` 确认 podSelector/标签都匹配)。
- **原因**:NetworkPolicy 是靠 CNI 在这台机器上写底层规则(iptables 之类)
  实现的,一个全新 pod 刚被调度、拿到 IP 之后,CNI 把对应的规则写好需要
  几秒钟——Job 的容器命令是"起来立刻执行",没有像手动测试那样天然多出
  几秒钟的间隔(先等 pod Running,再单独 exec 进去跑命令),所以精确踩中
  了这个规则生效前的窗口期,第一次连接必然失败。这不是 NetworkPolicy
  配错,是一个真实的、跟这台机器 CNI 实现相关的时序竞态。
- **处理**:Job 里但凡要连接受 NetworkPolicy 保护的目标,`curl` 要显式加
  `--retry-connrefused`(普通 `--retry` 默认不重试"连接被拒"这种,只重试
  超时/5xx 这类)。这是比"在命令前面加个 `sleep N`"更靠谱的写法——重试
  次数和间隔是可预期的,不用去猜"到底要等几秒才够"。

### 给 ConfigMap 新增一个 key 之后,subPath 挂载这个新 key 的文件在 pod 里变成了一个空目录

- **现象**(2026-08-15,新增 `apps/airflow/dags/dbt_demo.py` 这个 DAG 时
  撞到):ConfigMap 里确实有这个新 key(`kubectl get configmap ... -o
  jsonpath='{.data}'` 能看到),`extraVolumeMounts` 里 `subPath:
  dbt_demo.py` 这一条配置本身也没写错,但 pod 起来之后
  `/opt/airflow/dags/dbt_demo.py` 是一个空目录(`drwxrwsrwx`),不是文件,
  同一批挂载的另外两个已经存在很久的文件(`feast_materialize.py`/
  `seatunnel_device_events.py`)都正常。`airflow dags list-import-errors`
  不会报这个错(它没有文件可解析,不是"解析失败",是"根本没看到这个
  文件")。
- **原因**:`subPath` 挂载不走 K8s ConfigMap 卷常见的"`..data` 软链定期
  刷新"机制(这个项目已经在别处吃过"改了 ConfigMap 要等 ~1 分钟才生效"的
  亏,但那条讲的是整目录挂载),`subPath` 是 pod **创建那一刻**去 ConfigMap
  里找对应 key、直接投影成一个文件——如果 pod 创建的那个时间点,底层
  ConfigMap 的新版本还没有完全传播到这台节点的 kubelet(改 ConfigMap
  和重启依赖它的 Deployment 这两个操作之间没有强制的先后等待,很容易
  连续执行时刚好撞上这个窗口),kubelet 找不到这个 key,不会让 pod 起不来
  报错,而是**默默地建一个空目录**占位。这个空目录建立之后,`subPath`
  挂载本身也不会像整目录挂载那样后续自动修复/更新——即使 ConfigMap
  之后确实同步好了,这个 pod 里那个位置永远是空目录,直到这个 pod 被
  重新创建。
- **处理**:改 ConfigMap 之后如果重启依赖它的 Deployment 碰到这种"文件
  变目录"的情况,不要怀疑 ConfigMap 内容本身(先用 `kubectl get
  configmap ... -o jsonpath='{.data}'` 确认 key 真的在),大概率是重启
  时机踩早了——再等几十秒到一分钟,重新 `kubectl rollout restart` 一次
  就好。用 `kubectl exec ... -- ls -la <挂载路径>` 检查是文件还是目录,
  是最快的确认方式,不用去猜是不是代码/配置写错了。

### OpenMetadata SSO 登录报 "Account already exists. Please contact administrator."——不是配置问题,是数据库里一条半损坏的用户记录

- **现象**:`apps/definitions/openmetadata.yaml` 里 Keycloak OIDC 配置
  从最初部署起就是对的(`authentication.provider: custom-oidc`,`
  jwtPrincipalClaims: [email, preferred_username, sub]`),但用 Keycloak
  的 `admin` 账号登录 OpenMetadata,页面报 "Account already exists.
  Please contact administrator.",换一个全新的 Keycloak 用户名登录完全
  正常。
- **排查过程中一个容易踩的坑**:重试登录时看着换了浏览器操作,但只要
  Keycloak 自己的 SSO session cookie(或者 OpenMetadata 自己的服务端
  session)还活着,点"重新登录"实际上是在复用旧 session,不会真的重新
  走一遍 OIDC 授权流程,会看到看似矛盾的结果(比如报另一个
  "Session not active"/"invalid_grant" 错误)。要排查这类问题,必须先
  显式访问 Keycloak 的 `/protocol/openid-connect/logout` **和**
  OpenMetadata 自己的 `/logout`,两边都清干净,再重新走一遍完整登录,
  不能只清浏览器 cookie(有的 session cookie 是 `HttpOnly`,JS `document.
  cookie` 清不掉)。
- **原因**:直接查 Postgres 的 `user_entity` 表(`SELECT json FROM
  user_entity WHERE name='admin'`)发现这条记录的 `authenticationMechanism`
  是空的,`updatedAt` 精确对应到某次早先的、不走正常浏览器 OIDC 流程的
  交互(比如直接拿 token 调 API 做验证测试)——这类操作会触发 OpenMetadata
  的用户自动创建/更新逻辑,但走的不是完整的 OIDC code flow,留下一条
  "创建了但没有正常关联认证方式"的半成品用户记录。之后任何人用同一个
  用户名(这里是 `admin`)走正常 OIDC 登录,OpenMetadata 发现这个用户名
  已经存在但认证方式对不上,判定为"身份冲突",拒绝登录——这是它自己的
  防呆机制,不是 bug,但触发条件是这条脏数据,不是配置本身有问题。
  `entity_relationship` 表里确认这条记录没有被任何其它实体引用
  (`fromid`/`toid` 查询 0 条),证明它只是一条孤立的半成品,不是承载了
  真实数据的账号。
- **处理**:直接从 `user_entity` 表删掉这条记录(先确认
  `entity_relationship` 里没有引用,再删,别对着一个可能有真实数据挂在
  上面的账号做这个操作),然后完整走一遍登出+重新登录,OpenMetadata
  会用干净的状态重新创建这个用户,`isAdmin: true` 正确、`email` 和
  Keycloak 里的 claim 完全对上。全程没有改任何 `apps/definitions/
  openmetadata.yaml` 里的配置——这是数据层面的一次性清理,不是需要同步
  进"一键部署"代码的修复,真正全新的部署从第一次登录起就不会有这条脏
  数据,不会重现这个问题。
- **教训**:验证 SSO/OIDC 集成时,只用真实浏览器走完整的 code flow 测试
  (这个项目已经建立的 curl+cookie-jar 或者真实浏览器测试habit是对的),
  避免用"直接拿 token 调 API"这类走捷径的验证方式碰触用户身份这一层——
  这类捷径可能会在应用自己的用户表里留下不完整的记录,虽然不影响当时
  测试本身"看起来通过",但会在后续正常登录时以完全不相关的报错形式
  冒出来,排查成本比老老实实走一遍完整浏览器流程高得多。
