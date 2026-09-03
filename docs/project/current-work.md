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

## CURRENT:按「配了但没打通」这条线索逐个组件排查(2026-09-02 起)

起因是一句抽查意见:"完全达不到生产可用,核心 demo 没跑通,业务的基本流程
和环节没验证过",并且预判"其他很多组件可能也类似"。这个预判是对的。

**做完的(每一条都有可重复的脚本,判据都不是 HTTP 200 / 状态 Success)**

| 问题 | 之前的表现 | 现在 |
|---|---|---|
| Superset 没有"只能看"的角色 | Gamma 看着像只读,实际带 `can_write\|Chart` | 两个自建角色,`scripts/54-` 建、按组映射 |
| 看板没法分享给指定的人 | `DASHBOARD_RBAC` 没开,`roles` 存了不生效 | `scripts/56-` 验证成立,含反向证伪 |
| 告警菜单不存在 | 开关没开 → 因为没 worker → 因为没 broker | 整条链补齐,`scripts/55-` 核对收件端拿到的字节 |
| 企微二开一行没跑过 | 插件按注册顺序取第一个,子类永远轮不到 | 修好,payload 形状核对过 |
| 分组页报错 | FAB 5.0.2 前后端列名对不上 | 在配置里补两个列名,不改镜像 |
| Airflow 谁登录谁是 Admin | 能进 Connections 看到所有数据库密码 | 按组映射;实测 connections 返回 403 |
| Airflow 任务失败查不到原因 | task pod 跑完即删,日志跟着没 | 远程日志写 MinIO(ADR-092) |
| 血缘整个是坏的 | OpenSearch demo 证书 SAN 里没有服务名 | ADR-093;DAG 全绿,血缘 API 查得到那条边 |
| 查询审计停了 33 小时 | event listener 根本没注册,且无任何报错 | 修好;offset 会涨,审计表最新记录 0 分钟前 |
| 镜像 tag 指向拉不到的镜像 | 同步下去就起不了 notebook | 停回存在的 tag + 加检查 |
| JupyterHub 卡死整个 Application | prePuller hook 拉不到镜像就永远 Pending | 关掉 hook |

**顺带确认的**:10 个对人暴露的组件全部真的带账号密码走完了登录
(`scripts/52-` 扩到全部),Superset 那次不是普遍现象。

### 2026-09-03:云资源成本 + 数据盘迁移

**数据盘从 200G ESSD AutoPL 换成 150G ESSD PL0**,每月约 302 → 113 元。
`ModifyDiskSpec` 不支持把 AutoPL 转成任何其他类型(三种目标全被拒),只能
新建 + 迁移,脚本是 `scripts/57-migrate-data-disk.sh`。**旧盘还挂着没删** ——
确认几天没问题之后再删,脚本末尾打印了那两条命令。

**"自动关机省钱"从 2026-08-15 装上起就没省到过。** 看门狗执行的是
`/sbin/shutdown -h now`,从系统内部关机时阿里云记的是 `KeepCharging`,
停机期间 CPU/内存照常按运行中收费。翻日志算了一下:光当前这台实例就有
33 小时是这么白烧的。改成走 EcsRamRole 调 StopInstance API;顺带发现那个
RAM 角色**在实例上根本没挂**(迁可用区时没带过来),已补上。

**修这个的过程本身踩了两次同一个坑,值得记住**:安装脚本用一个
**未加引号的 heredoc** 把看门狗写进目标文件,而我在注释里用反引号包了
一句关机命令 —— 反引号在那种 heredoc 里是命令替换,**写文件的那一刻就在
云主机上执行了**,机器因此被关了两次。注释也是代码。

---

### 已经处理掉的卡点(2026-09-02 提出时还没解决)

**ingress-nginx 反复 OOMKilled,平台外部整个访问不通。** 内存上限 256Mi,
已重启 40 次,当前 255Mi/256Mi、CPU 1960m(在疯狂 GC)。节点本身还有余量
(内存用了 51%)。

改法很直接:`templates/platform-apps/ingress-nginx.yaml` 里把
`limits.memory` 提上去。**但它是集群级组件,和另一个项目共享同一个 k3s**,
按 CLAUDE.md 的硬约束,这类改动必须先问过再动。

---

## 上一轮:P1.5 + 后续七项全部实机验收通过(2026-08-30)

**2026-08-30 下半场**:`docs/project/next-boot-checklist.md` 里 15~20 项
(audit 探针 / Trino 查询血缘 / 推理留痕 / OA 治理接口 / Iceberg 表维护 /
Iceberg 备份)+ 脚本验不了的那三条(越权、组审批按钮、作业详情页)全部
在真集群上跑过一遍并通过。

**上集群一共跑出 14 个只有实机才会暴露的问题**,全部已修 + 有测试或检查
兜住。挑最值得记住的:

