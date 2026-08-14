# 044. 权限 OA 分级审批工作流(权限 OA 审批系统 Phase 2)

- 状态: 已采纳,已实现,端到端验证见"验证"一节(2026-08-14)

## 背景

[ADR-040](040-enterprise-governance-roadmap.md) 归档的 7 条企业治理需求里,
需求 2"权限管理:分级审批"当时的判断是"基本要自己写,是最重的一块",建议
"等平台核心稳定后单独立项"。[ADR-043](043-table-registration-tool.md)
(建表注册工具,Phase 1)已经把这条链路的前置依赖做完了——表的安全等级
(1/2/3)和负责人现在会被强制录入并回写进 OpenMetadata。这份 ADR 是 Phase 2:
真正按安全等级路由的分级审批本身。

用户在 2026-08-14 拍板,同时明确要求这批治理/易用性功能要做到"生产级别、
好用易用、方便运维管理"。

## 决策

### 职级数据:虚拟占位,标准 HR 导出表结构

调研确认(见对应会话记录,不重复展开):这个平台现在**完全没有**"职级/
汇报线"这个维度的数据源——`platform/iam/` 现有的角色/组模型是"职能角色"
(data-analyst/algorithm-team 这类),和"谁是谁的上级"是两个不同的东西;
Keycloak 用户对象上也没有任何相关 attribute。根源是 [ADR-028](028-iam-org-model.md)
写的"公司没有统一 HR 系统对接",`memberships.csv` 本来就是过渡方案。

用户拍板:先用一份虚拟的、按通用 HR 系统导出表结构设计的数据占位
(`platform/iam/employees.csv`:`employee_id,username,name,email,department,
title,manager_id`,`manager_id` 自关联指向上级),不是发明一个"职级等级"
枚举——正常公司的人事数据本来就有"上级是谁"这个字段,"申请人职级+1"就是
"申请人的直属上级","+2"就是"上级的上级",这个结构不需要真的知道每个人
具体是 P6 还是 P7,只需要知道汇报线。以后公司真实 HR 数据接入时,只要
保持这几列的列名一致,直接换文件内容,`load_employees()`/
`get_manager_chain()`(见 `apps/permission-request-app/src/app.py`)不用
改一行代码。

### 不新建独立服务,扩展 `apps/permission-request-app/`

评估过和 ADR-043 类似的"独立组件"方案,但这次判断相反:ADR-043 的建表
登记和权限申请是两个不同的治理动作、依赖也不同(Trino+OpenMetadata vs
git),值得拆开;而分级审批和已有的"组权限自助申请"本质上是同一类动作
(申请 -> 审批 -> 生效),用户在 ADR-040 补充信息里也明确说过希望"自建
前端把各种申请规范起来",拆两个门户不符合这个方向。复用现有的 SSO/
oauth2-proxy 接入和"审批通过后写回 git、由 iam-sync 之类的机制消费"这套
骨架,只是新增数据表和路由逻辑,不是另起一个 Deployment。

同一个 ConfigMap(`permission-request-app-src`)现在挂两个文件:`app.py`
(代码)和 `employees.csv`(组织架构数据,和 `platform/iam/employees.csv`
保持同步,同一个"改代码要同时改两个地方"的既有模式)。

### 数据模型:`table_access_requests` + `approval_steps`,按 step_order 顺序推进

不复用现有的 `requests` 表(那张表是"申请人 -> 单一审批人"的一跳模型,
字段语义也是"申请哪个组",硬塞进去会两边都不干净)。新增两张表:

- `table_access_requests`:申请本身(谁申请、申请哪张表、这张表的安全等级、
  谁是表负责人、总体状态)。
- `approval_steps`:一个申请对应多行,每行是"第几级、谁审、状态"。**状态机
  是"前一级(`step_order` 更小的)全部 `approved` 才轮到下一级"**,不是
  谁都能审——`current_step_order()` 查"这个申请里 `status='pending'` 的
  最小 `step_order`",审批人只能看到、只能操作自己在那个当前级别里的行
  (`approve_table_step`/`reject_table_step` 会校验这两点,直接 POST 绕过
  前端也拦得住)。

路由规则严格按 ADR-040 原文(不是 2026-08-13 复述时失真的版本):
- L1:直属上级 + 表负责人
- L2:L1 全部 + 上级的上级(叠加,不是只换成 +2)
- L3:L2 全部 + 指定管理员(`DESIGNATED_ADMIN` 环境变量,不是从组织架构
  推导的——这个人本来就是"指定的",不是算出来的)

同一个人在同一级出现多次(比如直属上级正好也是表负责人)只记一行,不用
同一个人对同一级批两次。

### 范围边界:只做决策与留痕,不做真正的访问拦截

全部审批通过后,会把这条授权写进 `platform/iam/table-access-grants.csv`
(和 `apply_to_git()` 同一套 clone/改文件/commit/push 模式)。**这份文件
现在没有任何东西去读它做真正的强制执行**——Trino 细粒度权限(行/列级)
是 [ADR-028](028-iam-org-model.md)"后续"里的独立课题,现在完全没有 OPA/
access-control 配置,没批准也一样能连 Trino 查到数据。这次刻意不假装
做到了实际做不到的事,`table-access-grants.csv` 现在的作用是"审批决策的
真实记录",是给以后接 Trino OPA 时用的数据源,不是马上生效的门禁。

## 涉及的文件

- 新增 `platform/iam/employees.csv`(虚拟组织架构占位数据)
- 改 `apps/permission-request-app/src/app.py` +
  `apps/permission-request-app/manifests/app-configmap.yaml`(两份保持同步)
- 改 `apps/permission-request-app/manifests/deployment.yaml`(新增
  `OPENMETADATA_URL`/`OPENMETADATA_TOKEN`/`DESIGNATED_ADMIN` 环境变量,
  `OPENMETADATA_TOKEN` 复用 `table-registration-app-openmetadata` 那个
  bot 的 token 值,在 `permission-request-app` 这个命名空间下单独建一份
  同值的 Secret)
- 新增 `platform/iam/table-access-grants.csv`(运行时由 app 写入,不是
  这次手工建的)

## 后续(明确不在这次范围内)

- Trino OPA 真正消费 `table-access-grants.csv` 做行/列级拦截——依赖这次
  的数据存在,但引擎本身是独立工作。
- 权限交接、资源回收——是这套系统之上的子功能,等审批链跑稳后再展开。
- `employees.csv` 换成公司真实 HR 数据——依赖公司 HR 系统对接,不由这个
  项目单方面推进。

## 验证

见 `docs/operations/troubleshooting.md`(如果验证中发现真实坑会补进去)。
真实注册测试表(L1/L2/L3 各一次)→ 提交表访问申请 → 核对生成的
`approval_steps` 行数和身份符合规则 → 走一遍能真实登录的账号的审批步骤
→ 核对全部批准后 `table-access-grants.csv` 正确写回。组织架构数据只有
`zhenghe`/`admin` 两个真实可登录账号,其余是纯占位、无法登录测试,这次
端到端验证覆盖到"能被真实账号操作到的那一段",其余部分靠直接检查数据库
和调用内部函数验证路由逻辑本身正确,不是回避,是账号数量的客观限制,
见验证记录里的具体说明。
