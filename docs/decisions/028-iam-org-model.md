# 028. 组织架构/角色数据模型 + Keycloak Group 同步

- 状态: 已采纳,已验证(2026-08-11:group/role/成员同步进 Keycloak 并做完整
  对账测试过加/减两个方向;真实拿到 token 确认 `groups` claim 出现在里面;
  ArgoCD/Grafana 已改成按 group 映射角色)

## 背景

之前 ArgoCD/Grafana 的 RBAC 是"登进来就是管理员"(ADR-009 的简化处理),
只有一个受信用户在用的阶段没问题。现在要接入团队/公司角色体系,这条路
走不下去了,需要一个真正的组织架构/角色模型。

公司目前没有统一的 AD/LDAP,人事数据在 HR 自己的系统/数据库里,还没有对外
的同步接口。这意味着 Keycloak 短期内没法通过"联邦到公司现有目录服务"这条
标准路径(见下面"和 HR 系统对接"),得先有一个能跑起来的方案。

## 决策

### 数据放哪:三个文件,按变动频率拆分

- `platform/iam/roles.yaml`——角色定义(名字 + 说明),改动很少,用 YAML
  合理。
- `platform/iam/groups.yaml`——组定义(组叫什么、对应哪些角色),同样低频,
  YAML。
- `platform/iam/memberships.csv`——"用户属于哪个组"这张关系表,高频变动
  (人员入职/调组/离职),纯表格,一行一条记录(`username,group`)。选 CSV
  不选嵌套 YAML,是因为这个数据本质是表格,嵌套结构反而不利于批量增删,
  而且以后从 HR 系统批量导入天然就是表格格式,不是配置格式。

一开始整份数据是塞进一个 `org.yaml`(嵌套结构)里的,写完同步脚本测试时
发现这个问题才拆开——记录下来是因为这是一个真实想岔了又改回来的过程,不是
一次到位的设计。

**没有存进数据库**:这个项目从 Phase 0 就定的铁律是"机器状态 = Git 状态"
(ADR-005/006),数据库里的一行变更不会出现在 git diff/log 里,会破坏这条
原则,所以哪怕这份数据本质是"表格",也还是选了"进 git 的 CSV",不是"进
Postgres 的表"。

### 同步进 Keycloak:`scripts/12-sync-iam.py`

命令式脚本(调 `kcadm.sh`),和 `scripts/03-configure-keycloak.sh` 是同一个
"Keycloak 没有声明式管理方案,只能用脚本模拟"的原因(ADR-009)。

**关键设计:声明式对账,不是只加不减**。group 的 role-mapping 和 group 的
成员都做完整 diff(读 Keycloak 现状 vs 读文件期望状态,多的删、少的加)。
访问控制系统如果只加不减,人离职/调组之后权限会一直留着,是真实的安全
问题——这个必须做对,不能图脚本简单。已经用真实数据测试过移除路径:把
`zhenghe` 从 `memberships.csv` 里删掉重跑,确认真的从 Keycloak group 里
移除了,加回去重跑也确认恢复。

role 的定义本身**不做自动删除**——删掉一个还在被其他地方引用的角色定义,
影响面代码里看不出来,这一步留给人手动做,和 group role-mapping/成员这种
"关系"层面的对账不是同一类风险。

### groups claim 怎么进 token

Keycloak 默认不会把 group 归属放进 token,要显式加一个
`oidc-group-membership-mapper` 类型的 protocol mapper。加在 "roles" 这个
**默认(default,不是 optional)client scope** 上,而不是给每个组件的
client 单独配一遍——"roles" 这个 scope 本来就是新建 client 自动带的,加在
这一层,ArgoCD/Grafana/Trino/...所有组件不用改 client 配置就自动拿到
`groups` claim。用真实 token 验证过(临时开了 grafana client 的
`directAccessGrantsEnabled` 拿到一个 password-grant token,解码后确认
`"groups":["platform-team"]` 真的在里面,验证完改回去了)。

### ArgoCD / Grafana 的 RBAC 接入

- **ArgoCD**:`policy.default: role:admin` 改成 `policy.csv`
  按 group 映射(`platform-team` → `role:admin`,其他组 →
  `role:readonly`)。`policy.default` 故意留空,不设兜底角色——不在任何
  已知 group 里的人登进来是零权限,报错让人来问,而不是意外获得管理权限。
  ArgoCD 默认就读 token 的 `groups` claim,不需要额外配置指定 claim 名字。
