# 045. 权限 OA 审批工作流 Phase 3:可插拔审批后端 + 通知/升级/交接/审计

- 状态: 已采纳,已实现(2026-08-14)

## 背景

ADR-044 做完了分级审批的核心机制,用户反馈"太玩具化"(测试环境只有
`zhenghe`/`admin` 两个真实账号),要求补齐大厂常见的配套能力。展开后
确认要:①能对接公司已有的 OA 系统(可插拔,不强制自建到底);②企业微信
通知;③审批人超时不处理要能提醒/升级;④权限交接(参考用户公司真实项目
`ysb/algo/big_data/common_tool/authorize.ipynb` 的 `transfer_*` 设计);
⑤请求审计数据完整可查;⑥界面更完整。

用户同时提醒:如果这些功能设计不好会直接变成"臃肿",宁可不做;通知/
升级这两条如果实现复杂就可以跳过(他们公司 OA 已经有这个能力了)。这次
落地时按这个精神做了取舍:核心是**可插拔**,通知/升级是**轻量、可选、
不影响主流程**的附加能力,不是不计成本地堆功能。

## 决策

### 可插拔审批后端:一个 webhook 出站 + 一个回调入站,不做通用适配框架

延续 ADR-030"文档化覆盖点,不引入新抽象层"的哲学。`APPROVAL_BACKEND`
环境变量:`local`(默认,ADR-044 那套不变)或 `webhook`。webhook 模式下,
一个 step 轮到时不再等人在这个页面点,而是 `POST
EXTERNAL_OA_WEBHOOK_URL` 把这一步的信息发出去,状态改成
`pending_external`(不再出现在"待我审批"列表,责任已转移);外部系统
处理完调 `POST /table-access/step/<id>/external-callback` 回报结果,
`token` 要匹配 `EXTERNAL_OA_CALLBACK_TOKEN` 才接受。

**没有接入任何具体厂商的真实 API**——没有真实对接目标,也没有权限去连
公司内网 OA。这次交付的是协议本身(请求体格式、回调格式、鉴权方式),
不是一个已经跑通的对接。以后要接公司真实 OA,照着这个协议改一下发送/
接收的字段名就行,不用碰核心状态机。

### 状态机:新增 `pending_external`/`escalated`/`skipped`,核心判断逻辑不变

`approval_steps.status` 从 ADR-044 的 `pending/approved/rejected` 扩到
六种。`current_step_order()`(卡在第几级)现在把 `pending`/
`pending_external` 都算"没解决";`finalize_table_request_if_done()`
判断"是否全部批准"时,把 `escalated`/`skipped` 从计算集合里剔除——
`escalated` 代表这一行已经被新插入的行取代,`skipped` 代表申请已经因为
别的原因终止。核心的"审批链按级推进"这套逻辑(ADR-044 已经验证过)完全
没动,新状态只是在外面加了几种"这一行不再计数"的标记,不是重新设计
状态机。

### 超时提醒 + 升级:换人审,不是自动通过

新增 `approval_steps.activated_at`(这一步真正变成"轮到它"的时间,和
申请提交时间不是一回事——多级审批时,后面几级要等前面批完才会被激活)。
一个内部端点 `/internal/escalation-check`,配合新增的
`escalation-cronjob.yaml`(用 `curlimages/curl`,每小时触发一次,不直接
碰 SQLite——PVC 是 ReadWriteOnce,让唯一在跑的 app 进程自己处理数据库
操作,不是两个进程抢着写)。等了超过 `ESCALATION_HOURS`(默认48)先发
提醒,等到 2 倍时长真正升级:原 approver 那行标 `escalated`(退出阻塞
集合),同一级插入一行新的 `pending`,审批人是原 approver 的上级
(`get_manager_chain` 查一级)。**明确不做"超时自动批准"**——升级的
含义是换人审,不是当作已经审过,绕过审批本身是安全问题,不是效率问题。

### 权限交接:参考用户公司真实实现的模式,不是抄代码

`ysb/algo/big_data/common_tool/authorize.ipynb` 用 DolphinScheduler API
做 `transfer_user_basic_objects`/`transfer_user_alerts`,交接完发企微
通知。这边技术栈完全不同(SQLite + git,不是 DolphinScheduler),但"一次
操作转移全部未决事项 + 批量转移组成员关系 + 通知接手人具体转移了什么"
这个模式值得照搬。新路由 `/admin/transfer`(`is_approver` 守卫):把
`from_user` 名下所有 `pending`/`pending_external` 的
`approval_steps.approver_username` 改成 `to_user`;把
`memberships.csv` 里 `from_user` 有、`to_user` 没有的组成员关系批量
补上(复用 ADR-032 `apply_to_git()` 的 clone/改文件/commit/push 模式)。

### 审计看板:暴露已经存在的数据,不是新增留痕机制

新路由 `/audit`(`is_approver` 守卫),列出全部组权限申请 + 表访问申请 +
关联的审批链历史。这套 app 从 ADR-032/044 起一直是"只 UPDATE status,
不 DELETE 行"的模式,历史数据本来就完整保留,这次只是把它暴露成一个
可读页面,不是额外加了什么记录机制。

### 通知:企业微信群机器人标准 webhook,和 ADR-034 预留的模板同一类处理

`notify_wecom(text)`,`WECOM_WEBHOOK_URL` 是标准企业微信群机器人 webhook
地址(公开、稳定的官方格式,不是猜的),未配置时静默跳过,失败也不影响
主流程(通知是锦上添花,不能让通知失败把审批操作本身搞挂)。触发点:
一个 step 被激活时通知审批人、申请终态时通知申请人、升级/交接时各发一条。

