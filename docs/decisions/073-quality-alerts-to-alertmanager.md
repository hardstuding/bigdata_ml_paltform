# ADR-073:数据质量/新鲜度断言的结果送进 Alertmanager

日期:2026-08-26
状态:已实现,**未部署验证**(逻辑用假数据在本地跑过)

## 背景:告警出口缺的最后一块

[ADR-071](071-platform-alert-rules.md) 补上了平台自己的告警规则,但当时就
写明了最大的缺口:

> 数据质量 / 新鲜度断言失败没有进 Alertmanager。它们的结果在 OpenMetadata
> 里,不是 Prometheus 指标。**三个来源(质量、新鲜度、审计断流)必须接同
> 一个出口**,各接各的最后一定有一套没人维护。

审计断流那条已经是 Prometheus 指标(`flink_jobmanager_numRunningJobs`),
规则也写了。剩下质量和新鲜度这两个——它们的结果只存在 OpenMetadata 的
数据库里,**红了只有人主动打开页面才看得到**。而"没人会天天打开它"恰恰是
这个平台反复出现的失败前提。

## 决策:一个 CronJob 把结果推给 Alertmanager

### 为什么不引入新组件

第一反应是写一个"OpenMetadata → Alertmanager"的转换服务(OpenMetadata 自带
Alerts & Notifications 能发 webhook,但它的 payload 和 Alertmanager 的
`/api/v2/alerts` 格式不兼容,中间需要转换)。**否掉了**:这个仓库已经有
三个自建 Flask 工具,再加一个常驻服务是实打实的维护成本
([ADR-055](055-external-review-response-2026-08-15.md) 里外部 review 点过
这条)。

用的是仓库里已经反复用过的那个模式:**一个 CronJob + `python:3.12-slim`
+ 只用标准库的内联脚本**(先例:`apps/opa/manifests/grants-sync-cronjob.yaml`
和 `departments-sync-cronjob.yaml`)。没有新镜像、没有新服务、没有新端口。

### 为什么是"推"而不是"暴露指标让 Prometheus 拉"

拉模型要一个常驻的 exporter(又是一个服务),或者 Pushgateway(又是一个
组件)。而 Alertmanager 本来就有 `/api/v2/alerts` 这个推送接口,这批告警
又天然是低频的(断言 6 小时才跑一轮),推送完全够用。

### 告警靠"续期"而不是显式发 resolved

每条告警带 `endsAt = now + 40 分钟`,CronJob 每 15 分钟跑一次:断言还红着
就续一次,恢复了就不再发,Alertmanager 到点自动 resolve。

**这比显式发 resolved 事件可靠**:如果这个 CronJob 自己挂了,告警只会自然
消失,而不会卡在 firing 上永远不走——后者会制造一个"永远红着但其实早就
好了"的假告警,而**被习惯性忽略的告警比没有告警更糟**,这条判断在
ADR-071 里已经用过一次。

## 除了"断言失败",还告警"断言不跑了"

这是这个设计里我认为最值钱的一条。

断言失败(`Failed`)是显性的。但还有一种更隐蔽的情况:**断言很久没有跑过
了**——TestSuite 的 CronJob 挂了、pipeline 被误删、OpenMetadata 迁移之后
调度没恢复。这时候页面上那条断言**还是绿的**,因为它显示的是上一次的成功
结果。人看一眼觉得没问题,实际上已经几天没有检查过任何数据了。

所以超过 24 小时没有新结果就单独发一条 `DataQualityCheckStale`(info 级)。
24 小时这个阈值是按"断言 6 小时一轮、连着漏三轮才算数"定的,不是拍的。

## 本地验证

内联脚本抽出来用假数据跑过四种输入:成功的、失败的、结果过期的、从来没跑
过的。**测试当场抓到一个真 bug**:`entityLink` 有两种形态,表级断言那种
结尾没有 `::columns::`,直接 `split("::")[2]` 会带上结尾的 `>`,标签变成
`trino.iceberg.demo.orders>`——按表名过滤或聚合就全对不上了,而且这种错
在页面上看不出来。

## 还没做的

1. **没部署验证。** 要在真集群上确认:token 能读到断言、推送真的到达
   Alertmanager、告警在 40 分钟后确实自己消失。
2. **仍然没有外部通知渠道。** 告警会停在 Alertmanager 界面里,不会推到人。
   这一步卡在真实凭据(zhenghe:"等后面上生产来测试"),配置模板在
   `platform/alertmanager-notification/`。
3. 没有按告警来源做路由/抑制。现在质量告警和平台告警混在同一个默认路由
   里,等真接了通知渠道再按需要分。
