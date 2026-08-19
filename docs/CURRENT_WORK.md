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

## CURRENT(2026-08-19)

- **标题**:架构评估与文档结构重组(ADR-057)
- **为什么现在做**:zhenghe 提出"最近做的事有点多,也开始有点乱了……
  我怕这么堆叠开发下去,后续就无法维护了",要求从各个角色的角度出发
  评估架构要不要调整。
- **状态**:**第一批(文档结构重组)已完成**。评估结论和后两批的决策
  见 ADR-057,执行项已排进 `docs/BACKLOG.md` 最前面。
- **做完了什么**:
  - [ADR-057](decisions/057-architecture-review-2026-08-19.md)——架构
    评估结论 + 三批调整的决策
  - [`docs/roles.md`](roles.md)——**新的进度权威入口**,五个角色 ×
    完整工作链路 × 每一环今天的真实状态
  - `CURRENT_WORK.md`(这份)拆成状态 + `docs/journal/2026-08.md` 日志
  - `docs/BACKLOG.md` 重排:取消 P1.5/P1.6/P1.7 这种"批次伪装成优先级",
    改成按"阻塞哪个角色的哪条能力"排序
  - `docs/decisions/README.md`——57 个 ADR 的主题索引
  - `docs/architecture.md`、`README.md` 的进度描述改成指向 `roles.md`,
    不再各自维护一份会过时的清单

## 下一步唯一动作

**ADR-057 第三批:补上环境抽象的"组件选择"层。**

为什么是它而不是别的(依据 `docs/roles.md` 的角色表):cloud-full 上有
8 个组件没启用,而它们几乎精确对应"大数据开发/算法/治理三个角色所需
要的东西"。**这些组件都单独验证通过过**,不是没做出来——是"哪些组件
在哪个环境启用"至今仍靠人工在 `apps/definitions/` 和
`environments/cloud-full/pending-definitions/` 之间 `git mv`,而
cloud-full 是 16 vCPU / 64 GiB,早就不受当初那个 6GB colima 的资源约束了。

做完这一批,"拉起 OpenMetadata / JupyterHub + MLflow / Spark Operator +
SeaTunnel"就从"人工 git mv 加祈祷"变成"改一行配置 + 渲染",一次性解锁
三个角色。

具体设计留到动手时展开(要和已有的
`scripts/render-environment-config.py` 机制衔接,不是再造一个新的)。

## 正在运行的后台任务

**没有。**

- cloud-full 云主机(`i-0jlbped4h1959tp591pe`)**已停机**,2026-08-16 用
  `scripts/26-stop-cloud-vm-economical.sh` 停的,经济模式
  (`StoppedMode=StopCharging`),停机期间不产生计算费用。
- 重新开机后 SSH 隧道要重新建(公网 IP 不是固定 EIP,可能变),命令见
  `environments/cloud-full/STATUS.md` 的"下一步"一节。
- 本机 colima 上的重量级组件处于 park 状态。

## 已知的、还没解决的事(不要重新排查一遍)

- **argo-workflows 的 SSO 登录从没真实验证过**——它和 Trino/Superset
  一样用 discovery 自动模式,理论上有同一类风险,但没有人真的登录触发过。
  如实记录,不是回避。
- **ArgoCD 偶发卡在过期的同步操作上**:`.status.operationState` 会卡在
  一个旧的操作快照上不断 retry,用它缓存的旧 source 把已经修好的资源
  改回去。处置方式(本次会话实测有效)见
  `docs/journal/2026-08.md`,搜"卡住的旧操作"。
- **`scripts/07-fix-trino-liveness-probe.sh` 必须在每次 Trino pod
  template 变更后重跑**,否则 livenessProbe 回退到 chart 的坏默认值。
  这条已经作为债务记进 ADR-057。
- **cloud-full 上 Keycloak `platform` realm 的 `admin` 密码**在排障时被
  `kcadm set-password` 重设成 `TestLogin2026Aug`,和
  `secrets/generated-credentials.txt`(那份是 local-lite 的)不是一回事。

## 结束一段工作前必须确认(照着过一遍,不要跳)

- [ ] `git status` 干净,该 push 的都 push 了
- [ ] 计费资源现在的状态说清楚了(开着/停了,为什么)
- [ ] 后台任务/SSH 隧道是不是还开着,写进了上面那节
- [ ] 这次做的事,哪些是真实验证过的、哪些只是写完代码没测,分层说清楚
- [ ] 有没有手工改过集群但没回写 git 的操作(有的话赶紧记下来或者补写)
- [ ] 失败但没解决的事情,写清楚现象+已经排除的原因,别人接手不用重新排查一遍
- [ ] **能力有增减的话,`docs/roles.md` 更新了吗**(新增的一条,ADR-057)
