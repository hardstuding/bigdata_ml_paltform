# prod 环境画像

## 目标硬件(2026-08-14 用户确认:之前说的"5 台"是记错了,以这份为准)

- 节点数:**6**
- 单节点:256GB 内存(具体 CPU 核数还没有记录过,建议确认;这类内存
  规模的服务器一般配 64-96 核,下面的资源分配先按 64 核估,拿到真实
  数字后按比例调整就行)
- 单节点存储:约 50TB
- 集群总量:约 1.5TB 内存 / 300TB 存储

这是现有(旧)生产 CDH 集群的规格(YARN/Spark/Flink/Maxwell/Flume
混跑),**不代表这台硬件会原样腾给新平台用**——这份模板假设的是"新
平台在同等量级的独立硬件上落地",不是"直接抢旧集群正在用的资源"。
真正上生产前,这个假设需要和用户当面确认一次(是全新采购的硬件,还是
旧集群退役腾出来的,两种情况下"能不能现在就开始规划迁移窗口"这个
时间线不一样)。

## 和 cloud-full 的关键差异,不只是"资源更多"

参考 [`environments/cloud-full/README.md`](../cloud-full/README.md)
先把组件资源规划过一遍——prod 在那个基础上,还有几件 cloud-full 不需要
考虑、但 prod 必须处理的事:

### 1. 真正的高可用,不再是"单实例但换了运维方式"

[ADR-038](../../docs/decisions/038-cloudnativepg-evaluation.md) 明确
记录过:local-lite 单节点机器上,CloudNativePG 只能跑 `instances: 1`,
"换 operator 管理"带来的是运维能力(自动备份、更规范升级),不是真的
高可用。**prod 阶段、有多节点可用时,这是补齐真正 HA 的窗口**:
- Postgres 副本数:**已经做成配置项**(ADR-059,
  `environments/resource-profiles.yaml` 的 `postgres_instances`,prod 档
  已经是 3),不用再手改 manifest。
- MinIO 从 `mode: standalone` 换成 `mode: distributed`:**这一条还没做,
  是有意留着的**。chart 原生支持,但分布式模式的副本数和纠删码、磁盘布局
  绑死,不是把 `minio_replicas` 从 1 改成 4 就行——猜一个数字比留在单节点
  更危险。prod 档目前只放大了 MinIO 的资源和存储容量,副本数仍然是 1,
  需要先做磁盘布局规划再动。
- Trino worker 数:**已经做成配置项**(`trino_workers`,prod 档是 3)。
- OpenSearch:**已经做成配置项**(`opensearch_replicas` 3 +
  `opensearch_single_node` false,这两个必须一起切,原因见组件文件注释)。
- Kafka 从单节点 KRaft 换成真正的多 broker 集群,评估副本因子
  (replication factor)配置。

### 2. TLS 证书从自签换成真实证书

[ADR-016](../../docs/decisions/016-ingress-domains-local-lite.md) 的
`*.local-lite.test` 自造域名 + cert-manager 自签证书是 local-lite
专用方案。prod 需要:真实域名 + cert-manager 接真实的 CA(Let's
Encrypt 或企业内部 CA,看有没有公网出口决定用哪种 ACME 方式)。

**2026-08-22 更新:切换机制已经做好了**(见
[ADR-060](../../docs/decisions/060-conditional-rendering-and-tls-issuer.md))
——`config.yaml` 里的 `tls_issuer_mode` 从 `selfsigned` 改成 `acme`,渲染
之后 ClusterIssuer 就从自签换成 ACME,**引用它的 Certificate 资源一行
都不用改**(三个环境的 issuer 同名 `platform-issuer`)。

**但这只是"配置结构就位",不是"跑通过"。** ACME 这一档还没有在真实
环境验证过,真正生效还依赖三件这个仓库控制不了的事:

1. 真实域名解析指到集群入口
2. 80 端口从公网可达(HTTP-01 挑战要用)
3. 国内公网服务还要 ICP 备案

另外 `tls_acme_server` 默认给的是 Let's Encrypt **staging** 地址,先用它
跑通一次签发再换 production——production 有每周签发限额,配错了反复重试
会把额度打光,被限流之后只能等一周。

`platform/coredns-custom/` 这个自定义 DNS 解析在 prod 阶段应该整个
删掉(见那个 ConfigMap 顶部注释,是 local-lite 专用的临时方案)。这一条
还没做,是 `render-if` 机制的下一个适用场景。

### 3. 备份目标从本地 MinIO 换成真正异地的存储

[ADR-033](../../docs/decisions/033-postgres-backup.md) 的 Postgres
备份现在传到集群自己的 MinIO——如果 MinIO 本身也在这几台服务器上
(没有异地容灾),"整个机房出问题"这类场景下备份和数据一起没了。prod
阶段要么接一个真正独立于这几台服务器之外的对象存储(公有云 S3,或者
另一个机房的 MinIO),要么至少做跨节点的存储冗余。同时 ADR-033 提过
备份文件目前没加密,prod 阶段要重新评估。

### 4. Keycloak 会话超时、密钥轮换等安全基线

`docs/operations/tuning.md` 已经标了一条:Keycloak 的
`ssoSessionIdleTimeout`/`ssoSessionMaxLifespan`(现在是 8/24 小时)
是为 local-lite 开发联调放宽的值,**prod 部署前必须按公司安全基线
重新评估**,不能直接照抄。同样要review 的还有:各组件的初始密码
(`scripts/00-generate-secrets.sh` 生成的)要不要接进真正的密钥管理
系统(Vault 之类),而不是留在 `secrets/generated-credentials.txt`
这个本地文件里。

### 5. 权限模型:local-lite 阶段的"能碰机器=管理员"在 prod 不成立

见 [`docs/operations/onboarding-offboarding.md`](../../docs/operations/onboarding-offboarding.md)
最后一节——这是明确记录过的已知缺口,prod 阶段必须重新设计 kubectl/
kubeconfig 的访问分级,不能沿用 local-lite 单机开发环境的假设。

## 资源分配建议(节点数已确认是 6,单节点真实 CPU 核数还没有,先给分配思路)

具体 CPU 核数确认之前,这里先给一个**分配思路**,不是精确到每个组件
该分多少核/多少内存的表格(那样的表格现在填出来的数字没有真实依据,
和这个项目"不做没法验证的东西"的原则冲突,拿到真实核数之后按下面的
思路展开就能算出具体数字):

- **存储层**(Postgres HA ×3、MinIO 分布式、Hive Metastore):建议专门
  隔出 6 台里的 2 台承载(不是 1 台——CNPG 的 3 副本本身就该跨节点
  分布,只留 1 台会让"高可用"名不副实,单节点故障照样打穿),数据
  服务对磁盘 I/O 敏感,不和计算密集型的 Spark/Trino 抢同一批节点。
  用 `nodeAffinity`/`taint` 把这两台专门标出来,不靠"希望调度器自己
  分散"这种隐式假设。
- **计算层**(Trino worker、Spark executor、Airflow worker):剩下
  的 4 台按需要弹性的量级分配,这类负载天然适合 k8s 的调度弹性,不需要
  像存储层那样固定绑核。
- **平台底座**(Keycloak/ArgoCD/Prometheus 等):资源需求相对固定且
  不大(参考 cloud-full 模板里的量级,乘 1.5-2 倍留余量),可以和计算层
  混跑在同一批节点上,不需要单独隔离。
- 拿到真实 CPU 核数之后,回来把这份文档和 `apps/definitions/*.yaml`
  里对应组件的 `resources.requests/limits` 一起精算一遍,不要凭这里
  的估算数字直接上生产。
