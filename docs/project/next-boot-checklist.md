# 下次开机要验的清单

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
