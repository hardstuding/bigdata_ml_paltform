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

## CURRENT(2026-08-26):六条缺口已补完,现在在补"乱"和"没验"

zhenghe 2026-08-23 提了三个问题(门户/自建工具能不能优化、按角色的资源
管理做了没有、有没有部署使用运维手册),并说"有些设计可能是我没想到的,
你可以给我建议一下,我们的目的是企业级生产可用好用"。据此主动盘点出六条
"不做会出事"的缺口,清单和挑选标准在
[`docs/production-readiness-gaps.md`](production-readiness-gaps.md)。
zhenghe 回复"这 6 个挺重要的,都要做"。

### 进度(2026-08-26)

**六条生产可用性缺口全部落地并实机验证**(明细见下面 08-23 那轮的记录)。
08-26 这一轮做完的:

| 事项 | 结果 |
|---|---|
| OpenMetadata 1.13.3 → **2.0.0** 大版本升级 | ✅ 已验证([ADR-072](decisions/072-openmetadata-2-upgrade.md)) |
| 敏感字段**行级过滤**实机验证 | ✅ roles.md 最后一个 🟡 转 ✅ |
| flink CRD 常年 OutOfSync | ✅ 修掉了,66 个应用**全绿** |
| 镜像搬运的新路径 `scripts/38` | ✅ crane + rsync,不需要本机 docker |

### 08-26 这轮的三个教训

1. **OpenMetadata 2.0 本身很顺,卡住的是拉不到镜像。** 云主机同时失去了到
   Docker Hub 的所有通路(直连超时、daocloud 卡在 blob、另试 5 个镜像站
   全超时)。这把"给 k3s 配 registry mirror"从锦上添花变成**真实成本项**
   ——见 BACKLOG 第一条。
2. **flink CRD 那个 OutOfSync 的根因,BACKLOG 里原来猜错了。** 不是"CRD
   太大塞不进注解"(ServerSideApply 本来就开着),是 API server 的默认值
   归一化,值不同的字段是 0 个。**猜的根因写进 BACKLOG 之后会被当成事实**,
   这次是逐字段比对才发现的。
3. **我自己用字符串位置做批量编辑,又误删了 108 行 BACKLOG。** 和几天前
   `\1` 被当八进制转义是同一类:断言只验了"发生了替换",没验"替换掉的
   是不是该替换的东西"。

### 下一步

1. **告警出口**——质量、新鲜度、审计断流三个来源接**同一个**出口。真实
   阻塞是没有通知渠道凭据(zhenghe:"等后面上生产来测试")。
2. ~~给 k3s 配 registry mirror~~ —— **查证后作废**:这个集群的 k3s 走
   cri-dockerd,`registries.yaml` 根本不会被读。三个可选方案和代价列在
   BACKLOG 里,当前结论是先不动运行时配置,用 `scripts/38` 顶着。
3. ~~本机 colima 切 x86_64~~ —— **不做了**(zhenghe 2026-08-26:"本机的
   不用做了呀,我们都已经上云了")。local-lite 目前不使用。
4. **prod 不要跟 OpenMetadata 2.0.0**,等 2.0.x 出到两三个补丁版本再说
   (ADR-072 的建议)。

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
隧道、核对 kubeconfig。**公网 IP 不是固定 EIP,每次开机都会变**(已经变过
好几次),所以不要手抄 IP。

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
