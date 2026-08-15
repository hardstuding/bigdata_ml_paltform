# 050. 表访问授权到期回收

- 状态: 已采纳,已实现,已实测

## 背景

`platform/iam/table-access-grants.csv` 从 ADR-044 一开始就有
`expires_at` 这一列,但写入逻辑(`apply_grant_to_git()`)一直没真正填过
这个字段——每条授权记录的 `expires_at` 永远是空字符串,等于"永久有效,
没有任何回收机制"。这是 ADR-040 原本就要求、但一直没有落地的缺口
(用户在 2026-08-14 前后明确要求过补上)。

## 决策

### 默认统一过期时长,不做按安全等级分档

新增 `GRANT_EXPIRY_DAYS` 环境变量,默认 `180`(半年,常见的企业访问复审
周期)。`apply_grant_to_git()` 写入时,`expires_at = granted_at + 180天`,
不区分安全等级——现在没有真实数据/真实审计要求支撑"L1 该多久、L3 该多久"
这种更细的分级策略,做了也是编出来的数字,不如先统一,以后有真实依据
再拆分。

### 回收方式:CronJob 定时调用内部端点,复用已有模式

新增 `/internal/reclaim-expired` 端点(和 ADR-045 的
`/internal/escalation-check` 同一个模式:`X-Internal-Token` 共享密钥鉴权,
不走 oauth2-proxy/人类登录)。新增 CronJob
`permission-request-app-reclaim`(`apps/permission-request-app/manifests/
reclaim-expired-cronjob.yaml`),每天 02:30 跑一次,和
`escalation-cronjob.yaml` 一样用 `curlimages/curl`(不装 python),一样
用 `--retry-connrefused` 应对新 Job pod 的 NetworkPolicy 生效延迟——这些
坑之前都踩过一次了,这次直接复用,不重新踩。

端点逻辑:clone 仓库 → 读 `table-access-grants.csv` → 按 `expires_at` 是否
已经过去分成"保留"/"回收"两组 → 如果有要回收的,重写 CSV(只保留未过期的
行)→ commit + push → 对每条被回收的记录发一条企微通知(未配置
`WECOM_WEBHOOK_URL` 时静默跳过,和其他通知场景一致)。解析 `expires_at`
失败的脏数据保守处理成"不回收",不会因为格式问题被误删。

### 明确的范围边界:回收的是记录,不是真实权限

`table-access-grants.csv` 现在**没有任何执行引擎在读它**(ADR-044/045/
046 都反复强调这条边界,这里继续如实标注)——回收动作只是让这份"决策
留痕"记录保持准确,不产生"撤销 Trino 里实际能查到的权限"这种效果。这个
边界不是这次要解决的问题,等 Trino OPA 真正接上这份数据(ADR-028"后续")
之后,回收才会变成有实际访问控制效果的动作。现在做这件事的意义是:
到那时候接上执行引擎,消费的是一份已经在正确回收、没有堆积大量早就该
失效的记录的数据,不用另外补一次历史数据清理。

## 涉及的文件

- 改:`apps/permission-request-app/src/app.py`(新增
  `GRANT_EXPIRY_DAYS`、`apply_grant_to_git()` 补上真实 `expires_at`、新增
  `/internal/reclaim-expired`)+ 同步的 `app-configmap.yaml`
- 改:`apps/permission-request-app/manifests/deployment.yaml`(新增
  `GRANT_EXPIRY_DAYS` 环境变量,默认 180)
- 新增:`apps/permission-request-app/manifests/reclaim-expired-cronjob.yaml`
- 改:`platform/network-policies/manifests/permission-request-app.yaml`
  (新增 `allow-reclaim-cronjob-to-app`,和已有的
  `allow-escalation-cronjob-to-app` 同一个模式)

## 明确不做的

- 不按安全等级/表/部门做不同过期时长——现在没有真实依据,先统一。
- 不做"授权到期前提醒续期"这种更完整的生命周期管理——现在的范围只是
  "到期后清掉记录",续期提醒是更进一步的用户体验工作,不在这次范围里,
  以后有真实使用场景反馈再加。
- 不产生真实的 Trino 访问控制效果(见上面"范围边界"说明)。

## 验证

### 已验证(2026-08-15,实测,不是只看代码)

- 部署:commit → push → ArgoCD sync(`network-policies`/
  `permission-request-app` 都到 Synced/Healthy,revision 对上)→
  `permission-request-app` Deployment 重新 rollout 成功;
  `permission-request-app-reclaim` CronJob、
  `allow-reclaim-cronjob-to-app` NetworkPolicy 都确认已创建。
- 端到端回收流程:在 `table-access-grants.csv` 手动加一条
  `expires_at=2026-01-15`(已过期)的测试记录、commit/push;用带
  `app: permission-request-app-reclaim` 标签的测试 pod(匹配
  NetworkPolicy 放行条件)直接 POST `/internal/reclaim-expired`,返回
  `{"reclaimed":1,"tables":["demo.access_test_expired"]}`;`git pull`
  确认仓库里这一行真的被移除、另外两条未过期记录原样保留、app 自己产生了
  一条 `iam: 自动回收 1 条已过期的表访问授权(ADR-050)` 的 commit。
- 幂等性:紧接着再调一次同一个端点,返回 `{"reclaimed":0}`——没有已过期
  记录时不会误删或重复处理。
- 鉴权边界:用错误的 `X-Internal-Token` 调用,返回 `403`,确认没有
  `INTERNAL_TOKEN` 正确匹配就拿不到这个端点的访问权限。

### 还没验证的

- `apply_grant_to_git()` 写入真实 `expires_at` 这部分,是读代码 + 语义
  确认(改动很直接:`granted_at + GRANT_EXPIRY_DAYS 天`),没有触发一次
  真实的表访问审批走完整链路来生成一条新记录并肉眼确认那条记录的
  `expires_at` 字段——下次有真实审批流程跑过一遍时应该顺便确认一下。
- CronJob 的 `schedule: "30 2 * * *"` 本身没有等到真实触发时间去看它
  自动跑起来(用的是手动模拟 CronJob 会调用的同一个端点来验证逻辑,
  不是等一整天看 CronJob 自己触发)。
