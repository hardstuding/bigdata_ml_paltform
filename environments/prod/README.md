# prod 环境画像

## 先说清楚一个不确定的地方,不是回避,是不想编数字

用户在 2026-08-14 提到"prod 按我之前说的 5 台服务器那种"生成配置模板。
翻遍了这个项目目录下能找到的全部历史会话记录(包括当前会话之外、更早
的几份原始 `.jsonl` 文件),**没有找到"5 台服务器"这个具体规格的原始
记录**——唯一验证过的、写进 memory 系统的硬件基线是**现有(旧)生产
CDH 集群:6 台服务器,每台 256GB 内存,约 50TB 存储(总计约 1.5TB
内存 / 300TB 存储),YARN/Spark/Flink/Maxwell/Flume 混跑**(来自
2026-08-08 的一次会话,已核实)。

这两个数字对不上,不确定是:(a) 用户说的"5 台"是记错了,实际指这个
6 台的旧集群;(b) 这个新平台的 prod 目标本来就是一个不同于旧 CDH 集群
的、单独规划的 5 台新硬件,只是这个具体规格还没有被准确记录过。**这份
模板先用验证过的 6 台/256GB 基线把计算过程做实**,方便直接套用真实
数字改;如果实际是 5 台或者规格不同,把下面"目标硬件"这一节的参数
换掉,后面的资源分配表是按每台服务器规格算比例的,换个数字重新算一遍
结论就对了。

## 目标硬件(先用验证过的基线代入,待确认)

- 节点数:6(如果实际是 5,下面的"每节点建议承载"要重新按 5 台摊)
- 单节点:256GB 内存(具体 CPU 核数没有记录过,建议确认;这类内存
  规模的服务器一般配 64-96 核,先按 64 核估,不确定的地方后面会标出来)
- 单节点存储:约 50TB
- 集群总量:约 1.5TB 内存 / 300TB 存储

**这是旧 CDH 集群的规格,不代表这台硬件会原样腾给新平台用**——旧集群
目前跑的是生产业务(YARN/Spark/Flink/Maxwell/Flume 混跑),这份模板
假设的是"新平台在同等量级的独立硬件上落地",不是"直接抢旧集群的资源"。
真正上生产前,这个假设需要和用户确认。

## 和 cloud-full 的关键差异,不只是"资源更多"

参考 [`environments/cloud-full/README.md`](../cloud-full/README.md)
先把组件资源规划过一遍——prod 在那个基础上,还有几件 cloud-full 不需要
考虑、但 prod 必须处理的事:

### 1. 真正的高可用,不再是"单实例但换了运维方式"

[ADR-038](../../docs/decisions/038-cloudnativepg-evaluation.md) 明确
记录过:local-lite 单节点机器上,CloudNativePG 只能跑 `instances: 1`,
"换 operator 管理"带来的是运维能力(自动备份、更规范升级),不是真的
高可用。**prod 阶段、有多节点可用时,这是补齐真正 HA 的窗口**:
- `apps/postgres/manifests/cluster.yaml` 的 `spec.instances` 从 1
  改成 3(CNPG 官方建议的最小 HA 副本数,支持自动故障转移)。
- MinIO 从 `mode: standalone` 换成 `mode: distributed`(chart 原生
  支持,`platform/apps/minio` 的 values 需要重写,不是简单加副本数——
  分布式模式对磁盘布局有要求,得提前规划)。
- Trino 从 `server.workers: 0`(coordinator 单打独斗)拆出真正独立的
  worker 节点,给多副本。
- Kafka 从单节点 KRaft 换成真正的多 broker 集群,评估副本因子
  (replication factor)配置。

### 2. TLS 证书从自签换成真实证书

[ADR-016](../../docs/decisions/016-ingress-domains-local-lite.md) 的
`*.local-lite.test` 自造域名 + cert-manager 自签证书是 local-lite
专用方案。prod 需要:真实域名 + cert-manager 接真实的 CA(Let's
Encrypt 或企业内部 CA,看有没有公网出口决定用哪种 ACME 方式)。
`platform/coredns-custom/` 这个自定义 DNS 解析在 prod 阶段应该整个
删掉(见那个 ConfigMap 顶部注释,是 local-lite 专用的临时方案)。

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

## 资源分配建议(占位,需要真实节点数确认后精算)

在确认到底是 5 台还是 6 台、每台的真实 CPU 核数之前,这里先给一个
**分配思路**,不是精确到每个组件该分多少的表格(那样的表格现在填出来
的数字没有真实依据,和这个项目"不做没法验证的东西"的原则冲突):

- 存储层(Postgres HA ×3、MinIO 分布式、Hive Metastore):建议专门
  隔出 1-2 台承载,数据服务对磁盘 I/O 敏感,不和计算密集型的 Spark/
  Trino 抢同一批节点。
- 计算层(Trino worker、Spark executor、Airflow worker):按需要
  弹性的量级分配剩余节点,这类负载天然适合 k8s 的调度弹性,不需要
  像存储层那样固定绑核。
- 平台底座(Keycloak/ArgoCD/Prometheus 等):资源需求相对固定且不大
  (参考 cloud-full 模板里的量级,乘 1.5-2 倍留余量),可以和计算层
  混跑,不需要单独隔离节点。
- 具体每台服务器跑哪些 pod、要不要用 `nodeAffinity`/`taint` 做隔离,
  等确认了真实节点规格和数量之后再展开,现在写死会是没有依据的数字。
