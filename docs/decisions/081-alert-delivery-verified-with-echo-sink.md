# ADR-081:用一个回显接收端,让"告警送得出去"变成一直被验证的事

日期:2026-08-28
状态:**已实机验证**(2026-08-28):告警从 Alertmanager 出去、POST 到外部终点、payload 完整可见

## 问题:一条从来没被跑过的路径

`platform/alertmanager-notification/` 那套外部通知配置很早就写好了,结构完整,
注释里连"怎么换成企业微信原生 receiver"都写了。但 cloud-full 一直是
`alert_notification_mode: none`,理由是没有真实的 webhook 地址(zhenghe
2026-08-22:"等后面上生产来测试,暂时不测试。留好配置项,能够生效就好")。

这个理由本身没问题。**但它的后果被低估了**:到 2026-08-28 为止,这个平台有

- 8 条告警规则(ADR-071),其中几条实机触发过;
- 质量断言推 Alertmanager 的 CronJob(ADR-073),实机验证过两条路径;
- 六条黄金链路探针(ADR-079);
- Alertmanager 本身、路由、receiver 配置。

**而"告警从 Alertmanager 出去、到达一个外部终点"这一段,一次都没有跑过。**

真到上生产那天配上企业微信 webhook,那是这条路径第一次被执行——而那时候
恰恰是最不该出问题的时候。这个仓库对这类"看着齐全、从没跑过"的东西吃过太多
亏了(`is_platform_admin` 从没触发过、OPA 策略从没热加载过、KServe 推理链路
断了三周没人发现)。

## 决策:给它一个真实的终点,只不过终点在集群里

`apps/alert-echo-sink/` —— 一个 55 行的 HTTP 接收端(标准库,不建镜像,和
黄金链路探针同一个模式):

- `POST /` 收 Alertmanager 的 webhook,存进内存(最近 50 条);
- `GET /alerts` 把收到的原样取回来;
- `GET /healthz` 探活。

cloud-full 的 `alert_notification_mode` 改成 `webhook`,
`monitoring/alertmanager-webhook` 这个 Secret 默认指向它(由 `scripts/00` 建)。

**换成真实渠道就是改这一个 Secret 的 url。** 机制那一半——规则触发 →
Alertmanager 分组 → 路由匹配 → receiver POST 出去——一直有东西在验证它。
上生产那天不是第一次跑这条路,而是换个地址。

### 顺带一个实际用处

`GET /alerts` 能直接看到**企业微信那边会收到什么样的 payload**,不用先接上
真实渠道再猜。想调告警文案(`annotations.summary` / `description`)时,这是
最直接的反馈回路。

### 这算不算"为了测试而引入组件"

算,而且我认为值得,理由是它改变的不是"能不能测",是**这条路径处于什么状态**:
- 之前:配置就位,状态未知,要等上生产才知道;
- 之后:配置就位,**一直在被真实告警流量走**,状态每天都在被确认。

代价是 monitoring 命名空间多一个 10m CPU / 48Mi 的 Pod。如果哪天觉得不值,
把 `alert_notification_mode` 改回 `none`、从 `enabled_components` 里去掉它即可
——但那样就回到"未知"状态,这个取舍要明确知道自己在换什么。

## 实机验证(cloud-full,2026-08-28)

往 Alertmanager 推一条真实告警(`POST /api/v2/alerts`),然后:

```
sink 日志:  收到 1 条告警: ['EchoSinkEndToEndTest'] status=firing
GET /alerts:receiver = monitoring/external-notification/external-webhook
            summary  = 验证告警能不能送到外部终点
            externalURL = http://kube-prometheus-stack-alertmanager.monitoring:9093
```

**这条路径以前从来没有被走通过一次。** 现在走通了,而且 receiver 名字确认了
它走的正是 `AlertmanagerConfig` 里那份配置(不是 chart 的默认 null receiver)
——也就是说 `alertmanagerConfigSelector` / `MatcherStrategy` 那两处配置是对的,
而它们恰恰是最容易静默失效的地方。

`GET /alerts` 拿到的就是**外部渠道会收到的完整 payload**。要调告警文案,
照着这个改就行。

### 中途犯的一个错,值得记

第一次提交时写了"cloud-full 启用它",实际**没生效**:我用
`t.replace("  - prometheus-rules.yaml\n", ...)` 往 `enabled_components` 里加,
而 `prometheus-rules` 是 **platform 层的 app**、根本不在那个列表里。
replace 不匹配就是静默无操作,而我**没有加断言**——于是提交、推送、同步全都
"成功",直到查 ArgoCD 才发现应用不存在。

**只验证了"我做了一个操作",没验证"操作真的改到了东西"。** 同一个模式今天
已经出现过三次(`re.sub` 的 `\1` 被当成八进制、YAML 重复键静默覆盖、
jobTemplate 标签没传给 Job)。

## 还没做的

1. **没部署验证。** 要验的是:AlertmanagerConfig 真的被 Alertmanager 收编、
   一条真实告警真的 POST 到了 sink、`GET /alerts` 能看到它。
2. **local-lite / prod 没改。** local-lite 仍是 `none`(那一档不跑告警);
   prod 是 `webhook`,但它的 Secret 该指向真实渠道,不是这个 sink——
   `scripts/00` 只在 Secret 不存在时创建,所以 prod 上先建好真实的那份即可。
3. sink 是内存存储,重启就空。**这是有意的**:它是观察窗口不是存储,真要留存
   告警历史,那是 Alertmanager 自己或者下游系统的事。
