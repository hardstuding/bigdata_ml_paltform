# 下次开机要验的清单

> **先跑这个,再手点剩下的:**
>
> ```bash
> ./scripts/46-verify-p15.sh
> ```
>
> 它把下面能自动化的部分跑一遍,一条一条报 ✅/❌,最后给汇总,日志在
> `logs/verify-p15.log`。**全部跳过会退出码 2,不会被当成通过** —— 一个
> "什么都没验"的运行报告成成功,是这个项目栽过四次的那个模式。
>
> 脚本**验不了**的:用两个真实账号验越权、组权限申请的批准按钮、作业详情页
> 的外观。
>
> **SQL Lab 那条原本以为要人点,后来发现不用** —— SQL Lab 用的就是
> `Database.get_sqla_engine()` 这条路,在 pod 里用 Superset 自己的
> `override_user` 把身份放进去走的是同一份代码。`./scripts/46-verify-p15.sh
> sqllab` 会验四条:current_user 是本人、有 grant 的能查、没 grant 的被拒、
> 脱敏生效。
>
> ⚠ **别用 `flask_login.login_user` 写这类测试** —— 2026-08-30 第一次就是
> 这么写的,`current_user` 返回 `superset_service`,差点当成"impersonation
> 坏了"报出去。Superset 读的不是 `login_user` 设的那个地方。

> 这份单独成文,是因为它在 [`current-work.md`](current-work.md) 里已经涨到
> 60 多行,把"现在的主线是什么"挤到看不见了 —— 而那份文件的规则是超过
> ~150 行就说明又在写日记。
>
> **每条都写死了判据和"失败长什么样"。** 后者尤其重要:这一批改动里好几处
> 的失败是**静默的**(token 没对上首页只是空着、impersonation 没生效只是
> 悄悄用了服务账号权限),不写清楚下次就会被当成"功能没做"或者"看起来没
> 问题"。


**1. SQL Lab 的 impersonation** —— ADR-084 唯一没验的一环:

```
analyst001 登录 → SQL Lab → SELECT current_user
  期望:analyst001,不是 superset_service
analyst001 → 查一张他没有 grant 的表
  期望:被拒
```

**2. 门户的角色工作台**(需要先跑一次 `scripts/00-generate-secrets.sh`
把 token 复制到 platform-portal 命名空间):

```
alice 登录门户 → 首页应出现「我的表权限」,快到期的排最前、标黄
审批人登录     → 额外出现「待我审批」,显示已等多久
两块都空着     → 多半是 token 没对上(各生成了一份而不是复制)
                 而这个失败是静默的,不会报错
```

**3. `internal-packages` 的定时发布路径** —— 手工触发验过,CronJob 按点
触发从没观察到过。

**4. 审批体验那一批**(纯 UI/逻辑改动,本地 95 个测试全绿,但没在真实
浏览器里看过):

```
页面上时间显示成本地时区、3 天内是"N 小时前"  ← JS 没跑起来的话会退回
                                                显示原始 UTC 串,不会报错
拒绝一条申请不填原因 → 应该被挡住
2 级表不写理由提交   → 应该被挡住
催办按钮 → 企微收到一条;24 小时内再点 → 429
```

**5. 门户「SQL 工作台」那张卡的地址对不对** —— 端口后缀要在 path 前面
(`…superset.<域名>:32460/sqllab/`),拼反了在 local-lite 上测不出来。

**6. 建表工具的对账 CronJob**(要先跑 `scripts/00-generate-secrets.sh` 建
`table-registration-app-internal`):

```
故意让一条登记停在 openmetadata_status=failed
→ 等 30 分钟那轮 CronJob,或手工 POST /internal/reconcile-openmetadata
→ 该行变成 ok,GET /internal/reconcile-status 的 pending 归零
```

**7. 提权路径确认已堵**(这条最值得亲手走一遍):

```
用普通账号建一张表 → 看 OpenMetadata 里的 owner 是不是登录者本人
                     (而不是表单里能填的任何人)
再用同一个账号申请这张表的权限
→ 期望:审批链里没有他自己;如果剔完没有审批人了,申请应该被**拒绝**,
        note 里写着"自己不能批自己",而不是被放行
```

验过了才能把 capability-matrix 里对应那格改掉 ——
`scripts/check-capability-matrix.py` 会拦住"没验就标 ✅"。


**8. 作业详情页 + 取消/重跑**(这条要亲手点,mock 测不出 RBAC 对不对):

```
在门户「我的作业」点一个作业名 → 详情页打开
  失败的作业:最上面应该是失败原因,不是参数
  点开某一步 → 日志加载出来(按需拉,不是一次全拉)
点「重跑」→ 跳到一个新的作业名,原来那个不动
跑着的作业点「取消」→ 变成 Failed/Error,**记录和日志还在**(不是消失)

RBAC 不对的话表现是 403,页面上是"取消失败:ApiException" —— 需要
`kubectl apply` 过新的 rbac.yaml(patch/create/pods/log 是这次新加的)
```

