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

## CURRENT:Codex 评审第 2~4 批(roadmap P1.5)

**做完的**:第一批 4 条确定性缺陷,以及 zhenghe 8-28 提的五点(Superset
权限 / 门户 logo / notebook 自动鉴权 / 内部包共享 / Flink 流作业入口)——
全部落地并在 cloud-full 上实机验证过。逐轮细节在
[`docs/journal/2026-08.md`](../journal/2026-08.md)。

**在做的**:P1.5 剩下三批,验收条件写在
[`roadmap.md`](roadmap.md) 的 P1.5 那节。
按解锁能力排的顺序:

1. **分析师的浏览器 SQL 入口** —— 方案已定([ADR-084](../decisions/084-analyst-sql-workbench.md)),
   门户已改。**差实机验证**:SQL Lab 里的 Trino 连接没单独验过 impersonation。
2. **门户升级成角色工作台** —— 还没开始。依赖 permission-request-app 开一个
   只读接口(现在没有)。
3. **作业发布从单文件升级成可维护流程** —— 还没开始。
4. **建表注册工具 / 审批体验** —— 还没开始。

**同时在推的**:文档职责重构(P1.5 的一项)。已做:能力表重写成
状态+验证级别+最后验证+证据(并加了 CI 自洽检查)、这份文件收敛回一页。
还没做:使用指南按角色和任务拆分、Runbook 每条统一成
触发条件/影响/前置检查/操作/验证/回滚。

## 下次开机要验的三件事(都写好了判据,不用现想)

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

验过了才能把 capability-matrix 里对应那格改掉 ——
`scripts/check-capability-matrix.py` 会拦住"没验就标 ✅"。

## 需要 zhenghe 配合的

- **真实告警渠道、域名+ICP 备案、多节点演练**:按他的安排,等上测试/生产
  环境再说,现在只留好配置。
- **真实 HR/IdP 对接方**:组织架构同步现在是虚拟占位数据,接真实系统需要
  他指定对接方。
- 除此之外没有卡在他身上的事。

---

### 云主机状态

**已停机**(2026-08-29,经济模式 `StoppedMode=StopCharging`,不产生计算
费用;磁盘照常保留)。开机 `scripts/32-start-cloud-vm.sh`,停机
`scripts/26-stop-cloud-vm-economical.sh`。

停机前最后一次核对:77 个 ArgoCD Application 全部 Synced/Healthy,零异常
pod。当时 `kubectl get pods -A` 里堆着 50+ 个 Error 看着很吓人,查下来是
两小时前 Trino coordinator 重启循环窗口的历史残留 —— **每条黄金链路探针、
每个采集 CronJob 的最近一次都是 Completed**。

> **判断"现在到底好没好"的方法**:pods 列表里的 Error 是**过去某一刻**的
> 快照,不是现在的状态。要看的是每个 CronJob **最近一次**那个 pod。

**这台机器不是我们独占的** —— 详见 `CLAUDE.md` 里那节:任何会让 k3s 停掉/
重装、停机释放、或改集群级资源(CRD/ClusterRole/ingress/cert-manager)的
操作,必须先停下来问 zhenghe。

---

## 以前的轮次

全部在 [`docs/journal/`](../journal/) 里,原文照搬,一个字没删:
2026-08-19 ~ 08-23 那 6 轮在
[`current-work-archive-2026-08.md`](../journal/current-work-archive-2026-08.md),
08-28 ~ 08-29 那 8 轮在 [`2026-08.md`](../journal/2026-08.md) 末尾。

**这份文件只留"现在"。** 开头那条"不能再退化成日记"的规则失效过两次:
2026-08-26 那次是 647 行里 500 行是历史;2026-08-29 这次是搬完不到三天
就又堆了 8 个「这一轮」小节、203 行。规则写在文件里挡不住复发,所以
**判断标准改成一句可执行的**:这份文件超过 ~150 行,基本就是又开始写
日记了。

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
- ~~`scripts/07-fix-trino-liveness-probe.sh` 必须在每次 Trino pod
  template 变更后重跑~~——**已解决(2026-08-20)**:`apps/
  trino-liveness-fix/` 这个 CronJob 每 5 分钟自动巡检并修复,不需要人
  记得手动重跑了,见 docs/project/roadmap.md 2.3。
- **cloud-full 上 Keycloak `platform` realm 的 `admin` 密码**是
  `TestLogin2026Aug`,和 `secrets/generated-credentials.txt`(那份是
  local-lite 的)不是一回事。
- **算法链路"训练 → MLflow"和"Feast 特征"都已验证,"notebook 触发"和
  "Argo Workflows 编排训练"是真空白**:JupyterHub/MLflow/Spark
  Operator/Feast/Argo Workflows/Kafka 都已部署验证,
  `scripts/09-train-demo-model.sh` 和 `scripts/19-feast-feature-pipeline.sh`
  分别证明了这两段真实可用。剩下两段不是"没重新验证"而是从没实现过,
  见上面"下一步唯一动作"里的说明。
- **低配额命名空间改 resources 字段要格外小心**:mlflow 命名空间的
  ResourceQuota 只有 3Gi,RollingUpdate 需要新旧 pod 同时占配额,改大
  resources 时如果新旧加起来超配额,新 ReplicaSet 会静默卡在
  "exceeded quota",ArgoCD 显示 Synced/Healthy 但实际流量还在旧 pod
  上——这次真实卡了一个多小时才发现。mlflow 已经改成 `Recreate` 策略
  规避,其它低配额命名空间(检查 `platform/resource-quotas/manifests/
  quotas.yaml`)如果也要改 resources,先算一下新旧加起来会不会超配额,
  或者一并考虑改成 Recreate。

## 结束一段工作前必须确认(照着过一遍,不要跳)

- [ ] `git status` 干净,该 push 的都 push 了
- [ ] 计费资源现在的状态说清楚了(开着/停了,为什么)
- [ ] 后台任务/SSH 隧道是不是还开着,写进了上面那节
- [ ] 这次做的事,哪些是真实验证过的、哪些只是写完代码没测,分层说清楚
- [ ] 有没有手工改过集群但没回写 git 的操作(有的话赶紧记下来或者补写)
- [ ] 失败但没解决的事情,写清楚现象+已经排除的原因,别人接手不用重新排查一遍
- [ ] **能力有增减的话,`docs/project/capability-matrix.md` 更新了吗**(新增的一条,ADR-057)