### 界面:延续单文件模板路线,只加导航和状态徽章

没有引入前端框架(ADR-032 已经评估过 Backstage 类工具,这次没有新证据
推翻那个判断)。加了顶部导航(申请/审计/交接三个入口互相可达)和彩色
状态徽章(替代纯文字),这是"界面更完整"这个诉求在这个量级下该有的
投入,不是更多。

## 涉及的文件

- `apps/permission-request-app/src/app.py` + `manifests/app-configmap.yaml`
  (同步)、`manifests/deployment.yaml`(新增环境变量)
- 新增 `apps/permission-request-app/manifests/escalation-cronjob.yaml`
- `scripts/00-generate-secrets.sh` 新增 `permission-request-app-internal`
  这个纯内部凭据的自动生成(不需要人工判断,不是 GIT_TOKEN 那一类)

## 后果 / 明确不做的

- 不接入任何具体外部 OA 厂商的真实 API——留给以后真的要对接时,照着
  这次交付的协议改字段,不是从零设计。
- 不做超时自动批准。
- 不引入前端框架/构建链路。
- 不做企微以外的通知渠道——ADR-034 已经给邮箱/Slack 留了模板,这次
  没有新的用户诉求要求马上接,不在这轮范围里现做。
- **这次也是给"这套治理机制不要过度设计"这条提醒留一笔记录**——用户
  明确说过"两个人用的系统堆这么多企业级能力,投入产出比是要打问号的",
  这次落地时优先做了有真实参照(审计、交接——公司自己的 authorize.ipynb
  证明是真需求)的部分,通知/升级/可插拔后端做的是"轻量、可关闭、不
  影响主路径"的版本,不是照着假想的"大厂标准"不计成本地堆。以后再有
  类似"要不要加XX企业能力"的请求,先问一句"这个项目现在的真实用户规模
  是否撑得起这个投入",不要默认照单全收。

## 验证(2026-08-14 深夜,真实端到端跑通,过程中发现并修复了 3 个真实 bug)

真实注册了 L1/L2/L3 三张测试表(临时 un-park Trino,验证完重新
park 回去),通过真实路由跑出申请→分级审批→写回 grants.csv 的完整链路,
用直接查 SQLite + `git fetch` 核对远端仓库(不是只信页面显示)确认:

- **L1(2 人 1 级)**:manager+table_owner 都批准后,`table_access_requests`
  正确变成 `approved`,`table-access-grants.csv` 真的被 push 到了远端
  仓库(`git fetch` 拉到了 app 自己 push 的那个 commit,内容字段核对正确)。
- **L2(3 人 2 级)**:只批 L1 的一个人时,第 2 级的行确认还没有
  `activated_at`,审批人在自己的"待我审批"列表里也确实看不到——两个 L1
  的人都批完之后,第 2 级才真正激活(`activated_at` 被设置),对应审批人
  这时候才能看到。全部批完后同样正确写回 grants.csv。
- **拒绝路径**:L3 场景下拒绝 L1 的一个人,`table_access_requests` 立刻
  变成 `rejected`,其余还没处理的行(包括还没激活的第 2 级)全部正确标成
  `skipped`,没有遗留任何"看起来还在等谁审批"的僵尸状态。
- **权限交接**:一次操作把某人名下待处理的 `approval_steps` 全部转移给
  接手人,数据库层面核对过确实改了 `approver_username`。
- **审计看板**:用构造的测试 JWT(带 `groups: [platform-team]`,这个 app
  本来就不校验签名,和它自己的信任边界一致)访问 `/audit`,确认能看到
  全部测试数据,状态徽章渲染正确。

**过程中真实发现并修复的 3 个 bug(不是设计阶段就想到的,是跑出来才发现
的)**:

1. **`build_approval_steps` 会让 L1 的人在 L2/L3 重复批一次**——第一版
   `l2 = list(l1)` 把 L1 的人复制进了 L2 的行,状态机要求他们对同一张表
   点两次批准。改成用一个贯穿全程的 `already_required` 集合去重,每一级
   只放新增的人。
2. **escalation CronJob 被 NetworkPolicy 挡住**——`permission-request-app`
   命名空间的 `default-deny-ingress` 只放行 oauth2-proxy 标签的流量,
   CronJob 的 Job pod 没有这个标签,直接被拒绝。给 Job pod 加专属标签
   + 补一条对应的 NetworkPolicy ingress 规则解决。
3. **NetworkPolicy 规则本身生效有延迟**——修完第 2 条后手动触发 Job 还是
   偶发失败,发现是全新 pod 的 NetworkPolicy 规则在 CNI 上生效需要几秒,
   Job 容器命令起来立刻执行,精确踩中这个窗口期。给 `curl` 加
   `--retry-connrefused`(普通 `--retry` 不重试"连接被拒")解决。

**没有验证到的部分,如实记录**:OpenMetadata 回写(建表工具那条路径)
这次卡在 `admin1` 这个 bot 的角色是空的(`roles: []`),只能读不能写,
403 Forbidden——这不是这次改动引入的问题,是这个 bot 本来就没有被正确
授权,需要人在 OpenMetadata UI 里给它配角色。为了不让这一个外部依赖
卡住整轮验证,后续测试改成直接操作数据库 + 调用真实的
`build_approval_steps`/`activate_next_step` 等函数,绕开 OpenMetadata
查询这一步,单独验证审批链路本身的正确性——这意味着"提交申请时自动查
OpenMetadata 拿到安全等级"这条路径,这次没有被真实走通过,只验证了
"查不到时优雅拒绝"这一半。企微通知/webhook 可插拔后端同理,没有真实
群/外部系统可测,只验证了"未配置时静默降级"这一半。
