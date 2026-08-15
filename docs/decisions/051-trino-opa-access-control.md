# 051. Trino 细粒度访问控制:OPA 策略引擎 + grants 数据同步机制

- 状态: 已实现,已在本地充分验证——**但故意没有接进 Trino 生效**,这是
  这份 ADR 里最重要的一条,不是遗漏,见"上线前必须先做的事"一节。

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

### 还没验证的(诚实标注,不是回避)

- **没有在真实 K8s 集群里部署过** `apps/opa/manifests/` 这几份文件——
  没有跑 ArgoCD sync、没有确认 NetworkPolicy 规则、CronJob 在集群里的
  真实调度都没有实测过。这些和之前几个 ADR(048/050)的差别在于:这次
  在集群里部署 OPA 服务器本身是安全的(它不影响任何现有组件,Trino
  没有指向它),风险可控,但受限于这台机器当前 88% 的内存占用和 Trino
  本身已经不稳定,这次选择先把本地能验证的部分做扎实,集群内部署留到
  资源状况更好、或者上云测试的时候再做,不是技术上做不到。
- **没有接进 Trino 生效**——见上面"上线前必须先做的事",这是故意的,
  不是没做完。
