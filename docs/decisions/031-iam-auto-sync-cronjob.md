# 031. IAM 自动同步 CronJob

- 状态: 已采纳,已验证(2026-08-12,手动触发 Job 跑通端到端)

## 背景

`platform/iam/`(见 ADR-028)改了之后,得有人记得手动跑
`scripts/12-sync-iam.py` 才会真的同步进 Keycloak——权限变更这件事不应该
依赖"有没有人记得跑一下脚本",容易出现"CSV 改了,git push 了,但 Keycloak
里没生效"的静默不一致。加一个 CronJob 每 5 分钟自动跑一次这个脚本。

## 决策

### 权限拆分:这个 CronJob 只能读 git、只能改 Keycloak,不能两者都行

这是这次设计里最重要的一点:CronJob 只有 `pods/exec` 到 `keycloak`
namespace 里那一个 pod(`keycloak-keycloakx-0`)的权限,没有任何 git 写
权限(读 git 是匿名 clone 公开仓库,不需要凭据)。以后如果建自助权限申请
门户(能改 `platform/iam/` 并 push),那个组件反过来只有 git 写权限,没有
任何集群 RBAC。没有任何一个组件同时拿到"能改权限数据"和"能让权限数据
生效"这两种能力,是刻意的纵深防御——哪个组件出了问题(比如权限申请门户
被攻破),攻击者能做的事都有边界。

### 无人值守场景下,新用户不自动建号

`scripts/12-sync-iam.py` 默认给新用户生成随机密码,人手动跑的时候密码会
打在终端里看得到。CronJob 是从 git 现拉的临时 pod,Job 一结束整个文件系统
就没了,自动生成的密码没人能看到,等于建了一个永久登不进去的账号——加了
`--no-create-users` 参数,CronJob 用这个模式跑,遇到 Keycloak 里还不存在
的用户只打警告跳过,新用户必须由人手动跑一次(不带这个参数)才能创建。

## 踩的坑

### 坑 1:amd64 kubectl 在 arm64 节点上模拟执行,触发真实并发 bug

第一次手动触发验证时,Job 报:

```
fatal error: concurrent map writes
...
k8s.io/client-go/restmapper.NewDiscoveryRESTMapper(...)
```

这不是环境抖动,是 kubectl/client-go 自己代码里的一个真实并发 bug(REST
discovery mapper 并发写同一个 map),只有在这台机器(colima 跑在 Apple
Silicon 上,节点是 aarch64)上用 amd64 的 kubectl 二进制、靠 QEMU 模拟执行
时才会稳定触发——原生架构跑同样的命令完全正常(用一个带同样
ServiceAccount 的调试 Pod 手动装 arm64 kubectl、跑同一条 `kubectl exec`
命令,`EXIT=0` 确认过)。修复:initContainer 里用 `uname -m` 动态探测架构,
下载对应的原生二进制,不硬编码 amd64。

这个坑的教训:**"装得上"不等于"跑得对"**,这类"只有模拟执行下才触发的
并发 bug",`helm template`/YAML 语法检查完全测不出来,必须真的手动触发一次
Job 观察实际运行结果。

### 坑 2:git clone 直连 GitHub 卡住

和 Argo Workflows 的 CRD 安装 Job(ADR-026)是同一个网络限制——这台机器上
pod 直连 `github.com` 会卡住/超时,`dl.k8s.io`(下 kubectl 二进制那步)
不受影响,只有 clone 这一步需要代理。修复:给 `sync` 容器加
`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` 环境变量,地址和 argo-workflows.yaml
用的是同一个(`colima ssh -- env | grep -i proxy` 查出来的)。

## 后果

- 代理地址(`192.168.5.2:1087`)是这台本机的地址,换了网络或者重启
  colima 后如果这个 Job 又卡在 git clone 上,先重新跑一遍
  `colima ssh -- env | grep -i proxy` 确认地址没变——和 ADR-026 提醒的是
  同一件事,这类"chart/Job 需要代理才能出网"的坑在这台机器上不是第一次
  踩了,以后新增任何需要直连 GitHub 的组件都要留意。