| 问题 | 为什么本地测不出来 |
|---|---|
| 三个自建应用的**镜像 tag 全落后于源码** | ArgoCD Synced、Pod Running、单测全绿 —— 唯独集群上跑的不是你写的代码。已加 `check-image-tag-freshness.py` 进 CI |
| `/api/table-governance` 的**参数格式和它自己的文档不一样** | 所有相关测试都把 `lookup_table_governance` 整个 mock 掉了,而错就藏在被 mock 的那层 |
| 推理留痕存下来但**查不出是哪个模型** | 测试夹具里的 `ce-source` 是编的,而且**编得比真实值好看** |
| 「从数据目录点一下去申请」**部署了但从来没工作过** | 地址读一个从没人建过的 secret key,写进 OM 的是空串 —— 不报错 |
| 门户「流作业」栏一直是 ForbiddenException | 页面不空白不报错,只显示一句看起来像"暂时没有"的话 |
| 门户作业日志显示成 `b'\xe5\xa4\x84...'` | k8s 客户端返回的是**字节串的 repr 字符串**;纯英文日志看着只是多个前缀,中文才完全不可读 |
| `model` 是 Calcite 保留字 | 同一个文件顶部刚写着"这次提前避开保留字" —— 现在改成所有字段一律加反引号 |
| `kserve/agent` 不在镜像清单里 | 它只有配了 logger 才被注入,躺在 ConfigMap 的 JSON 字符串里,扫 `image:` 行永远看不到 |

**这批问题的共同形态值得单独说**:大部分不是"报错了",是**"绿着但不
工作"** —— 状态页正常、日志无异常、测试全过,功能就是没生效。这类问题
只有真的去用一次才会发现,这也是这份清单存在的理由。

### 之前那一轮(P1.5 六条)

`./scripts/46-verify-p15.sh` **28 条全过,0 失败**(1 条跳过:权限门户里
没有申请记录,看不出状态中文化)。日志在 `logs/verify-p15.log`。

| P1.5 那六条 | 实机状态 |
|---|---|
| 分析师的 SQL 入口 | ✅ SQL Lab 上 `current_user` 是本人、没 grant 的表被拒、脱敏生效 |
| 门户角色工作台 | ✅ 按角色显示、我的表权限渲染出真实表名 |
| 作业发布升级 | ✅ 多文件 + 补数(表里真的多出 `2026-08-01` 那 4 行)。**定时路径仍没被触发过** |
| 建表注册工具 | ✅ 字段说明/分区进了真实表结构、2 级表被挡住 |
| 审批体验 | ✅ 三条通过;续期和到期提醒还没在有真实数据的情况下走一遍 |
| 文档职责重构 | ✅ |

**这一轮验收的最大收获不是"六条都通过了",是验收脚本自己有三个 bug**,
而且每个都是"检查跑了、结论不可信":拿空字符串当日志判断(一条假阳性
一条假阴性)、`| while read` 子 shell 把记账和失败一起吞掉、断言 302 而
urlopen 默认跟随重定向。外加我自己用 `flask_login.login_user` 写 SQL Lab
的测试,差点把好的功能报成坏的。**详见
[`capability-matrix.md`](capability-matrix.md) 底部那节** —— 这是第五、
第六次了。

## 真正的弱点(2026-08-30 主动盘点)

使用方问"是不是还有很多没做好的" —— 是。逐条查证后列在
[`production-readiness-gaps.md`](production-readiness-gaps.md) 末尾那节,
最要紧的三条:

1. **全平台零个「生产验证」**(能力表 62 格:集成验证 50 / demo 7 / 未验证 2
   / 计划中 2 / **生产验证 0**)。所有结论都来自一台单节点云主机。
2. **MinIO 单副本** —— ~~不备份~~ 备份 2026-08-30 补上并实机跑通
   (`apps/iceberg-backup/`,audit 33MiB / 1239 个对象)。但
   **cloud-full 这一档备份写回同一个 MinIO**,防误删可以、防不住 MinIO
   本身没了;`minio_replicas` 三档都是 1;**恢复路径(数据文件 + Hive
   Metastore 表结构两半)一次都没演练过**。
3. ~~**Iceberg 表维护完全没做**~~ —— 2026-08-30 做完并实机验证
   (`jobs/iceberg-maintenance/`,12 张表 3/3 全成功)。

## 还没验到的(不要当成已完成)

- ~~定时路径从没被真正触发过~~ **2026-08-30 已验**:克隆一份 CronWorkflow、
  把时间改成两分钟后,它**自己**起了一个 workflow 并跑成功。已经做成
  `46-verify-p15.sh jobs` 里的一条,以后每次开机都会跑。
  (真实那条 `daily-order-summary` 定在 UTC 01:30,云主机那个点基本关着,
  所以它自己的 `status` 仍然是空的 —— 那是**排期问题不是能力问题**。)
