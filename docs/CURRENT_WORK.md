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

## CURRENT(2026-08-28):A/C/D/E 四条主线各推进第一步

六条生产可用性缺口 08-26 已补完并实机验证。这一轮转向 zhenghe 说的
"先有整体":平台有 8 条告警、4 个看板、ArgoCD 健康、roles.md 能力表,
**但它们回答的全是组件层面的问题**,没有一个地方回答"一件真实的事现在
做不做得成"。

### 这一轮(2026-08-28)做完并实机验证的

**六条黄金链路全通**(`平台能不能用 = 6`)。四条产品主线各推进了第一步:

| 主线 | 做到哪 |
|---|---|
| A 统一开发工作台 | 🟡 4 个作业模板(`examples/`)+ `platform-submit --new` 脚手架。**CI-CD 那半没做** |
| C 完整 MLOps | ✅ 审批/回滚闭环([ADR-080](decisions/080-model-approval-and-rollback.md)):训练→注册→审批→部署→**真实推理返回 `[0,1]`**→回滚守卫。**灰度没做** |
| D 统一运维控制面 | ✅ 六条链路探针 + 告警 + 看板([ADR-079](decisions/079-golden-path-probes.md)) |
| E 管理驾驶舱 | 🟡 平台总览看板;**按月聚合的前提刚补上**(见下面第 2 条) |

另外:Superset 汉化实机验证通过(4054 条,`Dashboards→看板`);
hive-metastore 自建镜像。

### 这一轮挖出来的四个"早就断了"

**都不是这次改动引入的**,共同点是**每一层都显示正常**:

1. **KServe 推理链路** —— `kserve-demo` 不在 MinIO 的 NetworkPolicy 白名单里,
   [ADR-035](decisions/035-network-policies.md) 上线之后就断了,而没人发现
   (从那以后没人跑过 `scripts/11`)。检查器报绿,是因为那个命名空间是脚本
   运行时建的、不在 git 里——**报绿而实际是断的,比没有检查器更糟**。
2. **Prometheus 指标每次开机清零** —— cloud-full 用的是 `retention: 6h` +
   emptyDir。我在 ADR-069 里写"没有月度聚合"时以为只是没写查询,**实际上是
   数据压根不存在**。已改成 15d + 20Gi 持久卷。
3. **hive-metastore 每次启动等 6~7 分钟**下 100MB 的 jar,而且把整条查询链路
   挂在境外网络上。自建镜像后**实测 55 秒**。
4. **改了 Dockerfile 但 CI 不构建** —— matrix 里加了镜像却漏了 `paths` 触发。
   已加 `scripts/check-build-triggers.py` 拦这一类。

### 2026-08-28 晚这一轮

1. **门户改版**。zhenghe:"毫无设计感""一些文字像是我们的对话,而不是
   一个产品应该有的形态"。文案和视觉分开修——标题改成名词短语、删掉所有
   "不是……而是……"式的解释和 ADR 编号(解释挪进 `title` 悬停),加了顶部
   四个概览数字和一套统一的 design token(含深色模式)。文案规则写死在
   模板顶部注释里,免得下次又写回去。已部署,页面实测 12 KB。
2. **Spark 3.5.9 → 4.1.3 + Iceberg 1.10.0 → 1.11.0**([ADR-076](decisions/076-spark-4-evaluation.md))。
   这两条本来就是一个决定:Iceberg 锁 1.10.0 是因为 1.11.0 要 Java 17 而
   Spark 3.5 镜像自带 Java 11,而 Spark 4 本身要求 Java 17。连带 hadoop-aws
   3.3.4→3.4.2、AWS SDK v1→v2(Hadoop 3.4 起 S3A 迁到 SDK v2,最容易漏的
   一条)。Hive Metastore 仍锁 3.1.3,理由和 Spark 无关。
3. **secrets 清理**。两份文件、42 条记录,实测比对集群指纹后只剩 16 条
   有效的;`generated-credentials-cloud-full.txt`(8-15 那份)13 条全失效,
   已删。顺手修了 `show-credentials.sh --audit-file` 路径写死、传别的文件
   会被静默忽略的问题。
4. **ArgoCD 的 `batch/Job` 健康检查已经生效**(查 `argocd-cm` 确认),
   上一轮写的"下次开机必须做的一件事"是过期信息,已删。

### 下一步

1. **算法链路和治理链路的探针**(ADR-079 只覆盖了三条)。
2. **A 线的 CI-CD 那半**、**C 线的灰度**、**E 线的按月聚合**(要等数据攒够)。
3. **prod 不要跟 OpenMetadata 2.0.0**,等 2.0.x 出到两三个补丁版本。

告警出口按 zhenghe 明确要求**不再推进**——"上生产了再说,现在留好配置
就好"。配置项都在,`alert-echo-sink` 那次已经验证过链路能通到出口。

### 云主机状态

**运行中**(2026-08-28 22:56 开机)。这一轮要在集群上实测 Spark 4 那条链路,
用完记得 `./scripts/26-stop-cloud-vm-economical.sh`。

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

### 两个 CronJob 类应用在 ArgoCD 上显示 Degraded(2026-08-28)

`golden-path-probes` 和 `openmetadata-quality-alerts`。原因和处置**不一样**,
不要一起当成同一个问题:

- **`golden-path-probes`**:ADR-079 已经给它配了 ArgoCD 自定义健康检查
  (`batch/Job` 的 Lua,只对带 `platform/golden-path` 标签的 Job 返回 Healthy)。
  但**标签是 2026-08-28 才加到 `jobTemplate` 上的**,在那之前生成的失败 Job
  没有这个标签,所以还会拉低健康状态。`failedJobsHistoryLimit: 3`,**会自己
  老化掉**,不用处理。下次开机确认一下就行。
- **`openmetadata-quality-alerts`**:它的 Job **没有**那个标签,也不该有——
  这个 CronJob 失败意味着"质量告警这座桥没跑",那是**真的降级**,变红是对的。
  当前这次失败来自云主机冷启动时 CoreDNS 还没就绪(`Temporary failure in
  name resolution`),下一轮定时执行会自愈。
  **但值得想一下**:冷启动瞬态失败不该长期挂红。可选做法是给它加
  `startingDeadlineSeconds` + 重试,或者接受"每次开机红一轮"。还没定。


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
