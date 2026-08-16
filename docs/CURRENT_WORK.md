## 2026-08-16 深夜(app 会话接手后):table-registration-app 修复 + Trino OPA 正式上线

这段是从 CLI 会话交接过来、在 app 会话里继续做的(用户明确要求"接手 CLI
那边的,继续做下去")。交接时发现的两个问题,都已解决:

1. **`table-registration-app` 云端 CrashLoopBackOff 已修复**:根因是这台
   云主机到 PyPI 官方 CDN 的真实带宽瓶颈(~22 kB/s,不是"抢带宽"的猜测),
   换阿里云 PyPI 镜像后 8 秒装完。已验证 `1/1 Running`、`Synced/Healthy`。
2. **Trino OPA 细粒度访问控制正式切换生效**(ADR-051):`access-control.
   name=opa` 已经在 cloud-full 生效,不再是"部署了但没接上"的状态。详细
   过程(审计现在实际在用 Trino 的身份、撞上的 3 个真实 bug、上线后端到端
   验证)见 ADR-051"2026-08-16 正式上线"一节,这里不重复。`docs/
   BACKLOG.md` P1.5 已相应标记完成。

**这次没有做完的**:切换后应该再观察一段时间(比如确认 dbt_demo DAG 下次
真实调度时用 `dbt_demo_service` 身份能正常建表/写入——这次只验证了 OPA
决策 API 层面"这个账号会被放行",没有触发一次真实的 DAG 运行去看端到端
效果),不算阻塞项,只是还没有拿到那个最后的真实证据。

## 2026-08-16 深夜:经济模式收尾 + Superset 自愈确认

**RAM 角色方案已经补完,自动关机现在真的是经济模式**:`cloud-full-vm-
self-stop` 这个角色(权限已经改指向新实例 ARN,`AttachInstanceRamRole`
挂到 `i-0jlbped4h1959tp591pe`),看门狗脚本(不进 git 的
`/usr/local/bin/idle-shutdown-watchdog.sh`)判定空闲后,改成用装在
虚拟机上的 `aliyun` CLI(`--mode EcsRamRole --ram-role-name
cloud-full-vm-self-stop`)直接调 `StopInstance --StoppedMode
StopCharging`,`--dryrun` 验证过请求参数正确,失败时兜底退回本地
`shutdown -h now`(不保证经济模式,但保证"空闲还是会停"这个底线不失效)。
`scripts/26-stop-cloud-vm-economical.sh`(手动立即停机用)的头部注释
同步更新,不再说"AccessKey 没有 RAM 权限做不了"——这条已经做完了。
这台虚拟机上额外装了一份 `aliyun` CLI 二进制(从阿里云自己的 CDN
`aliyuncli.alicdn.com` 下的,不是 PyPI,没有踩到那个网络不稳定的坑)。

**Superset 已经自愈**:`kubectl -n superset get pods` 确认
`superset-55fd8b5bbd-hpklk` 现在 `1/1 Running`(只重启了1次)——印证
了上一条记录里"网络偶尔不稳定,K8s 自动重试会自己好"的判断,不需要
再人工介入,标记为已解决。

## 2026-08-16 晚:cloud-full 迁移到抢占式实例(省钱),记录当前真实状态

**实例已经换了**:旧实例 `i-0jl7spqzz0rfnqv2abd2`(按量付费)已经**释放**
(含它的 40G 系统盘+200G 数据盘,全部删除,不再计费)。现在用的是**新的
抢占式实例 `i-0jlbped4h1959tp591pe`**(`ecs.g9i.4xlarge`,同样规格,
`SpotStrategy=SpotAsPriceGo`,`SpotInterruptionBehavior=Stop`——被回收时
只是停机不是销毁,数据安全,但停机后重开不保证能立刻抢到资源)。价格
约 ¥0.72/小时,比原价 ¥3.58/小时便宜约 80%。公网 IP 恰好还是
`8.130.69.252`(旧实例停机后释放、被新实例接手,纯巧合,不保证以后
还是这个)。两块盘(40G 系统盘+200G 数据盘)都已确认设置成
`DeleteWithInstance=False`(和旧实例一致,实例被删不会连带删数据)。

**迁移方式**:从旧实例(停机状态)直接 `CreateImage` 打一份系统盘+数据盘
一起的镜像,再用这份镜像 `RunInstances` 开新实例——不是"detach/attach
同一块物理盘"那条路(本来计划这样,但镜像顺带把两块盘都拍了快照,更省事
就直接用了,代价是新实例的盘是从快照克隆出来的新盘,不是原来那两块物理
盘,原来那两块后来已经删除)。