**越权那条要单独验一次**(用两个账号):

```
用 A 的账号访问 B 提交的作业 /job/<B的作业名>
→ 期望 404「找不到这个作业,或者它不是你提交的」
→ 不能出现 B 的作业内容,也不能出现"存在但无权"这种能用来探测名字的措辞
```

---

## 这一批最要紧的一条:groups 到底有没有传过来

上面好几条都建立在同一件事上 —— 三个 Flask 应用能不能从 access token 里
读到 `groups`。**跑 `scripts/03-configure-keycloak.sh` 之后重新登录**,然后:

```
打开门户 / 权限门户 / 建表工具
→ 页面顶部**不应该**出现黄色的 ⚠ 提示
→ 出现了就说明 groups 还是没传过来,提示里写着该怎么办
```

这个提示本身就是这一轮的产物:在这之前,"配置没配对"和"你不在任何组"
在代码里长得一模一样(`groups == []`),分不开 —— 这个项目栽过三次
(ADR-078 的 Trino group provider、Superset 的 groups scope、
permission-request-app 的 `is_approver`)。

**9. 组权限申请这条流程**(此前对所有人 403,谁都没发现):

```
用 platform-team 的账号打开权限门户
→ 顶部应出现"你在 platform-team,可以审批组权限申请"
→ 「审计」和「权限交接」两个链接应该可见
→ 点批准一条组权限申请,应该成功而不是 403
```

**10. 门户按角色显示**:

```
data-analysts 账号 → 看不到 ArgoCD / Keycloak,看得到 SQL 工作台 / Superset
platform-team 账号 → 全部可见
顶部"N 个工具"的数字应该跟着可见的走,不是总数
```

**11. 代他人建表**:

```
platform-team 账号打开建表工具 → 负责人那个框可编辑
其他账号                       → 那个框是灰的(disabled)
非平台组的人直接 POST 一个别人的名字 → 落库的负责人仍然是他自己
```

**12. 建表工具的新表单**(全部只有单元测试,一次都没在集群上跑过):

```
带字段说明和分区提交一张表
→ Trino 里 SHOW CREATE TABLE 应该能看到 COMMENT 和 partitioning
→ OpenMetadata 里字段应该带 description
勾上质量断言
→ OpenMetadata 的 Data Quality 里应该真的出现对应的 testCase
点「预览要执行的 SQL」→ 显示的那段应该和实际执行的一字不差
用非平台组账号选 2 级 → 应该被挡住,记录里写着去权限申请门户
```

**13. 作业发布那一批**(同样只有单元测试):

```
多文件:jobs/daily-order-summary 现在是 job.py + jobkit.py
→ 手工提交一次,应该能 import jobkit 而不是 ModuleNotFoundError
补数:argo submit --from cronwf/daily-order-summary -n argo-workflows \
        -p run_date=2026-08-01
→ 表里应该出现 run_date=2026-08-01 那一批,而不是今天的
```

**14. 续期和到期提醒**:

```
造一条 7 天内到期的 grant → 跑一次 /internal/reclaim-expired
→ 企微(或 echo sink)应该收到「权限即将到期」
门户首页那条应该标黄、排最前、带「续期」链接
点续期 → 权限门户里出现一条 [续期] 开头的新申请,状态是等待审批
   (**不是直接延期**)
```

---

## 跑完之后

把验过的项在 [`capability-matrix.md`](capability-matrix.md) 里从「未验证」
改掉,并写上日期和证据。`scripts/check-capability-matrix.py` 会拦住"没验
就标 ✅"。

**15. 新加的 audit 黄金链路探针**(2026-08-30 加,**没上过集群**):

```
kubectl -n monitoring get cronjob goldenpath-audit        # 应该存在
手工触发一次:kubectl -n monitoring create job --from=cronjob/goldenpath-audit t1
→ 期望输出「链路 [audit] 通(Xs):最新审计记录 N 分钟前」
→ 如果报表不存在,说明 iceberg.audit.query_events 还没建 —— 那本身就是
  审计链路没跑起来,是真阳性不是探针的问题
门户首页「黄金链路」那栏应该变成 7 条,并出现「查询留痕」
```

**16. 从查询历史自动推的血缘**(2026-08-30 加,**没上过集群**):

```bash
./scripts/47-configure-openmetadata-trino-lineage.sh   # 配采集
./scripts/48-verify-trino-lineage.sh                   # 验证
```

48 会用一条真实的 `CREATE TABLE ... AS SELECT` 制造一条**确定存在**的血缘,
跑完采集之后查血缘接口确认那条边在,最后清理临时表。

**失败长什么样**:如果报"没有从 demo.orders 指过来的边",最可能的原因是
Trino 的 `system.runtime.queries` 里已经没有那条 CTAS 了 —— 它是内存里的,
受 `query.max-history` 限制,coordinator 一重启就清空。**这是已知局限,
不是配置错了**,脚本的输出里会直接这么说。