- ~~续期 / 到期提醒~~ **2026-08-30 已验**(用一条 5 天后到期的临时 grant):
  门户上出现「1 项即将到期」角标、那行标黄、显示"还剩 4 天"、带续期入口,
  长期那条正常显示到期日;点续期建出的是一条 **`pending`** 的新申请(不是
  直接延期),理由带 `[续期]`,审批链真的有两级(table_owner + manager);
  重复提交被 409 挡住。临时数据已清理。

  **过程中确认了一件事**:续期对**没走过建表注册工具的表**会被拒,提示
  "请先用建表注册工具登记这张表" —— 因为目录里查不到它的安全等级。
  这是正确行为(和首次申请同一套校验),而且正是使用指南里警告过的
  "直接在 Trino 里手写 DDL 建的表在目录里是隐形的"。`iceberg.demo.orders`
  就是这样一张表(`scripts/08` 直接建的)。
- **两个真实账号验越权**(A 打不开 B 的作业详情)、**组权限申请的批准
  按钮**:要真人浏览器登录。
- **门户「我的作业」详情页的外观**:接口验过,页面长什么样没人看过。

## 下次开机要验的清单

在 [`next-boot-checklist.md`](next-boot-checklist.md),7 条,每条带判据和
"失败长什么样"。最要紧的两条:**SQL Lab 的 impersonation**(ADR-084 唯一
没验的一环)和**提权路径确认已堵**(建表 owner → 自己批自己)。

## 需要 使用方配合的

- **真实告警渠道、域名+ICP 备案、多节点演练**:按他的安排,等上测试/生产
  环境再说,现在只留好配置。
- **真实 HR/IdP 对接方**:组织架构同步现在是虚拟占位数据,接真实系统需要
  他指定对接方。
- 除此之外没有卡在他身上的事。

---

### 云主机状态

> **⚠️ 2026-08-30 09:20 开不起来:可用区库存售罄**(`OperationDenied.NoStock`)。
> 这台是抢占式实例,cn-wulanchabu-a 的 g9i **以及同代族的 r9i/c9i 一起售罄**
> —— 改规格也解决不了(阿里云只允许同代族互换)。
>
> 处置和判断方法写进了
> [`troubleshooting.md`](../operations/troubleshooting.md)(搜 `NoStock`)。
> `scripts/32` 现在支持 `WAIT_FOR_STOCK_MIN=30` 重试等待。
>
> **后果**:这一批没上过集群的东西(audit 探针、Trino 血缘、推理留痕、
> OA 模式、数据目录跳转链接)全部继续挂着,验不了。


**已停机**(2026-08-30 03:00 左右,经济模式,不产生计算费用)。这一轮开机
约 50 分钟,专门用来做 P1.5 的实机验收。

开机 `scripts/32-start-cloud-vm.sh`,停机 `scripts/26-stop-cloud-vm-economical.sh`。

停机前:77 个 ArgoCD Application 全部 Synced/Healthy,零异常 pod,六条黄金
链路最近一次全部 Completed,验收产生的临时数据(表、grants、申请记录、
CronWorkflow)全部清理。

> **判断"现在好没好"的方法**:`kubectl get pods -A` 里的 Error 是**过去
> 某一刻**的快照,不是现在的状态 —— 开机后总会有一批(组件还没就绪时定时
> 任务先跑了)。要看的是每个 CronJob **最近一次**那个 pod。

**这台机器不是我们独占的** —— 详见 `CLAUDE.md`:任何会让 k3s 停掉/重装、
停机释放、或改集群级资源的操作,必须先停下来问。

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
- **开发环境的凭据在承载真实数据之前必须整体轮换一次** —— 这是上生产的
  硬门禁,记在
  [production-readiness-gaps](production-readiness-gaps.md)。开发测试阶段
  按既定安排不处理。轮换步骤见
  [troubleshooting](../operations/troubleshooting.md#凭据轮换)。

## 结束一段工作前必须确认(照着过一遍,不要跳)

- [ ] `git status` 干净,该 push 的都 push 了
- [ ] 计费资源现在的状态说清楚了(开着/停了,为什么)
- [ ] 后台任务/SSH 隧道是不是还开着,写进了上面那节
- [ ] 这次做的事,哪些是真实验证过的、哪些只是写完代码没测,分层说清楚
- [ ] 有没有手工改过集群但没回写 git 的操作(有的话赶紧记下来或者补写)
- [ ] 失败但没解决的事情,写清楚现象+已经排除的原因,别人接手不用重新排查一遍
- [ ] **能力有增减的话,`docs/project/capability-matrix.md` 更新了吗**(新增的一条,ADR-057)