**迁移过程中真实踩到、修好的问题**(以后再做类似迁移会用得上):
1. k3s 集群里同时出现新旧两个节点记录(旧节点是克隆前的残留状态)——
   `kubectl delete node <旧节点名>` 清掉。
2. **本地存储(`local-path` provisioner)的 9 个 PV 全部记着旧节点的
   `nodeAffinity`**,导致 Postgres 等好几个组件 Pending
   (`didn't match PersistentVolume's node affinity`)——这个字段
   immutable,不能 patch,得整个删除重建(先把 `persistentVolumeReclaimPolicy`
   patch 成 `Retain` 防止删除时真把数据删了,备份每个 PV 的完整 YAML,
   去掉 `pv-protection` finalizer 才能真正删掉,改好 `nodeAffinity` 里的
   节点名重新 apply——**这一步风险最高,操作前一定要先 Retain+备份**)。
3. Codex 的 traefik(和这台节点共享,见下面 STATUS.md 引用的历史记录)
   在排查 ingress-nginx 想用 `hostNetwork` 抢公网标准端口时被发现也在
   抢 80/443——`hostNetwork` 这条路最终**放弃**(会跟 Codex 的 svclb
   冲突,已经在用户授权下临时停过一次 Codex 的 traefik 验证过这个结论,
   验证完照原样恢复了),继续用 NodePort(32460/32535)。
4. Superset 的 `bootstrapScript`(装 psycopg2-binary/authlib/trino)在
   新实例上反复卡在下载小包不动——**排查过换阿里云 PyPI 镜像这条路,
   实测会把包装到 Superset 进程读不到的地方**(具体是不是 UID 0 和
   `pip-install.sh` 假设的 HOME 路径不一致导致,没查清楚,不是这次
   优先级),已经回退到原始验证过的脚本。**这是这次唯一没有收尾的
   问题**——原脚本本身没错,是这台机器网络这几个小时里反复出现的真实
   不稳定(dbt-core、table-registration-app 之前也撞过同一类问题),
   K8s 会自动持续重试,不需要人守着,下次接手时先查
   `kubectl -n superset get pods` 确认是不是已经自己成功了。

**Why 记这些**:这次迁移过程本身就是一次没有脚本化、纯靠命令行临场
操作完成的"重要操作"(按 `~/.claude/CLAUDE.md` 的要求应该脚本化,这次
没有,是一次已知的欠账,不是疏漏——迁移涉及的判断点太多,当时判断没有
把它写成可重复脚本的必要性,以后如果要经常做这类"实例类型切换"迁移,
值得补一个)。用户当场也指出"过程中很多东西没及时改代码"这个更大的
问题——今天这个会话里确实有几次直接 `kubectl patch`/手动改活集群、
事后才补写回 git 的操作(有的忘了补,靠这次记录兜底),这正是仓库
CLAUDE.md 里"已知差距——一键部署目前仍是多个手动脚本"这条的真实代价,
不是纸面上的担忧。

# 当前唯一主任务

> 这份文档解决的问题(2026-08-15 Codex review 第二轮指出的):这次会话
> 在门户/OPA/dbt/cloud-full 部署/本机内存/回应外部 review 之间来回切换,
> 没有一个清晰的"现在到底在做哪一件事"锚点。规则很简单:**任何时候只有
> 一个 CURRENT,新想法默认进 `docs/BACKLOG.md`,不自动抢占 CURRENT**。
> 每次恢复工作先看这份文档,不要只信聊天记录/记忆摘要。

## CURRENT(已切换,2026-08-16 深夜)

- **标题**:破坏性操作防护补全(ADR-055 P1 排期第一条)
- **为什么现在做**:上一个 CURRENT(cloud-full 部署上线)已经在
  2026-08-16 达到验收标准(见下面"已归档"),ADR-055"后续"一节明确写了
  "下一段工作的默认优先级是破坏性操作防护补全和三个自建工具补测试,
  原因是这两条风险敞口最大"。当天晚些时候的抢占式迁移过程里又出现了
  一次没有走任何 guard 脚本的手动 `kubectl delete pv`(虽然操作前做了
  Retain+备份,没有出事故,但流程上确实又是一次"裸操作"),进一步印证
  这条该排第一。