- **Grafana**:`role_attribute_path` 从写死的 `"'Admin'"` 改成
  JMESPath 表达式,按 group 分 Admin/Editor/Viewer。这里**没有**采用
  ArgoCD 那种"零权限报错"的姿态,没匹配到任何已知 group 的人落到
  Viewer——因为 Grafana 看板本身信息敏感度比 ArgoCD(能直接改集群状态)
  低得多,给个只读兜底比强制报错更实用,两个组件按各自的风险等级分别
  决定,不是不一致。

Trino 的细粒度权限(表级/行列级)不在这次范围内,见下面"后续"。

## 顺带踩到的坑:firstName/lastName 是隐藏必填项

测试新建的 `zhenghe` 账号时,发现登录报 `invalid_grant: Account is not
fully set up`,错误信息完全看不出和什么有关。查 Keycloak 事件日志才看到
真实原因:`error="resolve_required_actions"`。对比 `admin` 账号(能正常
登录)和 `zhenghe` 账号(登不了)的完整用户对象,唯一区别是 `admin` 有
`firstName`/`lastName`,`zhenghe` 没有——Keycloak 的 User Profile 校验把
这两个字段标成必填,账号缺了就在登录时动态判定"需要补资料",非交互式的
password grant 没法处理这种情况,直接拒绝,而且不会体现在这个用户自己的
`requiredActions` 字段里(是登录时动态算出来的,不是存量属性)。

`admin` 账号没踩到这个坑,不是因为它有什么特殊配置,是因为它很早就通过
浏览器登录时被 Keycloak 提示"更新个人资料"、人工填过一次——纯粹运气,
新建的账号不会有这个补救机会。

`scripts/03-configure-keycloak.sh`(建初始用户)和
`scripts/12-sync-iam.py`(建 group 成员对应的用户)现在都在建号时显式带上
`firstName`/`lastName`(先用用户名本身占位,不是真实姓名,只是让账号一开
就能登录;真实姓名以后从 HR 数据接进来时再补)。

## 和 HR 系统对接(2026-08-11,记录讨论结论,还没做)

公司人事数据在 HR 自己的数据库里,标准做法不是让各个应用直连 HR 生产库
(安全和部门边界都不允许),而是:HR 数据 -> 某种同步机制(常见是 SCIM
协议,或者定期导出)-> 一个统一目录服务(通常是 AD/Azure AD)-> 各应用对接
这个目录服务,不直接对接 HR。公司现在没有这个统一目录服务这一层。

现在这套 `memberships.csv` 手动维护的方案,定位是**HR 系统真正接进来之前
的过渡方案,同时也把"下游怎么消费这份数据"的接口先定好**——等确认了 HR
那边能给到什么(导出文件?API?)之后,要做的是写一个转换器把 HR 的数据
格式转成这三个文件的形状(或者直接改 `scripts/12-sync-iam.py` 读 HR 的
数据源),同步进 Keycloak 那部分逻辑不用大改。

## 后续(有意留白,不在这次范围内)

- Trino 细粒度权限(行级/列级):倾向于用 Trino 原生的 OPA 授权插件,不用
  Ranger——Ranger 官方(Apache 项目本身)没有维护官方 Helm chart,只有
  社区/第三方仓库,不满足这个项目"只用官方支持的部署方式"的门槛;Trino
  自己的 OPA access control 插件是 Trino 官方原生支持的(文档:
  trino.io/docs/current/security/opa-access-control.html),而且支持行
  过滤和列脱敏,不一定需要 Ranger 才能做到。**更正(2026-08-15,ADR-051
  落地时查证)**:这里原来写的"OPA 有官方 chart"是不准确的
  ——OPA 项目本身**没有**官方维护的 Helm chart(`open-policy-agent/opa`
  仓库里有一个长期开着、还没关的 issue #7109 就是在要这个东西)。这条不
  影响这里的结论(选 OPA 不选 Ranger 的关键理由是"Trino 原生支持",不是
  "有没有 chart"),但既然写错了就要更正,不能放着不管;OPA 的实际部署
  方式见 ADR-051(原生 K8s manifest + OPA 官方 Docker 镜像,不需要
  chart)。设计留到真正要做的时候展开。
- JupyterHub/MLflow/Argo Workflows/KServe 目前还是 `allow_all`/无门禁,
  没有按 group 收紧——JupyterHub 的 `GenericOAuthenticator` 原生支持
  `allowed_groups`,以后可以直接接,这次没做是不想在刚修好 SSO 登录之后
  又立刻改一次授权逻辑,增加新的回归风险。
- 自助权限申请门户(降低"改 CSV 发 PR"这个操作对非技术人员的门槛):还在
  调研阶段,候选是 Backstage 一类的开发者门户工具,评估规模是否匹配还没
  做。
