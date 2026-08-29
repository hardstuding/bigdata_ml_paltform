# 当前工作

> 这份文档只回答三件事:**现在的主线是什么、下一步做什么、有没有还在跑
> 的后台任务**。规则:任何时候只有一个 CURRENT,新想法默认进
> `docs/project/roadmap.md`,不自动抢占 CURRENT。
>
> 2026-08-19 起,按日期堆叠的排障叙事**不再往这里写**,归档到
> `docs/journal/<年-月>.md`(原因见
> [ADR-057](../decisions/057-architecture-review-2026-08-19.md))。这份文件
> 要一直保持"打开就能知道现在什么情况",不能再退化成日记。
>
> 想知道"某个角色今天能做什么" → [`docs/project/capability-matrix.md`](../project/capability-matrix.md)
> 想知道"以前某个问题怎么解决的" → [`docs/journal/`](../journal/) 和
> [`docs/operations/troubleshooting.md`](../operations/troubleshooting.md)

## CURRENT:P1.5 全部写完,**卡在实机验证上**

Codex 评审第 2~4 批(roadmap P1.5)六条全部落地。**但其中大部分只有单元
测试,没上过集群** —— 云主机是关的,而这一批改动里有好几处的失败是静默的
(token 对不上首页只是空着、impersonation 没生效只是悄悄用了服务账号权限、
RBAC 少一个动词只在点按钮时才 403)。所以状态是"写完了",不是"能用了"。

| P1.5 那六条 | |
|---|---|
| 分析师的 SQL 入口 | 写完(ADR-084)。差:SQL Lab 的 impersonation 没单独验过 |
| 门户角色工作台 | 写完(我的权限 / 待我审批 / 作业详情 / 按角色显示工具) |
| 作业发布升级 | 写完(多文件 / 依赖对账 / 参数化补数 / 按环境晋级)。owner_group 身份绑定**今天不生效**,要真实 HR 数据 |
| 建表注册工具 | 写完(字段说明 / 分区 / 质量断言 / 预览 / 审批分流 / 负责人不能冒充) |
| 审批体验 | 写完(状态中文化 / 时区 / 理由必填 / 拒绝原因 / 到期提醒 / 催办 / 续期) |
| 文档职责重构 | 做完(能力表 / 这份文件 / 使用指南 / 两份操作手册) |

**下一步只有一件事:开机,跑 `./scripts/46-verify-p15.sh`,再手点它验不了
的那几条**(清单和判据在 [`next-boot-checklist.md`](next-boot-checklist.md))。 在那之前不该再往上堆新功能 —— 现在已经有一批"写完但没
验过"的东西了,继续堆只会让实机验证那天要一次面对更多未知。

## 下次开机要验的清单

在 [`next-boot-checklist.md`](next-boot-checklist.md),7 条,每条带判据和
"失败长什么样"。最要紧的两条:**SQL Lab 的 impersonation**(ADR-084 唯一
没验的一环)和**提权路径确认已堵**(建表 owner → 自己批自己)。

## 需要 zhenghe 配合的

- **真实告警渠道、域名+ICP 备案、多节点演练**:按他的安排,等上测试/生产
  环境再说,现在只留好配置。
- **真实 HR/IdP 对接方**:组织架构同步现在是虚拟占位数据,接真实系统需要
  他指定对接方。
- 除此之外没有卡在他身上的事。

---

### 云主机状态

**已停机**(2026-08-29,经济模式,不产生计算费用;磁盘保留)。
开机 `scripts/32-start-cloud-vm.sh`,停机 `scripts/26-stop-cloud-vm-economical.sh`。
停机前核对过:77 个 ArgoCD Application 全部 Synced/Healthy,零异常 pod。

