# 051. Trino 细粒度访问控制:OPA 策略引擎 + grants 数据同步机制

- 状态: **2026-08-16 已正式接进 Trino 生效**(cloud-full,zhenghe 在场,
  见文末"2026-08-16 正式上线"一节)。下面"已实现,已在本地 + 真实集群里
  充分验证"和"上线前必须先做的事"两节是切换前的记录,保留作为过程存档
  一节。

> **2026-08-26 后续修订**:这份 ADR 里"`superset_service` 一个共享账号连
> Trino、看板可见性交给 Superset 自己的 RBAC 管"这个取舍,**已经被
> [ADR-074](074-superset-impersonation.md) 推翻**。当时没算清的代价是:
> 能在 Superset 里建 chart 的人可以绕开任何看板直接写 SQL,于是列级脱敏和
> 行级过滤([ADR-063](063-trino-column-row-level-security.md))在 BI 这条路上
> 完全不生效。现在改成透传登录用户身份。**照这份 ADR 的原文去配会配出那个洞。**

## 背景

ADR-028"后续"一节早就写下要用 Trino 原生的 OPA 授权插件做细粒度权限,
一直没有展开设计和实现。`platform/iam/table-access-grants.csv`
(ADR-044/046/050)一直有数据在积累,但从没有任何东西真正读它去拦截
Trino 查询——ADR-044/045/046/050 反复强调这条边界:表访问申请/审批/回收
这套流程做的是"决策与留痕",不是真的访问控制。这次把"真的接上执行引擎"
这部分补上。

用户明确要求过这项("你做就好了"),同时这台开发机内存已经吃紧
(88%,`kubectl top node` 实测),Trino 当天已经因为资源争抢崩溃重启过。
用户后续澄清:资源不够跑不满,不代表不能先把代码写完、在能验证的范围内
验证完——"上云测"是下一步,不是不做这一步。这份 ADR 记录的就是"写完 +
本地能验证的部分都验证过"这个状态。

## 部署方式:原生 K8s manifest,不是 Helm chart

调研确认(`WebSearch`/`gh api search/code` 实测查证,不是凭印象):

- **Trino 的 OPA 授权插件是 Trino 官方原生功能**,不是第三方插件——源码
  在 `trinodb/trino` 仓库自己的 `plugin/trino-opa` 目录下
  (`io.trino.plugin.opa.OpaAccessControl`),官方文档在
  trino.io/docs/current/security/opa-access-control.html。这条符合这个
  项目"只用官方支持的方式"的门槛。
- **OPA 本身没有官方维护的 Helm chart**——这一点之前 ADR-028 写错了
  (写的是"OPA 有官方 chart"),这次核实时发现
  `open-policy-agent/opa` 仓库里有一个从很早就开着、至今没关的 issue
  #7109 就是在要一个官方 chart,说明确实没有。已经回去更正了 ADR-028
  那句话,不让错误的记录继续留着。