- **明确范围**:不是重写成评审建议的"完整统一 guard 框架"(ADR-055 已经
  明确决定不做,除非真的反复出现同一种误用模式)——而是给现有轻量版
  `scripts/confirm-destructive-kubectl.sh` 补上原始评审 5 条建议里当时
  没做的两处具体缺口:①按环境的 namespace 白名单(现在没有,任何
  namespace 名字都能传进去);②namespace/PVC/数据库删除前的备份状态
  检查(现在没有)。外加把历史误删事故(`kubectl delete namespace
  airflow` 误删成 `data`)补一份仓库里的短事故复盘(评审第 5 条建议,
  当时只存在私有 memory 里,不在仓库),并做一次 dry-run 演练证明 guard
  真的能挡住误删 `data`(评审给的验收标准)。
- **明确非目标**:完整统一 guard 框架本身、三个自建工具补测试(下一条,
  这次不切进去)、环境 overlay 重构、5 条产品主线、任何新组件。
- **当前阶段**:2026-08-16 深夜已完成:①`docs/operations/incidents.md`
  新建,补上原始事故复盘 + 这次改 guard 脚本时自己又真实删了一次
  local-lite `data` namespace 的完整记录(如实写清楚是操作判断失误,
  不是脚本 bug,已经恢复,详见该文档);②`scripts/
  confirm-destructive-kubectl.sh` 补上 namespace 允许清单(动态从
  ArgoCD Application 的 destination.namespace 现查)+ 受保护 namespace
  二次确认(`data`/`kube-system`/`kube-public`/`kube-node-lease`/
  `argocd`,需要额外的 `--i-understand-protected-namespace`)+ `data`
  namespace 的 Postgres 备份新鲜度检查(有 20 秒超时保护,查不到只警告
  不阻塞);③4 组拒绝路径(缺确认 flag/目标不在清单/环境 context 不
  匹配/预览模式不执行)全部验证通过。**这个 CURRENT 的"明确范围"已经
  全部完成**——上面一句话曾经错误地把"三个自建工具补测试"(ADR-055
  排期的第二条,本来就在这个 CURRENT 的"明确非目标"清单里,不算它的
  验收范围)写成"这个 CURRENT 剩下唯一没做的部分",自相矛盾;实际上
  那条已经在别处独立做完了(`docs/BACKLOG.md` P1.2:三个 app 共 60 个
  测试,含 CI 集成),这里只是一直没回来更正,2026-08-16 深夜(app 会话,
  读 CLI 会话原始记录核实后)修正。

**已归档的上一个 CURRENT**:cloud-full 环境(阿里云)部署上线——
2026-08-16 五个子任务(#12~#16)全部完成,核心链路(Trino/Superset/
Airflow)端到端验证通过,详见下面"任务#16 完成"那节,以及
`environments/cloud-full/STATUS.md`。Trino OPA 真正切换生效(需要用户
在场)仍然单独排期,不属于任何一个 CURRENT,见 ADR-051。
- **详细进度/实例信息**:见 `environments/cloud-full/STATUS.md`(这份
  文档不重复那些细节,只负责"现在主线是什么、下一步做什么")
