# ADR-078:给 Trino 配 file group provider —— `is_platform_admin` 一直是摆设

日期:2026-08-26
状态:**已实现并实机验证通过**

## 这是怎么被发现的

在为 [ADR-074](074-superset-impersonation.md)(Superset 透传登录用户身份)
做上线前的"盘表"时顺出来的。盘表本身的结论很干净——Superset 里 22 个数据集
只有 1 个走 Trino,唯一登录过的用户是 `admin`,所以 impersonation 的影响面
极小。但顺着"那 admin 会被怎么判定"这个问题查下去,发现了下面这件事。

`kubectl exec` 进 Trino coordinator 看 `/etc/trino/`:**没有
`group-provider.properties`**。Trino 没有配任何 group provider,意味着它传给
OPA 的 `input.context.identity.groups` **永远是空数组**。

而 `apps/opa/policy/trino.rego` 里有这么一条:

```rego
is_platform_admin if { "platform-team" in input.context.identity.groups }
```

**它从来没有真正触发过。** "平台管理组不受表级授权约束、方便排障"这个口子
一直是个摆设,而且没人发现——因为:

- `opa test` 全过,但测试里的 `input` 是手写的、带着 groups;
- 真实请求里没有 groups,策略就落到后面按 grant 判断的分支,
  **行为看起来完全正常**(平台组的人也有 grant,或者干脆没人试过)。

**单元测试通过,恰恰掩盖了这个问题**:测试验证的是"给定这样的输入,策略
判断对不对",而真实缺陷在"这样的输入根本不会出现"。

## 为什么它现在从"摆设"变成"硬伤"

ADR-074 打开 impersonation 之后,Trino 看到的是真实的人而不是那个无条件
放行的服务账号。如果 Trino 不知道这个人属于哪个组,**platform-team 的人也会
被当成普通用户拦下**——正好和使用方 那句"admin 应该有全权限"相反。

也就是说:不修这条,ADR-074 一上线就会砸到管理员自己头上。

## 决策

用 Trino 的 **file group provider**,组成员数据从
`platform/iam/memberships.csv` 生成——复用同一份组织结构,不新建一套。
这个仓库在权限、审批、Keycloak 同步、Kueue 队列上用的都是这一份
([ADR-064](064-role-based-resource-quota.md) 里解释过为什么不能各搞各的)。

`scripts/sync-trino-groups-configmap.py` 负责生成,CI 用 `--check` 拦漂移
(和 `sync-airflow-dags-configmap.py` 同一个模式)。

### 挂载方式被实测逼着改过一次

第一版把 `group-provider.properties` 和 `group.txt` 都用 `configMounts`
挂进 `/etc/trino`,coordinator 直接 CrashLoopBackOff:

```
error mounting ... to rootfs at "/etc/trino/group.txt":
not a directory: Are you trying to mount a directory onto a file
```

根因:**`/etc/trino` 本身就是 chart 挂上去的一个 ConfigMap 卷**,不能再往它
里面 subPath 塞文件。改成两边分开:

- `group-provider.properties`(静态三行)走 chart 原生的
  `coordinator.additionalConfigFiles`,由 chart 塞进它自己那个配置 ConfigMap;
- `group.txt`(从 CSV 生成)挂到 `/etc/trino-groups/` 这个**独立目录**。

(整个过程 Trino 服务没有中断——滚动更新时老 Pod 一直在跑。给 fail-closed
的组件配好 rollout 策略,这个月已经兜住第二次了。)

### 中间还犯过一个更隐蔽的错

改 values 时直接新写了一个 `coordinator:` 块,而里面已经有一个(带
jvm/config/resources)。**YAML 重复键后面覆盖前面,`additionalConfigFiles`
被静默丢掉**——渲染不报错、`--check` 也不报错,是核对渲染产物里
`coordinator` 有哪些键时才发现的。

## 实机验证(cloud-full,2026-08-26)

临时给 `zhenghe`(`memberships.csv` 里属于 platform-team)加一个 Trino 密码,
验完删除:

| 查询 | 结果 |
|---|---|
| `select count(*) from iceberg.audit.query_events` —— **只有 platform-team 能读的审计表** | ✅ 允许,2062 行 |
| `select count(*) from iceberg.demo.regional_sales` —— 没给他发过任何 grant,而且这张表有行级过滤 | ✅ 允许,**6 行(全部)** |

第二行的"6 行"是关键:`analyst001` 查同一张表只能看到自己部门的 2 行
(见 ADR-063 的验证)。管理员拿到 6 行,说明 `is_platform_admin` 让他
**同时绕过了 grant 检查和行级过滤**——这正是"全权限"该有的样子,而在这次
改动之前它一次都没生效过。

## 后续

- `memberships.csv` 目前只有 2 行(admin / 使用方,都在 platform-team)。
  其它三个组(data-analysts / algorithm-team / viewers)**在这份 CSV 里一个
  成员都没有**——Keycloak 那边可能有,但 Trino 这条路上看不到。真要用起来
  得把成员补全,否则那些组的人在 Trino 里同样是"无组"状态。
- 组文件 30 秒刷新一次,改完 CSV 同步之后不用重启 Trino,但要等 kubelet
  把 ConfigMap 更新同步进容器(约 1 分钟)。
