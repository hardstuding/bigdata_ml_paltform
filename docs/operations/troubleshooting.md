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

### 某些镜像仓库(如 quay.io)在这个网络下连不上,但 docker hub 是通的

- **现象**:Pod 一直 `ImagePullBackOff`,事件里是 `TLS handshake timeout` 或下载到一半 `EOF`。`colima ssh` 里直接 `curl` 目标 registry 也是 `Connection timed out`,换成 `https://registry-1.docker.io/v2/` 测试却能正常返回(401 也算通,那是正常的匿名访问被拒)。
- **原因**:本机代理对不同站点的连通性不一致,quay.io 这类站点即使走代理也可能连不上,不是配置错误,是这条网络对特定站点没有稳定路由。
- **处理**:很多镜像(包括 MinIO)官方会同时发布到 docker hub 和 quay.io,遇到这种情况直接把 chart/manifest 里的 `image.repository` 换成 docker hub 上的同名镜像,不用死磕代理配置。以后新组件如果也卡在拉镜像,先用 `colima ssh -- curl` 测一下目标 registry 通不通,别默认怀疑是 k8s 配置问题。

### ArgoCD 接 Keycloak OIDC,登录跳转到集群内部域名,浏览器打不开

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