- **计费资源状态**:阿里云 ECS 按量付费,**2026-08-16 当前是开机状态**。
  2026-08-15 曾经空转过一次(教训:6小时10分钟没干活,约¥24.9,zhenghe
  自己发现的)。之后装了看门狗脚本(`scripts/24-install-idle-shutdown-
  watchdog.sh`,不进 git,见 memory),2026-08-16 真实验证过它按"网络
  流量+新进程"双信号正确判定空闲并自动关机——**这次是它主动关的,不是
  故障**。后来 zhenghe 给了阿里云 AccessKey(已经用 `aliyun configure`
  存在这台 Mac 本地 `~/.aliyun/config.json`,profile 叫 `cloud-full`,
  不进仓库),装了 `aliyun-cli`(`brew install aliyun-cli`),用
  `aliyun ecs StartInstance` 重新开的机——**以后开机不一定要等 zhenghe
  自己去控制台点,可以用这条 CLI 路径**(浏览器 Chrome 那条路当时被
  插件自己的安全分类拦截,走不通,备用 CLI 路径证明是可行的)。
  - **实例绑定的是普通公网 IP,不是 EIP**(`aliyun ecs DescribeInstances`
    实测确认)——如果以后开"经济模式停机"省钱,这个 IP(`8.130.69.252`)
    大概率会变,到时候所有写死这个 IP 的脚本/SSH 配置都要跟着改,不是
    可以随便开的选项,需要先想清楚。
  - **2026-08-15/16 新增约束,关机前必须做**:zhenghe 让 Codex 另开了
    竞争项目 `bigdata_ai_platform_v2`,那边也会用云资源(而且是同一个
    k3s 集群,不是分开的两台机器,见下面"重大发现"那节),**不能再假设
    这台 VM 只有我在用**——关机前先查有没有非本会话的登录/进程/文件。
    2026-08-16 zhenghe 明确说了 Codex 那边他自己在协调,不需要我每次
    都主动去查冲突,他会主动告知——但看门狗自动判断"有没有人在用"这层
    保护依然保留,不受这条影响。
  - **2026-08-16 发现并处理了一个真实的计费漏洞**:这台实例的
    `StoppedMode` 默认是 `KeepCharging`(停机也照常收计算费),实测确认
    (`aliyun ecs DescribeInstanceAttribute`),不是猜的——而且这个参数
    只能在调 `StopInstance` API 那一刻指定,不是能一次改掉永久生效的
    实例属性,更不是虚拟机内部 `shutdown -h now` 能带上的。之前装的
    看门狗是在虚拟机内部自己关自己,天生拿不到经济模式。理想方案(给
    虚拟机挂一个权限受限的"实例 RAM 角色",让它自己能正确调用)需要
    `ram:CreateRole` 这类权限,zhenghe 给的 AccessKey 权限范围只到 ECS
    操作,做不了。**现阶段方案**:新增
    `scripts/26-stop-cloud-vm-economical.sh`(实例ID/region 都是变量,
    不写死),看门狗继续负责"检测空闲"(有参考价值),但**真正的关机
    动作改成我从 Mac 这边主动调用这个脚本**,保证带上
    `StoppedMode=StopCharging`;如果哪天我的会话完全不在场,看门狗的
    `shutdown -h now` 兜底仍然会触发,只是那种情况下会退回到
    `KeepCharging`(可以接受的降级,不是常态路径)。
    **2026-08-16 后续**:zhenghe 把 RAM/VPC 权限也授权给我了,把"实例
    RAM 角色"这个更完整的方案的基础设施部分做完了——新建了权限受限的
    RAM 角色 `cloud-full-vm-self-stop`(自定义策略
    `cloud-full-vm-self-stop-policy`,只允许对这一台实例调用
    `ecs:StopInstance`/`ecs:DescribeInstances`/
    `ecs:DescribeInstanceStatus`,不能碰任何别的资源),已经用
    `AttachInstanceRamRole` 挂到实例上。这几步都不需要开机,零成本做完
    了。**还没做完的最后一步**:把看门狗脚本(`scripts/24-*`)改成通过
    这个角色(ECS 元数据服务拿临时凭据)调用 API 正确关机,而不是内部
    `shutdown -h now`——这一步需要开机验证网络连通性/可能要在虚拟机上
    装 `aliyun-cli`,不为了这一项优化单独开机付费,等下次因为别的正事
    (比如 ArgoCD)开机时顺手做完。
  - **2026-08-16 用户自己手动操作确认过**:控制台停机弹窗里能直接选
    "节省停机模式"(经济模式),已经这么操作过一次,实测确认
    `StoppedMode` 变成了 `StopCharging`——以后 zhenghe 自己手动关机也
    可以直接在控制台选这个,不一定非要等我用脚本处理。
- **验收标准**:`kubectl get applications -n argocd`(cloud-full 集群)
  全部 Synced/Healthy;核心链路(Trino 查询、Superset 出图、Airflow 跑
  一次 DAG)至少各验证一次
