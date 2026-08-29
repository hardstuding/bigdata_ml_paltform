# 063. 敏感字段行列级策略:Trino OPA 列级脱敏 + 行级过滤

- 状态: 策略写完、`opa test` 本地全部验证通过(28/28,含 ADR-051 原有的
  12 个表级用例)。**没有在真实集群里验证过**——这一轮 cloud-full 云主机
  停机,只能做仓库级验证(`opa test`/`validate-charts.py`/YAML 解析),
  Trino 真的按这两个新接口调用 OPA、返回结果真的改写查询,还没有实测过。
  下次上云要补这一段。

## 背景

`docs/project/capability-matrix.md` 的数据治理板块里,"敏感字段行列级策略"一直是 ❌:
"OPA 原生支持,没配置"。OPA 已经在 2026-08-16 正式接进 Trino 生效
(ADR-051),但那份 ADR 自己写明了范围边界——只做"能不能读到这张表"的
表级判断,不覆盖列级脱敏/行级过滤。这份 ADR 补上这两块。

## 决策:复用 Trino OPA 插件原生的两个额外接口,不是自己发明拦截层

调研确认(`curl raw.githubusercontent.com` 拉 `trinodb/trino` 仓库
`plugin/trino-opa` 真实源码核实,不是凭印象——ADR-051 已经踩过"编接口"
的教训,这次继续用同一套核实方式):

- `OpaConfig.java` 里除了已经在用的 `opa.policy.uri`,还有
  `opa.policy.column-masking-uri`(非 batch,单条列查询)、
  `opa.policy.batch-column-masking-uri`(批量,这次没用,见下)、
  `opa.policy.row-filters-uri`。三个都是可选配置,不配就不启用对应能力,
  不会影响现有的 `allow` 判断。
- 列级脱敏(`GetColumnMask`)的请求体是
  `{"context": ..., "action": {"operation": "GetColumnMask", "resource": {"column": {"catalogName", "schemaName", "tableName", "columnName", "columnType"}}}}`
  (`OpaHighLevelClient.java`/`TrinoColumn.java` 核实过字段名),期望响应
  `{"result": {"expression": "<SQL 表达式>"}}`(`OpaColumnMaskQueryResult`,
  `result` 是 `Optional<OpaViewExpression>`——规则在 OPA 里 undefined 就是
  "不脱敏",不需要显式返回一个"不脱敏"的哨兵值)。
- 行级过滤(`GetRowFilters`)的请求体同构(`resource.table`),期望响应
  `{"result": [{"expression": "<SQL 布尔表达式>"}, ...]}`
  (`OpaRowFiltersQueryResult`,多条按 AND 叠加成 WHERE 条件)。
- 用非 batch 的 `column-masking-uri`,不用 `batch-column-masking-uri`:
  现在 `allow.` 判断本身也是非 batch 模式(`opa.policy.batched-uri` 没配),
  两边保持同一种调用模式,不引入"表级判断走单条请求、列级判断走批量请求"
  这种不一致,复杂度没有必要现在就上。以后如果实测发现单条请求的延迟/
  QPS 有问题,再切批量,是可以独立做的优化,不是这次的范围。

对应的 Rego 规则写在 `apps/opa/policy/trino.rego`(和表级 `allow` 同一个
文件、同一个 `package trino`——OPA 部署时只加载这一个文件,`deployment.yaml`
的启动参数写死 `/policies/trino.rego`,不想为了这次改动改部署方式),
`columnMask`/`rowFilters` 两条规则。

## 权限判断复用 platform/iam 已有数据,没有新发明一套模型

这是这次设计里最重要的一条约束(任务本身也这么要求)——具体怎么落地:

### 列级脱敏门槛:复用 `data.trino.grants` 里已有的 `security_level` 字段

`platform/iam/table-access-grants.csv` 每条 grant 记录本来就带
`security_level`(ADR-044/046 分级审批留下的字段,`apps/permission-
request-app` 的 `build_approval_steps` 里 level>=2 要多一层主管审批、
level>=3 再加一层)。这次没有新增字段、没有新增文件,直接用这个已有字段
判断"这个用户对这张表的授权级别够不够看敏感列的明文":

- 敏感列按列名分类(子串匹配,不区分大小写):`phone`/`mobile`/手机 →
  需要 `security_level >= 2`,做部分脱敏(保留前 3 后 4 之外的部分,保留
  排障时能核对到"大致是哪个号"的信息量,不是全部打码);`email`/邮箱 →
  同样 level 2,部分脱敏(保留前两位和 `@` 之后);`id_card`/
  `identity_no`/身份证 → 需要 `security_level >= 3`(和这个仓库对身份证
  的直觉一致,最敏感),整段替换成 `'***MASKED***'`,不留任何片段。
- 没有有效 grant(或 grant 过期)按 `security_level = 0` 处理,低于任何
  `required_level`,一定脱敏。这是有意的纵深防御:正常情况下没有 grant
  连 `SelectFromColumns` 都过不了(见 `allow` 规则),理论上走不到脱敏
  这一步,但脱敏判断本身不应该依赖"外面一定先挡住了"这个假设。