- `NO_CREATE_USERS` 这个跳过分支目前只打印警告,没有做进一步的告警通知
  (比如发到某个地方提醒人去处理)——等真的有人被这个逻辑挡住、需要手动
  建号时,才知道是不是需要加通知机制,现在没有真实需求先不做。

## 2026-08-12 补充:打开 Alertmanager(ADR-034)后第一批告警就抓到真实问题

`activeDeadlineSeconds` 最早设成 300 秒(等于调度间隔),`kubectl get jobs
-n keycloak` 查出来连续 3 次跑失败,`kubectl describe job` 确认原因是
`DeadlineExceeded`——内存紧张时 `apt-get install git` + `pip install
pyyaml` + `git clone` 这几步本身就可能超过 5 分钟,不是卡死,是单纯不够快。
放宽到 600 秒(10 分钟),`concurrencyPolicy: Forbid` 保证不会因为放宽这个
值就产生并发跑多份的风险。这是打开 Alertmanager 之后第一个通过告警(不是
人肉巡检)发现的真实问题,验证了这件事本身的价值。

## 2026-08-12 再补充:两个更深的坑(colima 9G→11G 重启之后暴露的)

放宽到 600 秒之后,colima 从 9G 扩到 11G、重启一次,这个 CronJob 又出了
两个新问题,都不是"deadline 设小了"这么简单:

1. **`activeDeadlineSeconds` 这个兜底本身可能会迟到**:亲眼看到一个 Job
   跑了 39 分钟都没被 600 秒的 deadline 杀掉。根因不是这个字段配错,是
   集群刚重启、k3s 的 job-controller 在追一堆积压的 reconcile 工作,
   "到期检查"这个动作本身被延后执行了——同一时刻 `kubectl delete job`
   这种最简单的操作也卡了 2 分钟才返回,side 证据指向同一个原因。**教训:
   `activeDeadlineSeconds` 是兜底,不是能百分之百按时生效的保证**,尤其是
   集群刚经历过重启/大范围调度变化的窗口期。
2. **`apt-get` 没有默认超时,会真的无限期挂着**:用 `crictl exec` 进卡住
   的容器,靠 `/proc/<pid>/cmdline` 找到具体是 `apt-get install`
   fork 出来的 `/usr/lib/apt/methods/http` 这个子进程挂住不动——不是
   网络彻底不通(同一时刻宿主机自己 `curl` 同样的地址是通的),是这一次
   TCP 连接本身卡住,apt 的 http/https 方法默认不会自己放弃重连。加了
   `-o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15
   -o Acquire::Retries=2`,让它按重试上限失败退出,不再依赖
   `activeDeadlineSeconds`(本身也可能迟到,见上一条)当唯一的兜底。

这两条合起来的教训:**任何会调用外部网络的脚本/命令,自己就要有超时和
重试上限,不能指望 Kubernetes 层面的 deadline 机制来兜底**——那一层本身
在集群压力大的时候可能也不准时。

修完 `apt-get` 那次之后,重新触发验证,**同一个坑又在另一个命令上复现了
一次**:这次卡住的是 `curl -fsSL -o /tools/kubectl ...`(下载 kubectl
二进制那一行)——curl 默认同样不会主动放弃一个卡住的连接。补了 `-m`
(总超时秒数)给这一步和获取版本号那一步的 curl,`git clone` 那行用的是
`http.lowSpeedLimit`/`http.lowSpeedTime`(git 自己的传输层超时,判定
"传输速度连续 N 秒低于某个字节数就算卡死",比外面简单包一层
`timeout N` 更贴切,能容忍真实的慢速网络、只掐真正卡死的连接)。

**这次教训要更狠地记一遍**:这个脚本里几乎每一行都可能碰网络
(apt-get、curl、pip、git),修了一个又冒出下一个是因为一开始就没有
"这个脚本里所有网络操作都要有超时"这个系统性意识,是发现一个补一个,
不是一次性设计对的。以后写任何会跑网络操作的脚本,第一遍写的时候就要
过一遍"这一行如果连接卡住会怎样",不要等它在生产 CronJob 里真的卡半小时
才发现。