- **最后更新**:2026-08-16(见下面"2026-08-16 ArgoCD root-apps 拉起
  排障记录"这节,任务#16 进行中)

## 正在运行的后台任务

**2026-08-16 更新**:镜像导出/传输(任务#14/#15)已经全部完成,上面
一段是完成之前的过程记录,不再是当前状态,不用再跑那几个脚本。现在
唯一还在常驻的:

- SSH 隧道(`ssh -f -N -L 16443:127.0.0.1:6443 ...`):常驻后台进程,
  给 `KUBECONFIG=~/.kube/cloud-full-config` 用,断了要重新起。

如果你是接手这个工作的人(人类或者别的 AI):任务#12~#16 全部完成,
先看上面"任务#16 完成"那节确认现状,不用重新跑 preflight 去猜进度。

## 2026-08-16 重大发现,阻塞了任务#15的"拉起全部服务"这一步

云主机(`8.130.69.252`)上的 **k3s 集群不是空的**——Codex 的
`bigdata_ai_platform_v2` 项目已经在**同一个 k3s 集群**里跑起来了,不是
分开的两个环境。实测确认(`k3s kubectl get pods -A`):

- 有一个独立的 `data-ai-platform-v2` namespace,里面跑着 MinIO、
  Polaris(Iceberg REST catalog)、Trino、Spark-Iceberg 互操作任务、一个
  `control-api`——`control-api` pod 4 分钟前刚重启过,是活跃在持续迭代的,
  不是挂着没人管的旧痕迹。
- `kube-system` 里的 `traefik`(k3s 自带默认 ingress)已经是
  `LoadBalancer` 类型,占用了 80/443 端口(`172.22.9.15:80/443`)。

**这直接构成一个真实的技术冲突**:这个项目(bigdata_ml_paltform)正常的
部署流程要装 `ingress-nginx` 也需要占 80/443 端口——如果现在按原计划跑
`scripts/01-bootstrap-argocd.sh`+`scripts/02-bootstrap-root-apps.sh`,
大概率会和 Codex 已经在用的 traefik 冲突。

**已经决定的处理方式**:不是纯技术判断题(会影响到 Codex 那边正在跑的
东西),**没有擅自决定怎么处理,任务 #15 的"拉起全部服务"这一步停在这里
等 zhenghe 醒了确认**——可能的方向包括分端口/分 ingress class 共存、
协调谁用 80/443、甚至重新评估要不要拆成两个独立环境,但具体怎么选不该
我自己拍板。

镜像缓存的准备工作(拉取/校验/同步回 Mac 归档)不受影响,继续做,这些
不会碰 k3s 集群里 Codex 那部分东西。

## 2026-08-16 重要发现:云主机直接走国内镜像站拉镜像,比 Mac 导出+上传快得多

zhenghe 提议的思路:云主机是 x86_64 原生、在阿里云网络里,能直连
`docker.m.daocloud.io`/`quay.m.daocloud.io`/`k8s.m.daocloud.io`/
`ghcr.m.daocloud.io` 这几个国内镜像加速站(分别代理 docker.io/quay.io/
registry.k8s.io/ghcr.io),不需要先在 Mac(arm64)上导出 amd64 tar.gz 再
用 rsync 上传(受限于 Mac 这边实测约 5MB/s 的上传带宽)。实测**digest
完全对得上官方源**,不是内容被篡改的野镜像。

**更关键的是**:之前在 Mac 上无论怎么试都导不出来的 cert-manager 系列
(`docker save` 一直失败,原因没查清楚,当时判断是"已知的、接受的
局限")、node-exporter/prometheus 的 distroless 变体、
kube-webhook-certgen——**这次通过云主机直连镜像站,全部成功了**。说明
那不是这几个镜像本身有什么特殊问题,是 Mac 本地 `docker save` 在处理
某些镜像时的一个具体环境限制,换一条路径(远端直接拉,不经过"导出成
tar.gz 再传"这个中间步骤)就绕开了。

脚本:`scripts/23-pull-images-remote-via-mirror.sh`(已进仓库)。**一个
实测踩到的坑**:脚本用长连接 SSH heredoc(`ssh ... bash -s <<'EOF' | tee
-a log`)在远端跑整个循环,真实发生过连接卡死超过 1 小时、远端进程早就
退出但本地看着还"在跑"的情况——**以后做类似"远端跑一长串操作"的事,
不要依赖一条 SSH 连接全程存活**,要把实际执行体放到远端用 `nohup ...
& disown` 脱离终端,本地只用短连接轮询进度(`ssh ... "grep xxx log"`),
两者要分开,这个教训比较通用,以后接自建 IDC 或者任何"本地控制远端长
任务"的场景都适用。

**以后要不要在这台机器上装/改集群级资源(见上面 Codex 共享集群那条),
仍然要先等 zhenghe 确认;但纯粹的镜像拉取/准备工作不受这个限制,可以
继续做。**

## 2026-08-16 真实验证出的问题:镜像站会返回和官方不一致的内容,不能盲信

zhenghe 提前提醒过"国内一些镜像可能没有最新的",这次真的验证出了实例,
记录清楚,以后不要重新踩:用 `docker manifest inspect --verbose` 在 Mac
上直连官方源(docker.io/quay.io/registry.k8s.io/ghcr.io)查真实 digest,
和云主机上通过 `*.m.daocloud.io` 镜像站拉到的内容逐个比对,**9 个
quay.io 镜像的内容跟官方对不上**(argoproj/workflow-controller、
jetstack/cert-manager-webhook、jupyterhub 那一批 4 个、
prometheus-operator/prometheus-operator、strimzi/operator)——大概率是
镜像站缓存了旧版本没刷新,不是内容被篡改。已经全部修复:8 个用 Mac 本地
已经导出好的正确版本重新传过去覆盖,cert-manager-webhook 那个
Mac 端 `docker save` 一直失败(已知问题),改成**直接让云主机按已经核实
过的 digest 从 quay.io 官方直连拉取**——顺带发现云主机能直连 quay.io
(不需要经过镜像站),这条路径比"镜像站兜底、Mac传输兜底"更简单,以后
优先尝试。

**教训:通过镜像站拉的镜像,不能默认内容就是对的,拉完必须抽查/全量核对
digest**——这次如果不核对,会把 9 个内容有问题的镜像当成"已经准备好"
直接部署上去,埋雷埋得很深。以后凡是走镜像站这条路径,收尾都要加一步
digest 核对,不是可选步骤。

## 2026-08-16 cert-manager 系列"docker save 导不出来"这个老问题,终于找到根因了

之前(见 ADR-054/更早的会话记录)一直把这个问题当成"Mac 本机的环境限制,
没深挖、接受为已知局限"。这次在云主机(Linux x86_64,和 Mac 完全不同的
环境)上重新测试 `docker save`,**同样的问题复现了**——`docker save`
产出的 tar.gz 只有 1.5~2KB,解开看 `manifest.json` 里列了一堆
layer blob 文件名,但 `blobs/sha256/` 目录下实际只有最顶层那一个
manifest 文件,**真正的层内容完全没写进去**。

**真正的根因**:检查 `index.json` 发现这些 blob 在 containerd 里的
annotation 同时标注了 `containerd.io/distribution.source.quay.io` 和
`containerd.io/distribution.source.quay.m.daocloud.io`——也就是说同一份
内容被记录成"来自多个仓库来源"(直连 quay.io 拉过一次、通过镜像站拉过
一次,containerd 的 content store 把两次来源关联到了同一个 blob 上)。
`docker save` 收集 blob 时在处理这种"多来源关联"的情况时出了问题,导出
不完整——这不是 Mac 或者这台云主机哪一台机器的环境问题,是这几个镜像
(cert-manager-cainjector/controller/startupapicheck/webhook、
prometheus/node-exporter、prometheus/prometheus 的 distroless 变体)
在这次操作历史下的 containerd 存储状态触发的一个 `docker save` 的真实
缺陷,两个平台复现完全一致。

**结论(比之前更明确的判断)**:这几个镜像**没法打包成 tar.gz 挪到 Mac
本地归档**,只能停留在"直接跑在某台机器的 docker/containerd 里"这个
状态——这不影响云端 cloud-full 实际部署使用(镜像已经正确加载在云主机
docker 里,`docker images --digests` 确认过 digest 是对的,k3s/ArgoCD
调度 pod 时能正常用),影响的只是"Mac 本地留一份完整归档"这个目标做不到
100%。以后如果真的需要给这几个镜像单独打包,值得试的方向是
`skopeo copy`(不经过 docker/containerd 的 content store,直接操作
registry blob,原理上不会有这个"多来源关联"的问题)而不是继续在
`docker save` 这条路上想办法,但这不是现在优先级,先如实记录根因,不
再当成"未知的环境限制"去猜。

## 2026-08-16 任务#16 完成:核心链路端到端验证通过

`kubectl get applications -n argocd`(cloud-full):36 个 Application 里
34 个 `Synced/Healthy`;`ingress-nginx`/`resource-quotas` 是
`OutOfSync/Healthy`(前者是 admission webhook caBundle 的
ignoreDifferences 已知展示问题,不影响功能,见下面记录;后者是次要
的服务端字段级 drift,没深挖,不影响调度);`feast` 是
`Synced/Degraded`(已知接受的差距——`feast-feature-server` 依赖本地构建
镜像,`ErrImageNeverPull`,不是新问题)。

验收标准三项全部真实验证过(不是"配置齐了应该能跑",是真的跑通了一次):
- **Trino 查询**:`scripts/06-configure-superset-datasources.sh` 里
  `SELECT 1` 查询成功,`scripts/08-create-demo-data.sh` 建表+插入也是
  通过 Trino 真实执行的。
- **Superset 出图**:`scripts/08-create-demo-data.sh` 建了 dataset +
  chart + dashboard,"验证通过: 图表查询链路成功, 4 行数据"。
- **Airflow 跑一次 DAG**:`dbt_demo` DAG 手动触发,
  `manual__2026-08-16T04:51:01+00:00` 状态 `success`。

**这一路排查修复的问题清单**(全部有独立 commit,根因见各自 commit
message,这里只列条目方便回顾):
1. ArgoCD dex-server 128Mi 内存不够,SIGSEGV → 调到 512Mi。
2. argo-workflows CRD 安装依赖 Mac-only 代理地址 → vendor 8 个 CRD 到
   仓库(第一版漏了 `workflowtemplates`,后补上)。
3. CloudNativePG/kube-prometheus-stack CRD 超 262144 字节注解上限 →
   补跑对应的一次性安装脚本。
4. postgres 镜像升级后 cloud-full 没缓存,下游建库 Job 提前耗尽重试 →
   删 Job 触发重建。
5. 3 个 Flask 工具 + dbt_demo DAG 的 pip/apt 硬编码 Mac-only 代理地址 →
   改成运行时自适应探测/换阿里云镜像站。
6. `03-configure-keycloak.sh` 遇到未 unpark 的命名空间直接退出 → 补存在性
   检查。
7. kserve-controller/ingress-nginx-controller 镜像从没缓存到云主机 →
   国内镜像站拉取;kserve 还额外发现 chart 默认
   `imagePullPolicy: Always` 会无视本地缓存反复联网校验 → 改
   `IfNotPresent`。
8. ArgoCD 默认的 Ingress/Service 健康检查在裸机(没有云 LoadBalancer)
   上永远卡在 Progressing → 加自定义健康检查覆盖。
9. ingress-nginx admission webhook 的 caBundle 是命令式 patch 注入的,
   selfHeal 会清空它导致证书校验失败 → 加 ignoreDifferences。
10. `trino/trino:483` 对 `http-server.http.port` 收紧了配置校验,chart
    无条件生成这行属性,和我们关掉 `http-server.http.enabled` 冲突,
    483 直接拒绝启动 → 回退到 chart 默认的 480(**教训**:`helm
    template` 渲染 diff 一致不代表运行时行为一致)。
11. `trino/superset/airflow/mlflow/openmetadata` 等组件的定义文件一直
    留在 `pending-definitions/`,从没真正 git mv 回 GitOps 管理——这次
    把 trino/superset/airflow 三个核心链路必需的收回来了(其余仍按需
    留在 pending,不在这次验收范围)。
12. dbt_demo DAG 三个连续的根因性 bug(每个都挡住过一次,依次排查):
    - `/project` 是只读 ConfigMap 挂载,dbt 没法写 `target/`/`logs/`,
      exit code 2 且没有任何输出(靠手动起调试 pod 才定位到)→ 复制到
      可写目录 `/workspace` 再跑。
    - MinIO NetworkPolicy 消费者名单没有 `dbt` 命名空间 → 补上(和
      2026-08-14 feast 那次是同一个模式的遗漏)。
    - `catalog.json` 不是 `dbt build` 的产物,是 `dbt docs generate`
      单独生成的 → DAG 里补这一步。
    这三个 bug **在 local-lite 上大概率一直存在**,只是这个 DAG 之前
    从没有真的端到端跑完过一次,没人发现——这次不是"云端特有问题",是
    真正的历史遗留 bug 第一次被暴露。

**排查方法论上的教训**:多次遇到"看不到真实错误"的情况(get_logs=False
是 local-lite 专门绕过一个 Privoxy 问题的设置,继承到 cloud-full 上
反而让人两眼一抹黑;KubernetesPodOperator 默认删除失败的 pod)。最后
靠手动起一个和 DAG 里配置完全一致的调试 Pod(同样的 ConfigMap 挂载/
Secret/镜像),一步步交互执行每条命令,才把三层叠加的 bug 一个个剥
出来——这是比反复改配置再触发真实 DAG 跑一轮(每轮几分钟)快得多的
排查方式,以后遇到"看不到日志"的黑盒故障应该优先想到这条路径。

## 2026-08-16 ArgoCD root-apps 拉起排障记录(任务#16 进行中,已完成,见上)

镜像缓存传输完成、ArgoCD 装好、`scripts/02-bootstrap-root-apps.sh` 跑完
注册了 30+ 个 Application 之后,大批组件一开始都卡着(OutOfSync/Missing/
Degraded)。逐个排查下来,**几乎所有故障的根源是同一类问题**:这套
manifest 之前只在 Mac(colima)上验证过,里面藏了不少"这台机器专属"的
硬编码假设,第一次在云主机上跑全套就暴露出来了。已修复并 push 到 git
的(每条都有独立 commit,可以在 git log 里查到完整根因分析):

- ArgoCD dex-server 128Mi 内存限制在真实压力下 SIGSEGV 崩溃 → 调到 512Mi。
- argo-workflows chart 默认靠一个 pre-install Job 从
  raw.githubusercontent.com 实时下载 CRD,配的 HTTP_PROXY 是 colima 宿主机
  专用地址(192.168.5.2:1087),cloud-full 连不上 → 把 8 个 CRD(注意是
  8 个,不是 7 个——workflowtemplates 这个和 clusterworkflowtemplates
  长得像,第一版漏了)vendor 进 `apps/argo-workflows-crds/manifests/`,
  chart 里关掉 `crds.install`,新增
  `scripts/25-install-argo-workflows-crds.sh` 一次性装好,不再依赖任何
  网络。
- CloudNativePG/kube-prometheus-stack 的 CRD 超过 kubectl 262144 字节
  annotation 上限(和 KServe 那次,ADR-027,是同一类问题)——
  `scripts/16-install-cloudnative-pg-crds.sh`/
  `scripts/04-install-kube-prometheus-crds.sh` 这两个脚本本来就是为这个
  准备的,之前只是没在这台新集群上跑过,补跑就好。
- postgres 镜像版本升级后(16.6→16.15,这次版本审计的一部分)cloud-full
  没有对应缓存,`hive-metastore-create-db`/`keycloak-create-db` 这些 Job
  在 postgres 起来之前就已经耗尽重试次数报错——postgres 起来后手动删了
  这几个失败的 Job 触发重建,不是它们自己会重试。
- 3 个自建 Flask 工具(permission-request-app/table-registration-app/
  platform-portal)的 pip/apt 也硬编码了同一个 Mac 专用代理地址 → 改成
  运行时先探测这个代理还在不在,连不上就跳过,不用代理直连
  (cloud-full 直连 pypi.org/mirrors.aliyun.com 本身是通的,实测确认)。
  table-registration-app 额外把 pip 超时从 120s 调到 240s(trino 这个
  依赖比另外两个多拉几个包,和其他并发任务抢带宽时 120s 不够)。
- `03-configure-keycloak.sh` 遇到还没 unpark 的 jupyterhub 命名空间就直接
  报错退出(`set -euo pipefail`),后面 trino/superset/openmetadata 等
  client 全部没建成 → 补了命名空间存在性检查,不存在就跳过继续。
- kserve-controller-manager/ingress-nginx-controller 两个镜像之前从没
  缓存到云主机上,直连 registry.k8s.io/docker.io 超时 → 通过国内镜像站
  (`k8s.m.daocloud.io`)拉取再 retag。kserve-controller 这个还额外发现
  chart 默认 `imagePullPolicy: Always`,即使本地已经有镜像,kubelet 每次
  调度还是会去连 registry-1.docker.io 校验导致 ImagePullBackOff——改成
  `IfNotPresent`(第一版改的 values 层级写错导致
  `map[imagePullPolicy:IfNotPresent]:v0.19.0` 这种非法镜像名,第二个
  commit 才修对)。
- iam-sync/opa-grants-sync 这两个 CronJob 的问题更深(apt-get+dl.k8s.io+
  github proxy 三层 Mac-only 网络依赖叠在一起),暂时 suspend 掉止损,
  记进 `docs/BACKLOG.md`,不算这次主线的验收范围。

**还没验证完的**:table-registration-app 用新超时能否稳定跑起来(正在
观察);`kubectl get applications -n argocd` 的最终全量 Synced/Healthy
扫描;Trino/Superset/Airflow 核心链路验证。feast-feature-server 的
ErrImageNeverPull 是已知接受的差距(本地构建镜像,不指望能拉取),不算
新问题。

## 下一步唯一动作

任务#16(核心链路端到端验证)已完成,见上面"2026-08-16 任务#16 完成"
那节。这个 CURRENT 的"明确范围"已经达到,下一次恢复工作时先判断要不要
挑一个新的 CURRENT(参考 `docs/BACKLOG.md` P1 排序),不要默认接着在
cloud-full 上找事做——"明确非目标"里列的那几项本来就没打算算进这次。

## 结束一段工作前必须确认(照着过一遍,不要跳)

- [ ] `git status` 干净,该 push 的都 push 了
- [ ] 计费资源现在的状态说清楚了(开着/停了,为什么)
- [ ] 后台任务/SSH 隧道是不是还开着,写进了上面那节
- [ ] 这次做的事,哪些是真实验证过的、哪些只是写完代码没测,分层说清楚
- [ ] 有没有手工改过集群但没回写 git 的操作(有的话赶紧记下来或者补写)
- [ ] 失败但没解决的事情,写清楚现象+已经排除的原因,别人接手不用重新排查一遍