- 更正之后的实际选择:OPA 官方发布的 Docker 镜像
  (`openpolicyagent/opa`,DockerHub 官方仓库)+ 这个项目自己写的原生
  K8s manifest,不经过 Helm——OPA 本身是单容器、无状态的策略引擎,复杂度
  和 `permission-request-app`/`platform-portal` 这几个自建薄组件是一个
  量级,用它们同一套"官方镜像 + 自己写 manifest"模式,不需要 chart 那套
  机制,不违反"只用官方支持方式"这条门槛的精神(门槛真正要防的是"来源
  不明、社区拼凑的打包方式",不是"必须是 Helm chart 这一种形式")。
- 镜像版本:`openpolicyagent/opa:1.19.0`(GitHub Releases API 查证的当前
  最新稳定版,发布于 2026-07-30),用非 `-static` 版本(带 shell,方便
  `kubectl exec` 排障,和这个项目其他组件的取舍一致)。

## 策略设计:Rego 策略只做"能不能读到这张表的数据"这一层

策略文件 `apps/opa/policy/trino.rego`,`package trino`,顶层
`default allow := false`(fail-closed,这是给 Trino 加访问控制,不是加了
个形式)。

三类操作分别处理(Trino 发送的 `action.operation` 完整取值来自
`OpaAccessControl.java` 源码里实际调用的字符串常量,`gh api search/code`
+ `curl raw.githubusercontent.com` 拉源码核实过完整列表,不是猜的):

1. **服务账号(`table_registration_service`)和 `platform-team` 组成员**:
   任何操作都放行。前者是 `apps/table-registration-app` 建表用的专属
   身份(不是终端用户),后者和 ArgoCD(ADR-028)的权限模型一致,平台
   管理员不受表级审批约束,方便排障。
2. **基础浏览操作**(`ExecuteQuery`/`AccessCatalog`/`ShowSchemas`/
   `ShowTables`/`ShowColumns`/`ShowCreateSchema`/`FilterCatalogs`/
   `FilterSchemas`/`FilterTables`/`FilterColumns`):任何登录用户放行。
   能看到目录结构存在,不等于能读数据——和这个平台已有的"任何登录用户
   都能浏览表目录、决定要不要申请"这个模式(权限申请门户的 catalog 浏览,
   ADR-046)保持一致,不在浏览这层新增限制。
3. **真正读数据**(`SelectFromColumns`/`ShowCreateTable`):按
   `data.trino.grants`(运行时从 grants.csv 同步过来的数据)判断——用户
   对 `schemaName.tableName` 这个 `table_fqn` 有没有一条未过期的 grant
   记录,过期判断复用 ADR-050 已经在写的 `expires_at` 字段。

**没有明确覆盖的操作全部默认拒绝**——`CreateTable`/`DropTable`/
`InsertIntoTable`/`DeleteFromTable`/`AlterColumn`/`CreateView`/... 这些
写类操作,终端用户一律拒绝。这是有意的设计:这个平台的建表/改表走
`table-registration-app` 这个专属工具(用服务账号),不是让终端用户在
Trino 里直接执行 DDL/DML。以后如果真有需要放开某类写操作给终端用户,
应该是一次独立、有讨论的决定,不是在这份策略里悄悄漏放导致的意外结果。

## 数据同步:OPA Data API 运行时 PUT,不是 ConfigMap + 重启

`apps/opa/manifests/grants-sync-cronjob.yaml`,每 5 分钟跑一次:

- 直接读 GitHub raw content(`raw.githubusercontent.com/hardstuding/
  bigdata_ml_paltform/main/platform/iam/table-access-grants.csv`)——这个
  仓库是公开的,不需要 git clone、不需要认证,比照抄 `iam-sync`(还要装
  git)更简单。
- 转成 JSON 数组后 `PUT http://opa.opa.svc.cluster.local:8181/v1/data/
  trino/grants`——这是 OPA 官方文档记录的 Data API,专门给"运行时推送
  数据"设计的,写进去立即在下一次策略求值时生效,不需要重启 OPA、不需要
  等 kubelet 同步 ConfigMap 那个已知的 ~1 分钟延迟(这个项目在
  `docs/operations/troubleshooting.md` 记过 ConfigMap subPath 挂载延迟的
  坑,这次直接绕开,不是没想到那个坑)。
- 镜像用 `python:3.12-slim`,但**只用标准库**(`urllib.request`/`csv`/
  `json`),不装任何包——这个项目在 `apps/iam-sync/manifests/cronjob.yaml`
  记过好几次 apt-get/pip 网络挂死的真实教训,这次不给它任何机会踩同样
  的坑。

## 上线前必须先做的事(这次故意没做,留给用户决定时机)

**这是这份 ADR 里最重要的一节**:接进 Trino 只差一步——在
`access-control.properties` 加 `access-control.name=opa` +
`opa.policy.uri=http://opa.opa.svc.cluster.local:8181/v1/data/trino/allow`
——但这次**故意没有**把这个改动加进 `apps/definitions/trino.yaml`。

