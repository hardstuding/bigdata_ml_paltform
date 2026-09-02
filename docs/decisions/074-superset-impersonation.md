# ADR-074:Superset 改成透传登录用户身份(impersonation)

日期:2026-08-26
状态:**已上线并实机验证**(2026-08-27);盘表已做,影响面确认极小

## 起因

2026-08-26 我在讨论"审计表的汇总视图"时说了一句:审计表加了 OPA 保护之后
`superset_service` 读不到,所以没法用 Superset 做看板。zhenghe 的回应是:

> 这个是权限没设计好吧,admin 应该有全权限

他是对的,而且问题比"admin 看不了审计表"严重得多。

## 真正的问题:Superset 那条路上,权限控制基本是空的

Superset 用**一个共享的** `superset_service` 账号连 Trino
(`scripts/06-configure-superset-datasources.sh`)。于是 Trino 看到的永远是
这个服务账号,而服务账号在 OPA 策略里是无条件放行的。两个后果:

1. **任何能在 Superset 里建一个查询的人,能读到 Trino 上的任何表** ——
   不管他有没有 `platform/iam/table-access-grants.csv` 里的授权。
2. **列级脱敏和行级过滤([ADR-063](063-trino-column-row-level-security.md))
   在 Superset 上完全不生效** —— `is_exempt_from_masking` 对服务账号豁免。

也就是说:这个平台在权限上最大的一笔投入(分级审批 + 列级脱敏 + 行级过滤,
前两天刚在真集群上逐条验过),**恰恰在用得最多的那条 BI 路径上是空的**。

## 当初为什么是共享账号,以及那个理由错在哪

不是疏忽,是 [ADR-051](051-trino-opa-access-control.md) 明确选的,策略注释里也写了理由:

> 看谁能看哪个看板是 Superset 自己的 RBAC 管,不是靠 Trino 按人头发 grant;
> 如果按人头发,以后每加一个 Superset 数据源都要手动补 grant,和 Superset
> 自己的权限模型重复维护。

这个理由本身站得住 —— **代价没算清楚**。它成立的前提是"Superset RBAC 管住
了谁能看哪个看板",但 Superset 里能建 chart 的人(数据分析师本来就要能建)
可以绕开任何看板直接写 SQL。而且它默认了"Trino 层的权限和 Superset 层的
权限是重复的",实际上两者管的不是一回事:Superset 管**看板可见性**,Trino
管**数据可见性**,后者才是脱敏和行过滤生效的地方。

## 决策:打开 impersonation

- Superset 侧:Database 的 `impersonate_user = True`,把登录用户(Keycloak
  SSO 认证过的)透传给 Trino。
- Trino/OPA 侧:新增一条规则,只允许 `superset_service` 这一个账号执行
  `ImpersonateUser`。

之后 Trino 看到的是真实的人,所有按人判断的规则自然生效:grant、列级脱敏、
行级过滤,以及 `is_platform_admin` —— **使用方那句"admin 应该有全权限"
就是靠这条成立的**,platform-team 的人在 Superset 里以自己的身份查,照常
全放行,审计表的看板也就做得出来了。

`resource` 的字段路径是从 Trino 源码核实的,不是猜的:
`plugin/trino-opa/.../OpaAccessControl.java` 的 `checkCanImpersonateUser`
构造 `OpaQueryInputResource.builder().user(new TrinoUser(userName))`,而
`TrinoUser` 是 `record TrinoUser(String user, ...)` —— 所以是
`input.action.resource.user.user`。

代理请求**单独走一条规则**,不吃"服务账号无条件放行"那条:否则以后往
豁免名单里加服务账号时,会以为自己没开代理权限、实际上早就开了。

`opa test` 39/39,其中 4 条是这次新加的,包括一条"代理之后列级脱敏对这个
真实用户仍然生效"——那才是这次改动要的效果。

## 后果:这是一次行为收紧,和 ADR-051 同一个性质

**之前能在 Superset 里查的表,现在没有对应 grant 就查不到了。** 上线前必须
照 ADR-051 的做法先盘一遍"实际在用的表",给对应的人补 grant,否则一上去
就是一片看板报 `PERMISSION_DENIED`。

这次没有立刻在集群上生效,就是为了先把这一步做完。

## 实机验证(cloud-full,2026-08-27)

**盘表结论**(`scripts/40`):Superset 里 22 个数据集,**21 个来自内置的
`examples` 库(Postgres,不走 Trino,完全不受影响)**,只有 1 个走 Trino
(`Trino.demo.orders`,1 个 chart);登录过的用户只有 `admin` 一个。所以这次
切换的影响面是 1 个图表 + 1 个用户 —— 远比预想的小,可以直接切。

切换后在 Superset 里以真实用户身份跑查询:

| | 传给 Trino 的身份 | `demo.orders` | 审计表 |
|---|---|---|---|
| `admin`(platform-team) | `admin` | ✅ 10 行 | ✅ 3068 行 |
| `analyst001`(无 grant,非 platform-team) | `analyst001` | ❌ PERMISSION_DENIED | ❌ PERMISSION_DENIED |

第一行的 `select current_user` 返回 `admin` —— **Superset 传的确实是登录用户
本人,不再是 `superset_service`**。

第二行是这次改动的全部意义:**改之前 `analyst001` 通过 Superset 能读到任何
表**(因为一切都走那个无条件放行的服务账号),现在两张表都被拒。
(验证用的临时 Superset 用户已删除。)

而 `admin` 那一行能通过,**完全依赖 [ADR-078](078-trino-group-provider.md)
的 group provider**:没有它 Trino 不知道 admin 属于 platform-team,他会和
analyst001 一样被拒 —— 那就变成"堵了洞、顺便把管理员也锁在外面"。两个 ADR
是一起才成立的。

## 还没做的

1. **没部署验证。** 要验三件事:Superset 里以普通用户查会不会被脱敏、
   platform-team 查审计表能不能通、没有 grant 的表是不是真的被拒。
2. **没盘表。** 见上面"后果"。盘表的脚本已经写好了:
   [`scripts/40-audit-superset-tables.sh`](../../scripts/40-audit-superset-tables.sh)
   ——直接查 Superset 自己的元数据库,列出数据集实际引用的表 + 登录过的
   用户,差集就是切换前要补的 grant。**这一步没做完就跑 `scripts/06`,
   会让一批看板同时挂掉。**
3. 审计表的 Superset 看板本身还没建——这条改动只是让它**成为可能**。
