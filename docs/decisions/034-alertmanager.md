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

## 2026-08-13 补充:预留多渠道通知配置(还没激活)

用户要求:邮箱、企业微信必须最终接上;飞书、Slack、其他主流办公/协作
软件(钉钉、Teams、Telegram 等)尽量都覆盖,"像很多开源工具一样"。同时
明确这几个渠道现在都**不需要真的配到能收到通知**,不用测试,先把结构
预留好、以后需要哪个填真实凭据就能生效即可。

按这个要求,在 `platform/apps/kube-prometheus-stack.yaml` 的
`alertmanager.config.receivers` 里加了一大段**全部注释掉**的 receiver
模板(邮箱/企业微信/Slack 各给了一份可直接抄的配置,`route` 没有指向
任何一个),对现在"能查不推送"的现状零影响——ArgoCD 同步这个文件不会
有任何实际行为变化,纯文档性质的改动。

- 邮箱(`email_configs`)、企业微信(`wechat_configs`)、Slack
  (`slack_configs`)都是 Alertmanager 原生支持的渠道,不需要额外组件,
  模板已经给好,激活只需要填真实凭据(敏感值走 Secret +
  `alertmanagerSpec.secrets` 挂载 + `_file` 字段引用,不直接写进 Git)、
  再给 `route.routes` 加一条匹配规则。
- 飞书(Feishu/Lark)**没有** Alertmanager 原生 receiver——它的"自定义
  机器人"webhook 要求的 JSON 格式和 Alertmanager 发出的 webhook
  payload 不兼容,需要一个小的转换服务做格式转换才能真正用起来,不是
  填几个字段的事,这次只记录了这个结论,没有实现这个转换服务。
- 钉钉的自定义机器人和飞书是同一类情况(格式不兼容、需要转换服务);
  Teams/Telegram/Discord 有 Alertmanager 原生 receiver,和邮箱/企业
  微信/Slack 是同一个模式,真要接入时照抄模板改字段即可。
- 电话/语音告警评估过(见更早的讨论):没有现成的免费自建方案,通常要接
  商用按次计费的语音 API(阿里云语音服务/Twilio 一类),这次用户没有
  这类账号,不在预留范围内,如果以后有对应账号可以再补。
- 之所以不逐个渠道都预写模板(比如钉钉/Teams/Telegram 都还没写):这类
  "没人验证过是否正确"的死配置攒多了本身是一种负债,等真的要用某个
  渠道时再照着已有模板抄一份、顺手验证一次,比现在批量预写但从来没跑
  通过更可靠。
