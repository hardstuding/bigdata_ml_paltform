# 030. 可插拔基础设施:允许接公司已有的 Postgres/Kafka/对象存储/SSO,不强制全部自建

- 状态: 已采纳,**Postgres 这条已经推广到 Keycloak + Hive Metastore + MLflow +
  Superset + OpenMetadata,S3/Kafka/SSO 待推广**(2026-08-11/13)

## 背景

用户明确了这个项目的定位:不是一次性内部工具,是要作为开源项目让别的公司
也能用的。真实企业场景下,大概率已经有自己的 Postgres/Kafka/对象存储/统一
身份系统——如果这个平台强制"必须用我们打包的那一份",接入成本和风险都会
劝退潜在使用者(相当于让人家先把已有基础设施推倒重来才能试用)。

这不是"给所有组件都做一遍云厂商式的高可用切换开关"那种量级的工程,量力
而行:多数组件本来就已经是靠一个 `xxx.database.hostname` 或者一段 JDBC URL
字符串连接数据库,这些本身就是**明文写在 git 里的普通配置值**,一直都能改
——真正缺的不是"能力",是**清晰标出"这里可以换成你自己的基础设施"**,和
"换了之后哪些自动建库/建账号的步骤要跳过"。

## 决策

### 模式:文档化的覆盖点,不引入新的抽象层

考虑过做一个中心化的"外部基础设施声明文件"(比如
`platform/external-infra.yaml`,写一遍"我们已经有 Postgres 在 X"),再靠
脚本生成/patch 各组件的 Application yaml——**否决了**,原因是这个项目从
Phase 0 就定的架构原则是"组件独立可升级,禁止用一个大 umbrella
chart/生成器把所有组件焊在一起"(见 architecture.md 原则 3)。加一层生成器
本质上就是那种"焊起来"的反模式,会让"git 状态 = 机器状态"这条更难验证
(生成结果本身也要进 git,还多一步"这份生成结果是不是新的"的心智负担)。

选择直接在**每个组件自己的 Application yaml 里**,用统一的
`【可插拔基础设施】` 注释标记出"这里改成外部实例"和"对应哪个 -db-init
Job 要跳过"。找起来靠 `grep -rn "可插拔基础设施"`,不靠一个中心索引文件——
和这个项目一直以来"每个组件的坑记在组件自己旁边,不搞一个巨大的全局文档"
的习惯一致。

### 已经落地的两个参考例子

- **Keycloak**(`platform/apps/keycloak.yaml`):codecentric/keycloakx 这个
  chart 原生就把 `database.hostname/port/database/username/existingSecret`
  拆成独立字段,不是一个不可拆的 DSN 字符串,改起来很直接。配套的
  `apps/definitions/keycloak-db-init.yaml`(建库用的 Job)标注了"接外部
  Postgres 就别装这个文件,数据库应该由你们 DBA 按平时流程建"。
- **Hive Metastore**(`apps/hive-metastore/manifests/deployment.yaml`):裸
  manifest,连接串是一行 `-Djavax.jdo.option.ConnectionURL=jdbc:...` 字符串,
  改这一行就行。**但这个例子暴露了一个历史遗留的不一致**:metastore 这个
  库不是靠独立 Job 建的,是写死在 `apps/postgres/manifests/
  init-configmap.yaml` 里,跟着共享 Postgres 自己首次启动一起跑——接外部
  Postgres 时,这个初始化脚本不会跟着生效,库还是得手动建。这处不一致
  留作已知欠账,不在这次范围内重构(现在的 Hive Metastore 是 Phase 1
  活跃组件,风险收益比不划算现在动它)。

### 2026-08-13 补充:MLflow/Superset/OpenMetadata 也标好了

三个都是 `-db-init.yaml` 独立 Job 建库的模式,结构上和 Keycloak 是同一套,
标记方式照抄,但连接串的**载体**三家都不一样,值得记一下差异,不是复制
粘贴就完事:

- **OpenMetadata** 最干净:`database.host/port/databaseName/auth.username`
  直接是 Application yaml 里的独立字段,和 Keycloak 那个参考例子完全同款。
- **Superset**:字段也是独立的(`DB_HOST`/`DB_USER`/`DB_PASS`/`DB_PORT`/
  `DB_NAME`),但不在 Application yaml 里,而是在 `superset-db-secrets`
  这个 Secret 里(chart 靠 `envFromSecret` 整体引用)——接外部 Postgres
  改的是这个 Secret,不是 `apps/definitions/superset.yaml` 本身。
- **MLflow** 最不灵活:chart 只接受一整个 DSN 字符串(`mlflow-db-secret`
  的 `uri` 字段),不是拆开的字段,改的时候整个 URI 都要重新拼。

Airflow 也补上了,和 MLflow 是同一类(整个 DSN 存在 `airflow-metadata`
这个 Secret 的 `connection` 字段里,不是拆开的字段)。

优先级(用户确认过的顺序):先做企业普遍已有的基础设施(Postgres、S3 兼容
对象存储、Kafka、已有的 SSO/IdP),这些换掉能带来最大的采用门槛下降;
Trino/Superset/MLflow/JupyterHub/KServe 这类偏"这个平台特有"的组件,公司
本来就不太可能已经有替代品,不是这轮的重点。

对象存储(MinIO → 外部 S3):现在各组件已经是通过
`MINIO_ROOT_USER`/`endpoint` 这类环境变量连接,本质和 Postgres 是同一类
"改个 endpoint + 凭据"的覆盖点,后续按同样的标记方式补。

SSO/IdP(Keycloak 本身 → 公司已有的 AD/Azure AD/Okta):这个和数据库不是
同一类问题——不是"换个 hostname"就行,需要 Keycloak 的 Identity Brokering
或者干脆整个跳过 Keycloak、各组件直接对接公司已有 IdP 的 OIDC endpoint,
架构改动更大,见 docs/decisions/028 里"和 HR 系统对接"那段讨论,是分开的
后续课题。

## 后果

- 这轮改动本身**不影响 local-lite 的默认行为**——不改
  `【可插拔基础设施】` 标注的那几行,所有组件还是接本仓库自带的 Postgres,
  没有引入任何新的失败模式。
- 只做了两个组件当参考例子,不是全覆盖,后续每接一个新组件、或者回头补
  已有组件,都应该照着这两个例子的标记方式加一份。
- 没有做自动化测试/CI 检查确认"这些覆盖点真的能连外部 Postgres"——只是
  静态标注了"改这里",没有真的拿一个外部 Postgres 实例验证过,等真的有
  人在自己环境试的时候才会验证到。
