# ADR-084:分析师的浏览器 SQL 入口 —— 复用 Superset SQL Lab,不引入新组件

日期:2026-08-29
状态:**已决策,实现中(SQL Lab 本身待实机验证)**

## 问题

外部评审(Codex,2026-08-29)指出:门户把 **Trino Web UI 当 SQL 工作台介绍**,
而 Trino 的那个界面**只是查询监控**——它能看到集群里正在跑什么查询、每个查询
的执行计划和耗时,但**没有 SQL 编辑器**,不能执行查询,不能看自己的历史,
不能下载结果。

这不是"体验不好",是**门户上写的东西和它实际是什么不符**。一个分析师照着
门户点进去,会发现自己无处输入 SQL。

## 验收条件(来自 roadmap P1.5,不改)

分析师能在浏览器里:编辑 SQL、执行、看历史、下载结果、看到可读的错误说明,
并能**从数据目录里的一张表一键跳到查询**。

## 候选方案

| 方案 | 怎么做 | 结论 |
|---|---|---|
| **A. 复用 Superset SQL Lab** | Superset 已部署、已接 Keycloak SSO,SQL Lab 是它自带的模块 | **选这个** |
| B. 引入专门的 SQL 客户端(Querybook / Redash / CloudBeaver) | 新增一个常驻组件,自己接 SSO、自己配 Trino 连接 | 见下 |
| C. 自建一个 SQL 页面 | 在门户里写编辑器 + 执行 + 历史 + 导出 | 见下 |

### 为什么是 A

**四件本来最麻烦的事,已经是通的:**

1. **SSO** —— Superset 已经接 Keycloak(OIDC),分析师不用再记一套账号。
2. **角色** —— 2026-08-29 刚在跑着的实例上验过:`data-analysts` 组登录后拿到
   `['Alpha','Gamma','sql_lab']`,`algorithm-team` 拿到 `['Gamma','sql_lab']`,
   **不在任何组的人拿到 `['Gamma']`(没有 `sql_lab`)**。也就是说"谁能用 SQL
   工作台"这件事**已经由 Keycloak 的组决定了**,不需要再设计一套授权。
3. **数据权限** —— Superset 连 Trino 走 impersonation(ADR-074),查询是按
   **登录用户**算权限的,不是按一个共享服务账号。这条是 B 和 C 都要从头做的。
4. **编辑/执行/历史/导出 CSV/可读错误** —— SQL Lab 自带,不用写。

**B 的代价**:多一个常驻组件要运维、升级、备份,还要把上面 1–4 全部重做一遍
——尤其是 impersonation,那是这个平台花了最多力气才走通的一段(踩过"加 header
不生效而且不报错"的坑)。在**已有组件就能满足验收条件**的前提下,这个代价
换不到东西。

**C 的代价**:更大,而且是在重造一个成熟软件。这个仓库的既定原则是"能不新增
常驻组件就不新增"、"能复用就不自建",C 两条都违反。

### 退出方案(选 A 不是不可逆的)

如果以后 SQL Lab 撑不住(比如需要更强的协作/版本化/调度化的查询管理),迁走
的成本主要是"用户习惯"和"存下来的 saved query",不是架构耦合:分析师访问的
是一个 URL,平台侧给的是**入口 + 深链**这两件事。把入口指向别处即可,Trino、
权限、身份这三层都不用动。**这也是选 A 的一部分理由——它没有把我们锁死。**

## 「从数据目录一键跳到查询」怎么实现

这是 A 唯一需要自己写的部分。

**不要猜 query string。** Superset 6.1 的 SQL Lab 路由(`superset/views/sqllab.py`
里的 `SqllabView.root`)**只读 POST 的 `form_data`**,不读 URL 参数——想靠
`?db=x&schema=y&table=z` 拼一个链接出来,是行不通的(会静默地开一个空编辑器,
不报错,又是一个"看起来对但没生效")。

正确的机制是 **SQL Lab permalink**,它是有 schema 的公开 API:

```
POST /api/v1/sqllab/permalink
{"dbId": <int>, "catalog": <str>, "schema": <str>, "sql": <str>,
 "name": <str>, "autorun": <bool>}
→ 201 {"key": "...", "url": "/sqllab/p/<key>/"}
```

字段名取自 `superset/sqllab/permalink/schemas.py`(6.1.0),**注意是 `dbId`
这种驼峰,不是 `db_id`**。

所以深链是两步:先调 permalink API 拿到 key,再把用户送到 `/sqllab/p/<key>/`。

**这段逻辑放门户,不放 platform-sdk。** SDK 的边界(ADR-058)白纸黑字写着
"只做连接封装和作业提交,任何顺手加个功能默认拒绝",SQL 工作台的深链不属于
这两件事。放门户还有一个额外好处:数据目录那边只要拼
`portal/query/<catalog>/<schema>/<table>`,**完全不用知道 Superset 的存在**
——permalink 怎么造、字段是驼峰还是下划线、以后换不换 SQL 工作台,都关在
门户这一层里。上面那条"退出方案"能成立,靠的就是这个收口。

**权限不因此被绕过**:造 permalink 用的是平台的 Superset 服务账号,但
permalink 里存的只是"编辑器预填什么",不是数据。用户打开
`/sqllab/p/<key>/` 时走的是他自己的 SSO 会话,查询由 Trino 按他本人的身份
执行。服务账号能造出一个查 X 表的链接,不代表点链接的人查得动 X 表。

## 现在做了什么 / 还没做什么

**已做**:

- 门户把 Trino 那张卡的说明改成它真实的样子(查询引擎 + 执行监控),新增一张
  「SQL 工作台」卡直接指向 Superset SQL Lab。
- 门户新增 `/query/<catalog>/<schema>/<table>`,给数据目录一个稳定的落脚点;
  `apps/platform-portal/src/sqllab.py` 负责造 permalink。
- **凭据没配时降级成空的 SQL Lab,不报错** —— local-lite 上本来就不会配,
  这条路径必须是 302 不是 500,有测试锁住。
- 顺带修了一个只会在 cloud-full 暴露的拼接顺序 bug:端口后缀要插在 host 和
  path 之间,先拼 path 再插端口会得到 `http://superset.x/sqllab/:32460`。
  local-lite 没有端口后缀,测不出来 —— 和 2026-08-16 那次"门户上点哪个链接
  都 404"是同一类。

**还没验**:SQL Lab 里的 Trino 连接能不能真的按登录用户跑起来(Superset 的
**看板**路径已经验过 impersonation,SQL Lab 走的是同一个 database 连接,
**但没有单独验过**)。云主机当前是关的,下次开机要验:用 `analyst001` 登录 →
SQL Lab → `SELECT current_user` 应该是 `analyst001` 而不是 `superset_service`,
且查一张他没有 grant 的表应该被拒。**在这条验过之前,capability-matrix 里
这一格不能标成绿。**

**还没做**:OpenMetadata 表详情页上的跳转按钮(要改 OM 的前端配置,单独一件事)。