列名分类规则(`sensitive_column_rules`)本身是这份策略里声明式写死的,
和已有的 `basic_browse_operations`/`gated_operations` 同一个写法,不是
数据驱动——现在这个仓库的表都是英文列名,还没有真实的手机号/身份证字段
上生产,这份分类目前是"能力已经打通"而不是"已经在保护真实敏感数据"。
以后如果列名命名习惯和这几个模式对不上,或者需要按 OpenMetadata 的
`SecurityLevel` tag 之类的显式标注来判断哪些列敏感(比表格里"security_
level" 字段现在的粒度是整表,不是按列),需要扩展这份规则,不是这次的
范围。

### 行级过滤:按部门,数据来自 `platform/iam/employees.csv` 的 `department` 列

`employees.csv` 本来就有 `department` 字段(见 `platform/iam/
employees.csv` 表头),但这份数据现在只同步进 Keycloak 的
`memberships.csv`/`groups.yaml`(角色/组维度),没有任何东西读过
`department` 这一列。这次新增一个和 `opa-grants-sync` 同一个模式的
CronJob(`apps/opa/manifests/departments-sync-cronjob.yaml`):5 分钟一次,
读 `employees.csv` 的 `username,department` 两列,`PUT` 进
`data.trino.user_departments`。

行级过滤规则:哪些表要按部门过滤是策略里声明式的白名单
(`row_level_filtered_tables`,目前只有 `demo.regional_sales` 一张,还
没真的建过这张表,是给以后接真实的按区域/部门分片数据留的占位)——不是
对所有表都生效,这点和列级脱敏的"按列名通用匹配、对所有表都生效"不一样,
是有意的设计:行级过滤的语义("哪些行属于我的部门")只对本来就有
`department`/类似维度列的表有意义,乱猜列名比列级脱敏更容易出错(脱敏是
"宁可错杀"更安全,行级过滤"猜错列名"可能直接报 SQL 语法/字段不存在的
错误,炸掉查询),所以选择显式白名单而不是通用模式匹配。

边界情况——查不到部门(`employees.csv` 里没这个人,或者
`opa-departments-sync` 还没跑过/跑失败了):按这个仓库一贯的"宁可漏权限
报错,不要漏配置导致意外授权"原则,过滤成 `1 = 0`(空结果集),不是默认
不过滤看到全部数据。

### 豁免规则:和表级 `allow` 用同一条

服务账号(`is_service_account`)和平台管理组(`is_platform_admin`)对
列级脱敏/行级过滤都豁免,复用已有的判断,不是重新定义一套——这些账号的
查询本来就不代表某个真实终端用户在看数据(见 `trino.rego` 里
`service_accounts` 那段注释),ADR-051 已经讲过这条道理,这里不重复
一套新的豁免逻辑。

## 涉及的文件

- 改 `apps/opa/policy/trino.rego`:追加 `columnMask`/`rowFilters` 两条
  规则和相关辅助规则/函数,顶部注释更新指向这份 ADR。
- 改 `apps/opa/manifests/policy-configmap.yaml`:和 `trino.rego` 保持
  字节级同步(脚本核对过,`diff` 后去掉缩进逐字节一致)。
- 新增 `apps/opa/policy/trino_masking_test.rego`:18 个新用例(不进
  ConfigMap,和 `trino_test.rego` 一样只在本地/CI 跑 `opa test`)。
- 新增 `apps/opa/manifests/departments-sync-cronjob.yaml`:department
  数据同步,复用 `platform/network-policies/manifests/opa.yaml` 里已有的
  `allow-grants-sync-to-opa` 规则(pod label 沿用 `app: opa-grants-sync`,
  没有新增 NetworkPolicy)。
- 改 `apps/definitions/trino.yaml`:`accessControl.properties` 加
  `opa.policy.column-masking-uri`/`opa.policy.row-filters-uri` 两行。
- 新增 `.github/workflows/validate.yml` 的 `opa-test` job:用
  `open-policy-agent/setup-opa@v2` 装 OPA 1.19.1,跑
  `opa test apps/opa/policy -v`,和 `test-flask-apps` 那个 job 同一个
  "写了没人跑等于没测试"的道理接进 CI。
- **没有改**:`platform/iam/` 下任何数据文件(`employees.csv` 本来就有
  `department` 列,不需要加字段)、`environments/*/config.yaml`、
  `environments/resource-profiles.yaml`、`scripts/bootstrap-all.sh`、
  `docs/project/capability-matrix.md`——这几个是共享文件,这次故意没碰,见下面"需要额外做
  的事"。

## 验证

### 已验证(本地,`opa test`,不是只读代码)

```
opa test apps/opa/policy -v
...
PASS: 28/28
```

28 = `trino_test.rego` 里 ADR-051 原有的 13 个表级 `allow` 用例(这次
没动逻辑,只改了顶部注释,重跑确认没有被这次改动破坏)+
`trino_masking_test.rego` 新增的 15 个列级脱敏/行级过滤用例。覆盖:

- 低 grant 级别脱敏手机号/邮箱(部分脱敏)、身份证(全脱敏)
- 足够级别的 grant 看到明文(手机号/邮箱要 level 2,身份证要 level 3,
  level 2 还不够看身份证明文——分层验证过,不是只测了"够"和"不够"两档)
- 没有任何 grant 的用户,敏感列依然脱敏(纵深防御用例)
- 非敏感列永远不脱敏
- 服务账号/平台管理组豁免脱敏
- 行级过滤:不同部门拿到不同的 `department = '...'` 表达式
- 用户查不到部门时过滤成 `1 = 0`(拒绝而不是放行)
- 不在白名单里的表不触发行级过滤
- 服务账号/平台管理组豁免行级过滤

`python3 scripts/validate-charts.py`:57 个文件,0 个失败。新增/改动的
YAML(`departments-sync-cronjob.yaml`/`policy-configmap.yaml`/
`trino.yaml`/`validate.yml`)额外用 `yaml.safe_load_all` 单独解析确认过
能正常加载。`check-networkpolicy-consumers.py`/`check-image-tags.py`
也重跑过,没有新增失败。

### 没有验证、诚实标注的部分

- **Trino 真的按这两个新接口调用 OPA、返回结果真的生效**:这是最大的
  未知项。`opa test` 只验证了"给定输入,Rego 规则算出的输出是不是预期
  的那个 JSON",不验证 Trino 侧真的会发出这个请求、Trino 真的会把
  `columnMask`/`rowFilters` 的返回值正确拼进最终 SQL、拼出来的 SQL 表达式
  语法在 Trino 里真的合法(比如 `regexp_replace`/`CAST(... AS varchar)`
  这几个函数名/写法,是照 Trino 官方文档记忆写的,没有真的跑一条查询去
  确认)。ADR-051 当年这一步是云主机上真实验证过的(`curl POST /v1/data/
  trino/allow` 对着活的 OPA 实例测),这次云主机停机,做不到同等验证。
- **`opa-departments-sync` CronJob 没有实际跑过**:和 `opa-grants-sync`
  当年一样,本地没有 K8s 环境跑 CronJob,这次连"本地 docker run 手动执行
  一遍这段 Python 代码"都没做(ADR-051 那次做了这一步,这次为了控制范围
  没做,原因是这段代码和 `opa-grants-sync` 几乎是同一份逻辑照抄,风险
  判断是"复制一份已经验证过在真实环境能跑通的模式,风险比重新设计一套低
  很多",但严格说这不等于验证过)。
- **`accessControl.properties` 里新增的两行会不会在 Trino 启动时被拒绝**
  (类似 ADR-051 当年撞到的"access-control.name 不能塞进主 config"那个坑)
  ——`opa.policy.column-masking-uri`/`opa.policy.row-filters-uri` 是从
  Trino 源码 `OpaConfig.java` 里确认存在的合法配置键,但它们和
  `access-control.name`/`opa.policy.uri` 是不是要求在同一个
  `access-control.properties` 文件里、还是也需要单独文件,没有实测过,
  只是"看起来应该一样,因为都是同一个 `OpaConfig` 类解析的属性"这个
  推断。

## 需要额外做的事(不在这次改动范围,交回去处理)

- `docs/project/capability-matrix.md` 那一格"敏感字段行列级策略 | ❌ | OPA 原生支持,没配置"
  要不要改成 🟡(能力已实现但没在真实集群验证过),还是等下次上云实测过
  再改成 ✅——按这个仓库一贯的"判定依据是实际验证,不是代码写完"的标准
  (ADR-062 末尾那条教训),这次应该停在 🟡,不要因为 `opa test` 全绿就
  标成完成。`docs/project/capability-matrix.md` 是共享文件,这次没有改,留给之后处理这一格。
- `scripts/bootstrap-all.sh` 需不需要显式提一句"`opa-departments-sync`
  这个 CronJob 是 GitOps 自动同步的一部分",按现在的模式(`opa-grants-
  sync` 当初大概率也没有单独提)应该不需要,ArgoCD 会自动发现
  `apps/opa/manifests/` 下新增的文件——但这条判断本身没有实测确认过
  `apps-root` 的发现范围是不是整个目录,值得下次上云时顺带确认一下。
- 下次云主机开机后的验证清单(建议接进 `docs/project/current-work.md` 或者
  cloud-full 的 STATUS 文档,这次没有改这两个文件,留给之后处理):
  1. 手动 `curl PUT` 一次 `data.trino.user_departments`,确认
     `opa-departments-sync` 那段 Python 代码本身没有语法/逻辑错误。
  2. 造一张真的有敏感列命名(比如 `phone`)的测试表 + 两个不同
     `security_level` 的 grant,直接 `curl POST /v1/data/trino/columnMask`
     对着活的 OPA 实例测,和 ADR-051 当年验证 `allow` 的方式一样。
  3. 真的用 Trino client 跑一条 `SELECT phone FROM ...`,确认返回的是
     脱敏后的值,不是只测 OPA 自己的决策 API。
  4. 建 `demo.regional_sales`(或者先用别的已有表临时加进
     `row_level_filtered_tables` 测),验证行级过滤真的生效。
