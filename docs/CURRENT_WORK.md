# 当前唯一主任务

> 这份文档解决的问题(2026-08-15 Codex review 第二轮指出的):这次会话
> 在门户/OPA/dbt/cloud-full 部署/本机内存/回应外部 review 之间来回切换,
> 没有一个清晰的"现在到底在做哪一件事"锚点。规则很简单:**任何时候只有
> 一个 CURRENT,新想法默认进 `docs/BACKLOG.md`,不自动抢占 CURRENT**。
> 每次恢复工作先看这份文档,不要只信聊天记录/记忆摘要。

## CURRENT

- **标题**:cloud-full 环境(阿里云)部署上线
- **为什么现在做**:local-lite 本机资源已经到物理上限(16GB Mac),
  用户确认生产是 x86_64,需要一个和生产架构一致、资源充足的环境完成
  Trino OPA 真实权限闭环、dbt/SeaTunnel 端到端验证等本机做不完的事。
- **明确范围**:把 `environments/cloud-full/pending-definitions/` 里的
  组件收回常驻、跑通 ArgoCD、完成"从零拉起整套服务"流程,达到和
  local-lite 同等的核心链路验证水平。
- **明确非目标**(这些不属于当前主线,出现新想法先记
  `docs/BACKLOG.md`):Trino OPA 真正切换生效(需要用户在场,单独排期)、
  P1 工程收口(环境 overlay 重构/自建工具补测试/扩大 CI)、5 条产品主线
  (统一开发工作台等)、任何新组件/新功能。
- **当前阶段**:2026-08-15 用户明确指示调整顺序——先把 Codex 提出的版本
  审计清单(`docs/BACKLOG.md` P1.5/P1.6)逐条核实、能升级的先升级(到
  社区/官方确认过再动),再把镜像传云端、拉起全部服务、端到端测一遍,
  最后收口一键部署。5 个子任务已经用 TaskCreate 建好(#12~#16,按顺序
  互相 blockedBy),不是脱离 CURRENT 的新支线,是同一个 CURRENT 的执行
  顺序调整。
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

(用 Bash 工具的 `run_in_background` 机制管理,任务 ID 是这个工具自己
分配的,不是额外自建的 task-runner——这个项目目前是单 Claude 会话操作,
Bash 工具自带的后台任务追踪+完成通知已经够用,没有必要再建一套平行机制)

- 本地 amd64 镜像导出(`scripts/export-image-cache-amd64.sh`):跑在
  这台 Mac 上,输出到 `image-cache-amd64/`,进度看
  `logs/export-image-cache-amd64.log` 或者
  `wc -l image-cache-amd64/manifest.txt` 对比 68 这个总数。
  **2026-08-15 发现的真实情况**:之前以为它一直在后台跑,回来检查发现
  进程已经不在了(`pgrep` 查不到),日志停在 60/68、没有"完成"结尾标记
  ——大概率是这次会话中间有一轮上下文压缩,压缩边界之前用
  `run_in_background` 起的进程没有真的存活下来(不是脚本本身报错崩溃,
  日志里没有任何 error 退出的痕迹)。**教训**:以后不能只凭"之前起过
  后台任务"就假设它还在跑,回来接手时要先 `pgrep` 实际确认进程还活着,
  不要只看日志内容判断"正在进行中"。已经重新用 `nohup ... &`(不是
  Bash 工具的 `run_in_background`,这次故意用更抗会话边界的方式)拉起,
  幂等,已有的 60 个会跳过。
- SSH 隧道(`ssh -f -N -L 16443:127.0.0.1:6443 ...`):常驻后台进程,
  给 `KUBECONFIG=~/.kube/cloud-full-config` 用,断了要重新起。
- 增量传输+加载(`scripts/22-load-image-cache-remote.sh`):每导出一批
  本地镜像就传一批到云端,不用等 68 个全部导出完才开始传,云主机不空转。
  2026-08-15 撞过一次真实故障:containerd 自己的存储没跟着 Docker
  `data-root` 走,把云主机系统盘写满,导致这一批传输/加载失败——已经
  在远程修好(containerd `root` 指到 `/data/containerd`)并回写进
  `scripts/21-bootstrap-cloud-vm.sh`,详见 [ADR-054](decisions/054-cloud-full-bare-vm-bootstrap.md)
  第 5 条,当前正在重新传这一批。

如果你是接手这个工作的人(人类或者别的 AI):先跑
`./scripts/cloud-full-preflight.sh`(设置 `CLOUD_VM_IP`/`CLOUD_VM_KEY`)
看现在是不是 READY,不要凭猜测判断进度。

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

## 2026-08-16 ArgoCD root-apps 拉起排障记录(任务#16 进行中)

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

等上面这批修复全部生效、`kubectl get applications -n argocd` 做一次
全量健康扫描,然后跑核心链路验证(Trino 查询、Superset 出图、Airflow
跑一次 DAG),达到验收标准就可以收尾任务#16。

## 结束一段工作前必须确认(照着过一遍,不要跳)

- [ ] `git status` 干净,该 push 的都 push 了
- [ ] 计费资源现在的状态说清楚了(开着/停了,为什么)
- [ ] 后台任务/SSH 隧道是不是还开着,写进了上面那节
- [ ] 这次做的事,哪些是真实验证过的、哪些只是写完代码没测,分层说清楚
- [ ] 有没有手工改过集群但没回写 git 的操作(有的话赶紧记下来或者补写)
- [ ] 失败但没解决的事情,写清楚现象+已经排除的原因,别人接手不用重新排查一遍
