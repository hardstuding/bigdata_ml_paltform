# 当前工作

> 这份文档只回答三件事:**现在的主线是什么、下一步做什么、有没有还在跑
> 的后台任务**。规则:任何时候只有一个 CURRENT,新想法默认进
> `docs/BACKLOG.md`,不自动抢占 CURRENT。
>
> 2026-08-19 起,按日期堆叠的排障叙事**不再往这里写**,归档到
> `docs/journal/<年-月>.md`(原因见
> [ADR-057](decisions/057-architecture-review-2026-08-19.md))。这份文件
> 要一直保持"打开就能知道现在什么情况",不能再退化成日记。
>
> 想知道"某个角色今天能做什么" → [`docs/roles.md`](roles.md)
> 想知道"以前某个问题怎么解决的" → [`docs/journal/`](journal/) 和
> [`docs/operations/troubleshooting.md`](operations/troubleshooting.md)

## CURRENT(2026-08-27):补"整体"——按链路看平台,不按组件看

六条生产可用性缺口 08-26 已补完并实机验证。这一轮转向 zhenghe 说的
"先有整体":平台有 8 条告警、4 个看板、ArgoCD 健康、roles.md 能力表,
**但它们回答的全是组件层面的问题**,没有一个地方回答"一件真实的事现在
做不做得成"。

### 这一轮做完并实机验证的

| 事情 | 证据 |
|---|---|
| **黄金链路探针**(ADR-079,D 线第一步) | 三条链路各一个 CronJob,实测全通:query 0.2s/10 行、catalog 6 个字段、streaming 最新数据 5 分钟前 |
| **Trino group provider**(ADR-078) | `is_platform_admin` 之前**从来没生效过**;修完后 platform-team 的人能查审计表、查带行级过滤的表拿到全部 6 行 |
| **IAM 数据补齐** | 5 个 demo 用户里 3 个不属于任何组、Keycloak 里根本没建号——补齐并加 CI 检查 |
| **Superset 汉化的另一半**(ADR-077) | 镜像里 22 个 `.po`、**0 个 `.mo`**,只加配置界面不会变中文;Dockerfile 加了 `pybabel compile` |
| KServe runtime 精简(ADR-075) | 12 → 4,镜像清单 77 → 69 |
| Spark 4.x 评估(ADR-076) | 暂不升,但查出它能解开 Iceberg 卡在 1.10.0 的天花板 |
| P5 瘦身 | 删掉 13MB 陈旧 worktree 副本;CURRENT_WORK 647 → 131 行;4 篇被推翻的 ADR 加前向指针 |

### 这一轮反复出现的一个模式

**"机制建好了,但它作用的对象不存在 / 它依赖的输入永远是空的"**:

- `is_platform_admin` 规则写了,但 Trino 从不传 groups(ADR-078)
- 组、角色、队列、策略全配好了,但 5 个用户里 3 个不在任何组里
- Kueue 的配额指标默认不导出,容量看板 6 个 panel 空了 4 个
- Superset 汉化配置生效了,但翻译文件没编译

**四个都不报错,而且单元测试/同步状态全绿。** 这就是黄金链路探针存在的
理由——只有"从头做一件真实的事"才发现得了。

### 下次开机必须做的一件事

**ArgoCD 的配置改了,但它不是 GitOps 管的**——ArgoCD 自己是
`scripts/01-bootstrap-argocd.sh` 手动 helm 装的(这是仓库里唯一允许手动
helm upgrade 的组件)。2026-08-27 给它加了 `batch/Job` 的自定义健康检查
(让黄金链路探针的失败不再把 Application 标成 Degraded,ADR-079),**改动
在 git 里,但集群上还没生效**。

```bash
./scripts/01-bootstrap-argocd.sh     # 幂等,就是 helm upgrade
```

不跑的话表现是:探针一失败,`golden-path-probes` 就在 ArgoCD 上挂 Degraded
——不影响功能,但会攒常年黄灯。

### 下一步

1. **告警出口**——三个来源(质量/新鲜度/审计断流)接同一个出口。真实阻塞
   是没有通知渠道凭据。
2. **算法链路和治理链路的探针**(ADR-079 只覆盖了三条)。
3. **prod 不要跟 OpenMetadata 2.0.0**,等 2.0.x 出到两三个补丁版本。
4. 五条产品主线 A/C/E 仍然基本没开始。

### 云主机状态

已停机。2026-08-26 开了两次:一次因为镜像拉不动主动停机止损、把镜像在
本地准备好再开第二次(按量付费的既定做法:先把要做的事准备到开机就能
立刻跑,再开机)。

---

## 以前的轮次

已完成的历史主线(2026-08-19 ~ 08-23 共 6 轮)搬到了
[`docs/journal/current-work-archive-2026-08.md`](journal/current-work-archive-2026-08.md),
原文照搬。**这份文件只留当前那一轮**——它开头那条"不能再退化成日记"的规则,
2026-08-26 之前其实已经失效了:647 行里 500 行是历史。

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
  记得手动重跑了,见 docs/BACKLOG.md 2.3。
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
- [ ] **能力有增减的话,`docs/roles.md` 更新了吗**(新增的一条,ADR-057)