原因:**Trino 现在完全没有访问控制**(`AllowAllAccessControl`,查过
`apps/definitions/trino.yaml` 确认没有任何 `access-control.*` 配置)。
接上 OPA 是一次真实的行为收紧,不是"反正本来就这样加个形式"——上线那
一刻起,`table-access-grants.csv` 里没有对应 grant 记录的表,所有非
`table_registration_service`/`platform-team` 的用户都会立刻查不到,
**包括 Superset 现在正在用的数据源表**(现在 grants.csv 里只有两条测试
记录)。这个改动如果通过 `apps-root` 的自动 sync 静默生效,会在没人盯着
的时候直接打断 Superset 看板、打断任何人正在跑的查询,而且这台机器
现在内存已经吃紧、Trino 本身还不稳定,不是验证这种变更的好时机。

真正要打开这个开关之前,需要:

1. 盘一遍现在实际在用 Trino 查询哪些表(至少包括 Superset 的数据源、
   `docs/architecture.md`/近期 commit 提到的 `iceberg.demo.orders` 这类
   已知在用的表),为它们造好对应的 grant 记录(可以用
   `apply_grant_to_git()` 走正常申请流程,也可以是一次性批量脚本,批量
   脚本本身该不该做、怎么做,是需要讨论的,不是这次顺手加的)。
2. 确认好"切换失败了怎么快速回滚"——GitOps 模型下这一步很简单
   (revert 那一条 commit,ArgoCD 会自动同步回去),但仍然应该是一次
   `zhenghe` 在场、能盯着看结果的操作,不是无人值守时的自动变更。
3. 决定要不要先跑一段"影子模式"——OPA 配置里其实可以只开
   `opa.log-requests`/`opa.log-responses` 观察一段时间会拒绝哪些真实
   请求,再正式切换 `access-control.name=opa`,降低"切换当天才发现漏了
   一堆 grant"的风险。这个没有定论,留给下次讨论。

## 涉及的文件

- 新增 `apps/opa/policy/trino.rego` + `trino_test.rego`(本地
  `opa test` 用,不进部署的 ConfigMap)
- 新增 `apps/opa/manifests/`:`deployment.yaml`(OPA Deployment+Service)、
  `policy-configmap.yaml`(和 `trino.rego` 同步)、
  `grants-sync-cronjob.yaml`
- 新增 `apps/definitions/opa.yaml`(ArgoCD Application)
- 新增 `platform/network-policies/manifests/opa.yaml`
- 改:`docs/decisions/028-iam-org-model.md`(更正"OPA 有官方 chart"这句
  错误记录)、`docs/architecture.md`(补组件行 + 更正 Phase 4 那行)
- **没有改**:`apps/definitions/trino.yaml`——这是故意的,见上一节。

## 验证

### 已验证(2026-08-15,本地真实跑过,不是只看代码/只读文档)

- **Trino OPA 输入/输出 schema**:没有凭印象写,用 `gh api search/code`
  定位到 `trinodb/trino` 仓库真实源码
  (`plugin/trino-opa/src/main/java/io/trino/plugin/opa/
  OpaAccessControl.java`),`curl raw.githubusercontent.com` 拉下来确认
  `action.operation` 的完整取值列表(62 个,`AccessCatalog` 到
  `WriteSystemInformation`),策略里用到的每一个操作名都是从这份真实源码
  核对过的。
- **策略单测**:本地 `docker pull openpolicyagent/opa:1.19.0`,
  `opa test` 跑 10 个测试用例(服务账号放行、platform-team 放行、基础
  浏览放行、有效 grant 放行、无 grant 拒绝、过期 grant 拒绝、跨用户拒绝、
  `ShowCreateTable` 同 `SelectFromColumns` 一样受控、DDL 对普通用户拒绝、
  即使有 SELECT grant 也不能 INSERT)——**10/10 PASS**。
- **真实 HTTP 场景**:本地 `docker run` 起一个 OPA server(挂载策略+一份
  样例 grants 数据),用真实 `curl POST /v1/data/trino/allow`(和 Trino
  实际会发的请求同一个 URL 路径)测了 5 个场景(有效 grant 允许、无 grant
  拒绝、DDL 对普通用户拒绝、服务账号 DDL 允许、任何人可以 `ShowTables`)
  ——全部符合预期。
