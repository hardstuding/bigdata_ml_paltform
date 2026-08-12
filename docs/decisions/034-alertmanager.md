# 034. 打开 Alertmanager

- 状态: 已采纳,验证中(2026-08-12)

## 背景

kube-prometheus-stack 从 Phase 0 起就是 `alertmanager.enabled: false`(图
省资源,local-lite 阶段只求指标链路能跑通)。复盘架构现状时发现:
cert-manager 因为内存压力反复触发 livenessProbe 重启,连续 8 小时没人
发现,是靠人肉巡检才注意到——这个模式撑不住无人值守,alerting 是当前
架构里优先级最高的一个空白。

## 决策

打开 `alertmanager.enabled: true`,轻量资源配置(64Mi 请求),不开
persistence(和这个项目 local-lite 阶段其他非核心数据一致的取舍——告警
状态丢了不是灾难性的,不像 Postgres 那种真数据)。

**chart 自带的 `defaultRules`(`defaultRules.create: true` 是 chart 默认值,
之前就一直在生效,只是没地方路由)已经覆盖了 Pod 崩溃重启、Job 失败、
资源紧张等常见场景,不需要自己从零写告警规则**——这是这次决策里最重要的
一点:打开 Alertmanager 本身比想象的成本低,因为规则早就在评估了,只是
之前"评估了但没人看得到"。

### 还没配外部通知渠道

现在只做到"告警可查"(Alertmanager 自己的 UI、Prometheus `/alerts`
页面),**不是"会推送"**。Slack/邮件/PagerDuty 这类真正能把告警推给人的
渠道需要对应的 webhook/凭据,这次没有,也不应该由自动化流程自己造一个
——这是留给人配置的部分,和 `permission-request-app` 的 `GIT_TOKEN`
是同一个"能力已经搭好,凭据要人给"的模式。

### 关掉三个 k3s 上必然假阳性的告警组

`kubeControllerManager`/`kubeScheduler`/`kubeProxy` 这三个默认告警组
在 k3s 上打开就会一直 firing——k3s 把这些组件内置在同一个二进制里,不像
标准 kubeadm 集群那样有独立的、chart 期望格式的 metrics 端口能抓,不是
真的挂了,是根本没有对应的抓取目标。实测确认过(打开 Alertmanager 前,
直接查 Prometheus `/api/v1/alerts` 已经能看到这三个一直在 firing)。
关掉对应的 ServiceMonitor 抓取(`kubeControllerManager.enabled: false`
等)和默认规则组,不是关掉告警能力本身,是去掉一类结构性假阳性。

## 顺带抓到的真实问题

打开 Alertmanager 当天,`KubeJobFailed` 告警就抓到 `iam-sync` CronJob 有
3 次运行失败(`DeadlineExceeded`,超时设置和调度间隔一样紧,内存紧张时
不够用)——这是靠告警发现的,不是靠人继续手动巡检发现的,验证了这件事
本身的价值。已经在 ADR-031 里补上这个坑和修复记录(`activeDeadlineSeconds`
放宽到 600 秒)。

## 后果

- 没有持久化,Alertmanager Pod 重建后历史告警状态会丢——local-lite 阶段
  可以接受,cloud-full/prod 起应该重新评估要不要加持久化(取决于是否需要
  长期保留告警历史用于事后分析)。
- 没有配置任何实际的告警接收渠道,现状是"能查但不会主动推送",这个仓库
  能做到的部分已经做完,下一步需要人提供一个真实的通知渠道。
- `defaultRules` 覆盖的是通用 K8s 层面的问题(Pod 崩溃、Job 失败、资源
  紧张),不覆盖这个项目自己的业务语义(比如"Trino 查询失败率异常"这类),
  这类自定义告警规则还没有,等真的需要的时候再加。
