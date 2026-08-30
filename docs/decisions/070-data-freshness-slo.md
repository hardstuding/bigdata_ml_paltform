# ADR-070:数据新鲜度当成一条数据质量断言,不另建监控子系统

日期:2026-08-23
状态:**检测已实机验证**(2026-08-23);**告警出口 2026-08-28 打通**(ADR-081)

## 背景:监控的对象错了

[`docs/project/production-readiness-gaps.md`](../project/production-readiness-gaps.md) 第 2 条。

平台有 kube-prometheus-stack、Grafana、Alertmanager,但监控的全部是**组件**
——Pod 活着吗、CPU 多少、JVM 堆多大。而数据平台对业务的承诺从来不是
"Pod 活着",是"**这张表每小时更新一次**"。

这两件事可以完全脱节:所有 Pod 绿着、所有 Job 显示 Success,而某张表因为
上游断供已经三天没更新了。这个项目对这种脱节并不陌生——
`docs/operations/troubleshooting.md` 开篇就是同一个道理:ArgoCD Synced 不
等于生效、Pod Running 不等于健康、Job Complete 不等于业务逻辑跑对。
**"组件健康"和"结果正确"之间隔着一层,而告警只装在前面那层。**

## 决策:它是一条断言,不是一个新系统

第一反应会是"写一个 exporter,定时查 Iceberg 表的最新提交时间,吐
Prometheus 指标,配 Alertmanager 规则"。这条路能走通,但它**新增一个自建
组件**,而这个仓库已经有三个自建 Flask 工具、并且把"它们没有测试、源码和
ConfigMap 靠人工同步"列为已知债务([ADR-055](055-external-review-response-2026-08-15.md))。

再想一层:**"这张表最近 N 天有没有新数据进来"和"这张表行数是不是为零",
是同一类问题。** 后者上午刚用 OpenMetadata 的 Data Quality 做完
([ADR-065](065-data-quality-on-openmetadata.md)),而 OpenMetadata 内置的
`tableRowInsertedCountToBeBetween` 恰好就是"某个时间列在最近 N 个
DAY/HOUR 内新增了多少行"。

所以新鲜度就是**再加一条断言**,复用同一套执行编排、同一份连接配置,并且
——这是最重要的——**结果落在同一个地方**:分析师查表之前看到的是一份
完整的"这张表健康吗",而不是"质量在 A 系统看、新鲜度在 B 系统看"。

## 参数名没有硬编码进去,脚本自己会核对

`tableRowInsertedCountToBeBetween` 的参数名无法离线确认。这个项目**已经
因为"猜 API 形状"栽过三次**(ADR-065 记了两次,scripts/30 一次),所以
`scripts/34` 的做法是:

1. 先 `GET /api/v1/dataQuality/testDefinitions/name/<定义名>`,把它真实声明
   的参数名拉下来
2. 和脚本里写的对一遍
3. **对不上就跳过这一条并打印真实的参数名**,不硬着头皮建一个必然失败的
   断言,也不让整个脚本失败

`scripts/35` 相应地把新鲜度列为**可选项**而不是必须项——否则一次正常的
跳过会被报成验证失败,又是一次"验证脚本自己把成功报成失败"。

## 还没做的:告警出口(这条比检测本身更重要)

**检测到了没人知道,等于没检测。** 现在三条质量断言 + 一条新鲜度断言的
结果都只在 OpenMetadata 界面里,要人主动去看。

出口有两条路,都卡在同一件事上:

- OpenMetadata 自带 Alerts & Notifications(支持 webhook / 邮件 / Slack)
- Alertmanager 已经部署,但**没有配置任何外部通知渠道**——zhenghe 明确说过
  "等后面上生产来测试,暂时不测试,留好配置项,能够生效就好"

所以这一条的真实阻塞不是技术,是没有真实的通知凭据。等有渠道时,
**质量、新鲜度、以及 [ADR-066](066-trino-query-audit.md) 那条"审计流断了"
的告警应该一起接同一个出口**,不要各接各的——三个来源三套通知配置,
最后一定有一套没人维护。