- **Data API 运行时更新**:`curl -X PUT /v1/data/trino/grants` 推一份新
  数据,`204` 响应,立即用新数据重新 `curl POST /v1/data/trino/allow`
  验证——旧用户的授权确实消失了(PUT 是整体替换,不是追加),新用户的
  授权确实生效,不需要重启 OPA 容器。
- **grants-sync 脚本端到端**:本地直接跑 sync CronJob 里那段 Python 脚本
  (原样复制,不是简化版),真的从 GitHub raw content 拉到当前仓库里
  `table-access-grants.csv` 的两条真实记录,`PUT` 进本地 OPA 实例,
  `curl GET /v1/data/trino/grants` 确认数据一致——整条链路(公网 GitHub
  → Python 标准库 → OPA Data API)在没有 K8s 集群参与的情况下就已经跑通。

### 也已经在真实集群里部署并验证过(2026-08-15,补充)

本地验证完之后评估过风险:部署 OPA 服务器本身对现有组件零风险
(Trino 没有指向它,不影响任何正在跑的东西),footprint 也小
(128Mi 内存上限),所以没有停在"只在本地验证",接着在这台机器的真实
K8s 集群里也部署+验证了一遍:

- commit → push → `apps-root` 自动发现新 Application → `opa`/
  `network-policies` 都到 Synced/Healthy,revision 对上。
- `opa` 命名空间:Deployment 起来、Pod Running、CronJob 创建成功。
- **NetworkPolicy 边界实测**:从 `trino` 命名空间起一个测试 pod 探测
  `opa:8181/health`,返回 `200`;从 `default` 命名空间起同样的测试 pod
  探测,连接被拒(exit 7),确认"只有 trino 命名空间能连 OPA"这条边界
  真的生效,不是只在 YAML 里写着。
- **live 策略实测**:从 trino 命名空间内直接 `curl POST /v1/data/trino/
  allow`,无 grant 的 SELECT 返回 `{"result":false}`,基础浏览操作
  (`ExecuteQuery`)返回 `{"result":true}`,和本地 `opa test` 的结论
  一致。
- **grants-sync CronJob 真实跑通**:用 `kubectl create job --from=
  cronjob/opa-grants-sync` 手动触发一次,Job 状态
  `Complete`(`succeeded: 1`);紧接着查 OPA 里 `/v1/data/trino/grants`
  的实际内容,确认就是 `table-access-grants.csv` 当前的两条真实记录——
  不是只看 Job 没报错,是确认了它产生的实际效果。

### 还没验证的(诚实标注,不是回避)

- **没有接进 Trino 生效**——见上面"上线前必须先做的事",这是故意的,
  不是没做完。这也是这台机器上 OPA 现在唯一"部署了但没有真实作用"的
  部分:它已经在跑、数据也在正常同步,但 Trino 现在完全不会去问它。

## 2026-08-16 正式上线:cloud-full 切换 access-control.name=opa

zhenghe 在场,按上面"上线前必须先做的事"清单执行,过程中额外发现并修
了 3 个真实 bug(不是纸面计划,是实际操作时撞上的)。

### 上线前审计(按清单第 1 条)

查了 Superset 数据库(`superset` 库的 `dbs`/`tables` 表)和
`scripts/00-generate-secrets.sh` 里 `ensure_trino_service_account` 的
调用,确认现在实际连 Trino 的身份只有 3 个服务账号,不是猜的:

- `superset_service`——Superset 所有看板/数据源共用一个连接。
- `table_registration_service`——已经在原策略的白名单里。
- `dbt_demo_service`——dbt_demo DAG 用,会真实执行 `CREATE TABLE`/
  `INSERT` 这类写操作。