> 开机后如果看到一大片 Error 的 pod,先看
> [troubleshooting 里那条](../operations/troubleshooting.md#kubectl-get-pods--a-里一大片-error但其实什么都没坏)
> —— 多半是历史残留,不是活故障。

**这台机器不是我们独占的**,`CLAUDE.md` 里那节列了哪些操作必须先问 zhenghe。

---

## 以前的轮次

全部在 [`docs/journal/`](../journal/),原文照搬:08-19 ~ 08-23 那 6 轮在
[`current-work-archive-2026-08.md`](../journal/current-work-archive-2026-08.md),
08-28 ~ 08-29 那 8 轮在 [`2026-08.md`](../journal/2026-08.md) 末尾。

**这份文件只留"现在"。** 判断标准:超过 ~150 行基本就是又开始写日记了
(这条规则失效过两次,写在文件顶上挡不住复发,所以改成一个可以数的数)。

## 正在运行的后台任务

**没有。** cloud-full 云主机已停机(`scripts/26-stop-cloud-vm-economical.sh`,
经济模式 `StoppedMode=StopCharging`,停机期间不产生计算费用;磁盘照常保留,
包括 `/data/k3s.pre-teardown-20260822` 那份备份)。

重新开机用 `scripts/32-start-cloud-vm.sh`——它会自动取新的公网 IP、重建 SSH
隧道、核对 kubeconfig、**并检查 `/etc/hosts` 里的 `*.local-lite.test` 是不是
还指着旧 IP**。**公网 IP 不是固定 EIP,每次开机都会变**(已经变过好几次),
所以不要手抄 IP。

**2026-08-27 真实踩到**:zhenghe 打开 `http://portal.local-lite.test:32460/`
报 500,以为是自己 VPN 的问题。实际是 `/etc/hosts` 里还写着上上次的 IP
(`8.130.69.252`),而那个 IP 早被回收、多半已经分给别人的实例了——**浏览器
会把 `*.local-lite.test` 的 cookie 一起发给那台陌生机器**。所以这不只是
"打不开",是每次开机后都存在的一个小信息泄露面。开机脚本现在会检测并给出
可直接粘贴的修复命令(想自动改就 `UPDATE_HOSTS=1 ./scripts/32-start-cloud-vm.sh`)。

**local-lite 目前不使用**(zhenghe 2026-08-26:"本机的不用做了呀,我们都
已经上云了"),本机 colima 是停的。

## 已知的、还没解决的事(不要重新排查一遍)

### 冷启动时 CronJob 类应用会红一轮(2026-08-29 复核)

`openmetadata-quality-alerts` 这类 CronJob,在云主机冷启动、CoreDNS 还没
就绪时会失败(`Temporary failure in name resolution`),下一轮定时执行自愈。
**它变红是对的**——这个 CronJob 失败意味着"质量告警这座桥没跑",那是真的
降级,不该像 `golden-path-probes` 那样用自定义健康检查糊过去。

**没定的是**:冷启动瞬态失败要不要长期挂红。可选做法是加
`startingDeadlineSeconds` + 重试,或者就接受"每次开机红一轮"。

(`golden-path-probes` 那半 2026-08-29 复核已经不再 Degraded:缺标签的
历史失败 Job 被 `failedJobsHistoryLimit: 3` 老化掉了,和当时判断的一致。)

- **idle-shutdown-watchdog 的开机自愈**(2026-08-19 修复,这个脚本本身
  按既定政策不进 git):停机几天后重新开机,看门狗第一次检查会用几天前
  的旧时间戳误判"已空闲超过阈值",机器刚开机 2-3 分钟就被自己关掉。
  已加开机时重置状态的机制,细节在本地脚本注释里,不在 git 历史里。
- **ArgoCD 偶发卡在过期的同步操作上**:`.status.operationState` 会卡在
  一个旧的操作快照上不断 retry,用它缓存的旧 source 把已经修好的资源
  改回去。处置方式(本次会话实测有效)见
  `docs/journal/2026-08.md`,搜"卡住的旧操作"。
- **cloud-full 的 Keycloak admin 密码在公开仓库的 git 历史里** —— zhenghe
  2026-08-29 决定**不换**("只是开发测试")。当前版本的文档里已经没有明文。
  **不要再提这条**;只有当这台机器开始承载真实数据或真实用户时才重新评估。
  背景和真要换时的步骤见
  [troubleshooting](../operations/troubleshooting.md#cloud-full-的-keycloak-admin-密码)。

## 结束一段工作前必须确认(照着过一遍,不要跳)

- [ ] `git status` 干净,该 push 的都 push 了
- [ ] 计费资源现在的状态说清楚了(开着/停了,为什么)
- [ ] 后台任务/SSH 隧道是不是还开着,写进了上面那节
- [ ] 这次做的事,哪些是真实验证过的、哪些只是写完代码没测,分层说清楚
- [ ] 有没有手工改过集群但没回写 git 的操作(有的话赶紧记下来或者补写)
- [ ] 失败但没解决的事情,写清楚现象+已经排除的原因,别人接手不用重新排查一遍
- [ ] **能力有增减的话,`docs/project/capability-matrix.md` 更新了吗**(新增的一条,ADR-057)
