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
       两边都满足。现在 SSO 登录本身已验证通过,**但登录后调 API 还是
       403**(RBAC 层,已单独立项跟踪)
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

这次拉起 P1.2/1.3/1.4 全靠人工 `git mv` + 逐个手动排查 Keycloak
client/Secret/scope 缺口完成的。过程中暴露的好几个 bug(client 已存在但
Secret 永远补不上、groups scope 从建realm起就没配过)本可以在"改配置就
重新拉起"这套机制的约束下被更早、更结构性地测试出来,现在只能靠"当天
有没有人手动测登录"这种运气发现。

做完这一批,以后再拉起/重建任何组件,应该默认带着"这次会不会又是同一类
配置漂移"的怀疑,而不是假设"Pod Running 就是好的"。

**其它排队里的事**(不阻塞,按 `docs/BACKLOG.md` P1.5/1.6/1.7 的顺序):
Argo Workflows RBAC(已单独立项)、Kafka 部署、算法链路端到端重新验证。

## 正在运行的后台任务

**没有。**

- cloud-full 云主机(`i-0jlbped4h1959tp591pe`)**本次会话结束前会停机**,
  用 `scripts/26-stop-cloud-vm-economical.sh`,经济模式
  (`StoppedMode=StopCharging`),停机期间不产生计算费用。
- 重新开机后 SSH 隧道要重新建(公网 IP 不是固定 EIP,这次会话期间就
  变过两次),命令见 `environments/cloud-full/STATUS.md`。
- 本机 colima 上的重量级组件处于 park 状态。

## 已知的、还没解决的事(不要重新排查一遍)

- **Argo Workflows 登录后调 API 返回 403**——SSO 本身已修好并验证(真实
  Bearer token、落地 SPA),这是 RBAC 授权层的独立问题,已单独立项跟踪
  (`server.sso.rbac` 需要的 K8s RBAC 绑定没配)。
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
- **算法/大数据链路的组件级验证 ≠ 端到端链路验证**:JupyterHub/MLflow/
  Spark Operator/Feast/Argo Workflows 都已部署验证,但"notebook → Feast
  特征 → Argo Workflows 训练 → MLflow 记录"这条完整链路本身还没有重新
  连起来跑一次。

## 结束一段工作前必须确认(照着过一遍,不要跳)

- [ ] `git status` 干净,该 push 的都 push 了
- [ ] 计费资源现在的状态说清楚了(开着/停了,为什么)
- [ ] 后台任务/SSH 隧道是不是还开着,写进了上面那节
- [ ] 这次做的事,哪些是真实验证过的、哪些只是写完代码没测,分层说清楚
- [ ] 有没有手工改过集群但没回写 git 的操作(有的话赶紧记下来或者补写)
- [ ] 失败但没解决的事情,写清楚现象+已经排除的原因,别人接手不用重新排查一遍
- [ ] **能力有增减的话,`docs/roles.md` 更新了吗**(新增的一条,ADR-057)