原策略只放行了 `table_registration_service`,`superset_service` 和
`dbt_demo_service` 补进 `apps/opa/policy/trino.rego` 的
`service_accounts` 白名单(`apps/opa/manifests/policy-configmap.yaml`
同步改,两边继续保持字节级一致)。补了 2 个对应的 `opa test` 用例,
12/12 全部通过(`docker run openpolicyagent/opa:1.19.0 test` 本地验证,
不是凭印象改完就信)。如果这一步漏做,切换瞬间 Superset 看板会查不到
数据、dbt_demo DAG 会失败——这是切换前审计要防的真实后果,不是假设性
风险。

### 撞上的 3 个真实 bug

1. **`opa-grants-sync` CronJob 硬编码 colima 专用代理地址**,cloud-full
   上连不上导致一直失败、被临时 suspend(`docs/project/roadmap.md` 记过这件事,
   但没细究是不是这个具体原因)。改成和
   `permission-request-app`/`table-registration-app` 同一套"运行时探测
   代理是否可达"模式(2 秒连不上就当作不需要),修完手动跑一次验证
   `PUT /v1/data/trino/grants` 返回 `204`,`GET` 确认数据真的进去了。
2. **Trino 拒绝把 `access-control.name`/`opa.policy.uri` 塞进主
   `config.properties`**:实测启动直接报错"Configuration property
   'access-control.name' was not used. Did you mean to use
   'access-control.config-files'?"——Trino 要求访问控制配置必须是独立
   文件。改用 `trinodb/charts` 原生的 `accessControl`(`type: properties`)
   顶层 values key,会自动生成 `etc/access-control.properties`(Trino
   按约定路径自动加载,不需要显式 `access-control.config-files`)。这次
   先用 `helm template` 本地渲染确认生成的 ConfigMap 内容正确,再推上去,
   不是"改完直接上线试错"。
3. **ArgoCD `ignoreDifferences` 没有真正生效**:`apps/definitions/
   trino.yaml` 里早就为 `livenessProbe`(chart 硬编码 httpGet,被
   `scripts/07-fix-trino-liveness-probe.sh` 一次性 patch 成 exec)声明了
   `ignoreDifferences`,但这是这条声明第一次真的被一次 `sync` 操作触发
   ——实测发现 `ignoreDifferences` 只影响"要不要标记 OutOfSync"的比较
   逻辑,不影响 sync 时补丁实际提交的内容:ArgoCD 把 chart 默认的
   httpGet 和已经手动 patch 的 exec 合并提交,K8s API 拒绝("may not
   specify more than 1 handler type"),sync 卡在无限重试(5 次后
   Failed)。加 `syncOptions: [RespectIgnoreDifferences=true]` 之后
   sync 补丁也跳过了这个字段,恢复正常——这是让原有的 `ignoreDifferences`
   真正达到设计时想要的效果,不是新引入的例外。全程活的 Deployment 探针
   字段没有被破坏(K8s API 拒绝了坏补丁,不是接受了坏补丁),没有真实
   故障窗口。

### 上线后端到端验证(真实查询,不是只看配置)

- 直接对活的 OPA 实例(带真实 grants 数据,不是本地 mock)发 3 组
  `POST /v1/data/trino/allow`:没有 grant 记录的用户查任意表 →
  `false`;`analyst001` 查自己有 grant 的表 → `true`;`analyst001` 查
  没有 grant 的表 → `false`。三组结果全部符合预期。
- 用 `superset_service` 的真实凭据,在集群里起一次性 pod 直接用
  Trino Python client 连 Trino(`https://trino.trino.svc.cluster.local:
  8443`)查 `iceberg.demo.orders`,成功拿到数据(`[[10]]`)——证明
  Superset 现在正在用的这条真实查询路径没有被打断,不是只测了 OPA 自己
  的决策 API。
- `kubectl get applications -n argocd`:`trino`/`opa` 都是
  `Synced/Healthy`;`superset` pod 没有因为这次变更受影响
  (`1/1 Running`)。

### 回滚方式(记录清楚,不用临场想)

`git revert` 这几条切换相关的 commit(`5bf9c78`/`b1e5a1d`,以及如果
`RespectIgnoreDifferences` 那条 `8e6384e` 也想收回的话一起),push 后
ArgoCD 会自动同步回 `AllowAllAccessControl`,不需要额外手动操作。
