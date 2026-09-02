# ADR-090:组件二开分三层,优先用上游留的扩展点

日期:2026-09-02
状态:**已实现(企业微信告警作为第一个样例)**

## 问题

使用方问:"部分项目可能需要二开,现在的部署方式支持吗?比如 Superset
我之前是有用源码部署的,新增了企业微信告警的功能。"

支持,但**怎么做**决定了以后每次升级的代价。

## 决定:分三层,能用上层就不用下层

| 层 | 做法 | 升级成本 | 现在谁在用 |
|---|---|---|---|
| **① 配置注入** | 组件官方留的扩展点(Superset 的 `configOverrides`、`CUSTOM_SECURITY_MANAGER`、OpenMetadata 的自定义属性、KServe 的 logger) | **零** | 汉化、OIDC、SecurityManager、**企微告警** |
| **② 自建镜像** | `FROM 官方镜像` + 加依赖/文件,CI 构建推 ACR | 低——换基础镜像 tag 即可 | 7 个组件 |
| **③ 改上游源码** | fork 打补丁 | **高**,每次升级都要重新合 | **没有,刻意避免** |

### 为什么这么排

使用方自己定过一条判据:

> **有些非必须的功能和版本升级的方便性,还是要做抉择。**

参照物是真实发生过的:2026-08-26 把 OpenMetadata 从 1.13.3 升到 2.0.0、
09-02 升到 2.0.1,都是逐条核对 breaking changes 做的。**如果那是个分叉
版本,这两次升级的代价会高一个量级 —— 而且很可能就此不升,然后一直停在
旧版本上。**

同一条判据此前已经用过两次:[ADR-086](086-approval-belongs-to-oa.md) 的
"申请访问"链接(用 OpenMetadata 的自定义属性,不改它的前端)、
[ADR-085](085-inference-payload-logging.md) 的推理留痕(用 KServe 自带的
logger,不改推理服务)。

## 样例:企业微信告警走第 ①

**Superset 的通知本身就是一个插件系统** —— `BaseNotification` 的
`__init_subclass__` 把子类自动注册进 `plugins`,后定义的同 `type` 子类
覆盖先定义的。所以在 `configOverrides` 里定义一个子类就生效,**不碰镜像
里的任何文件**。

三个实现上的取舍:

1. **继承 `WebhookNotification`,不新增通知类型。**
   `ReportRecipientType` 是固定的 StrEnum(Email/Slack/SlackV2/Webhook),
   **没法在配置里加新成员**。复用 Webhook 类型、只覆盖 `_get_req_payload`,
   二开面缩到一个方法。

2. **必须放进 `FLASK_APP_MUTATOR`,不能在配置顶层 import。**
   `superset_config.py` 是在 Flask app 初始化**之前**导入的,而
   `superset.reports.notifications.webhook` 会连锁加载
   `superset.models.core`,直接抛
   `Exception: App not initialized yet`。第一版就是这么写的,整个 Superset
   起不来(实测 CrashLoopBackOff)。

3. **`_get_files()` 返回 None。** 企微机器人不接受 multipart,而父类在有
   附件时会改用 multipart 发送 —— 那样企微直接拒收。代价是截图/CSV 发不
   出去,只能靠消息里的链接跳回 Superset。**这是企微机器人本身的限制。**

### 验证

- `WeComWebhookNotification` 在真集群上**确认注册成功**,排在
  `WebhookNotification` 之后(插件机制按定义顺序覆盖)。
- payload 形状有 7 条单元测试(`tests/test_wecom_notification.py`),进 CI。
  **反证跑过**:把 `msgtype` 改成 `text`、把截断去掉,对应的测试立刻红。
- **没有接真实企微地址** —— 按既定安排,真实告警渠道等上生产再接。

**为什么 payload 值得单测**:这段代码住在 YAML 里的一个字符串中,不在任何
import 路径上,静态检查扫不到;而且它的失败是**远端的** —— 形状错了企微
返回 `errcode != 0`,而告警"发出去了",从 Superset 这边看不出问题。

## 什么时候可以下沉到第 ②③ 层

- **②**:需要装额外的 Python 包、系统依赖、字体、驱动 —— 那些没法靠配置
  注入。Superset 镜像本身就是这么加的 `trino`/`authlib`/汉化语言包。
- **③**:只有当上游**确实没有**扩展点,而这个功能又是必须的。到那一步要
  先写 ADR 说明:为什么必须、升级时怎么重新合、谁负责跟上游版本。
