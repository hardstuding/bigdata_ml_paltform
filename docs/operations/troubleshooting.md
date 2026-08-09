# 常见问题排查

> 施工过程中遇到的真实问题按时间顺序往这里加,格式:现象 → 原因 → 处理方式。这份文档主要给未来的 AI Agent 和人类共同排障用,记录要具体(报错信息、命令、涉及的组件版本)。

## 索引

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
