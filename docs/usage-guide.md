# 开发使用指南

这份文档给**用这个平台干活的人**看(数据分析师、算法工程师),不是给
平台运维看的——运维相关的文档在 `docs/operations/`。这里只讲"我要查
数据/搭看板/跑 notebook,应该怎么用",不讲这些组件是怎么部署起来的。

看这份文档之前先确认:你要用的组件现在是不是"常驻"状态。这台机器按需
park/unpark 组件是常态(见 `README.md`),真实状态以
`http://portal.local-lite.test`(平台门户,登录后能看到每个工具现场探测
的在线状态)为准,不要相信这份文档里任何"现在是不是在跑"的静态描述——
这条本身也是这个项目吃过亏才写下的规矩(见
`docs/operations/troubleshooting.md`)。

## 你的账号

所有工具共用同一个 Keycloak 账号(realm: `platform`),登录一次,后面
打开别的工具不用重新输密码。你的账号所在的组(`platform/iam/
groups.yaml`/`memberships.csv`)决定你能用哪些工具、审批链路怎么走——
不知道自己在哪个组,找 `platform-team` 组的人确认。

## 查数据:Trino

湖仓用 Iceberg + Trino,这是这个平台目前**唯一**的交互式 SQL 入口。

- Web UI:`http://trino.local-lite.test`,浏览器打开会走 Keycloak 单点
  登录,登录身份就是你的 Keycloak 账号。
- CLI / JDBC:Trino 原生支持 OIDC 交互式登录(命令行工具会弹出浏览器
  完成一次授权),也支持用户名+密码的 Basic Auth(给脚本/BI 工具这类
  没法弹浏览器的场景用,账号是单独发的服务账号,不是你自己的 Keycloak
  账号)。JDBC URL 形如 `jdbc:trino://trino.local-lite.test:443`,证书是
  自签的,客户端要么信任这张自签证书,要么按各工具自己的"跳过证书校验"
  选项配(生产环境上正式证书后这条不再需要,现在是 local-lite 阶段的
  临时处理)。
- 权限:能查哪些表,由**表访问分级审批**控制(见下一节)。没申请过的表
  查不到,不是 bug。
- 常见报错排查:先看 `docs/operations/troubleshooting.md`,这台机器的
  Trino 有已知的资源争抢问题(colima 13GB/6vCPU 的限制下,JVM 启动期
  偶尔会 CrashLoopBackOff,等它自己退避重启几次通常能自愈,不是配置
  错误)。

## 查表权限:权限申请门户

`http://permission-request.local-lite.test`——想查一张之前没权限的表,
在这里发起申请,不要去找人手动改 `platform/iam/table-access-grants.csv`
(那份文件现在是这个门户自动写的,手动改容易和门户的记录对不上)。

- 按表的"安全等级"(OpenMetadata 里打的 tag)走不同的审批链:等级越高,
  审批链越长(直属上级 → 上级的上级 → 表负责人 → 指定管理员,按等级
  逐级叠加,不是每次都要走全部四层)。
- 审批通过后的授权记录**默认 180 天后自动过期**,到期会被自动回收
  (ADR-050),届时需要重新申请,不是一次批准永久有效——这条是最近才
  补上的行为,如果你发现权限"突然没了",先看是不是过期,不要当成 bug
  报。
- 这份门户目前只做"决策与留痕",**不做真正的 Trino 查询拦截**——批准
  记录写进 grants.csv,但现在没有任何东西读这份数据去真的拦住你的 SQL
  查询(Trino 层面的细粒度强制执行还没做,是明确的后续工作,ADR-028)。
  换句话说:现在"申请-审批"这条流程本身是真实、被使用的,但"不批准就
  真的查不到"这个技术保障还没有——目前的访问边界靠 Trino 自己的角色/
  catalog 权限,不是这套 OA 流程本身在拦。

## 建表:建表注册工具

`http://table-registration.local-lite.test`——需要新建 Iceberg 表时用
这个,不要直接手写 DDL 连 Trino 建表。它会同时把表的负责人、安全等级回写
进 OpenMetadata,保证目录信息和实际的表同步创建,不会出现"表建了但目录
里没有、没人知道该找谁"的情况。

## 看板 / BI:Superset

`http://superset.local-lite.test`,数据源接的就是 Trino,建看板前先确认
自己对要用的表有权限(上面"查表权限"那节)。

## 交互式开发 / Notebook:现状是缺口,不是已经打通

这是目前平台里**明确存在差距**的一块,如实说清楚现状:

- JupyterHub 之前部署验证过(接了 Keycloak SSO,按组分配访问权限,见
  `docs/decisions/025-jupyterhub-sso.md`),但**现在是 park 状态,没有在
  跑**——`apps/definitions/` 里没有它,定义在
  `environments/cloud-full/pending-definitions/jupyterhub.yaml`。要用需要
  先 un-park(参考 `README.md`/`docs/operations/tuning.md` 里 park/unpark
  的操作方式),这台机器的资源余量要先确认够不够,不建议在 Trino 已经
  在跑、资源偏紧的时候顺手再拉起来。
- 即使 un-park 之后,现在也**没有"打开 notebook 就自动连好 Trino/带着
  你自己权限"这种一键体验**——用户需要自己在 notebook 里装 Trino 的
  Python client(比如 `trino` 这个包)、自己填连接串和账号密码。对照
  用户提到的"字节 Dolado"/"火山引擎"那类平台(一键生成开发环境、自动
  带凭据、和数据目录打通),这个平台目前是没有的,这是一个真实、还没
  设计的产品差距,不是"已经很接近了只是细节没做"。
- 训练模型用的 notebook 环境(如果要接 MLflow 实验跟踪),同样需要
  自己在 notebook 里装包,参考 `scripts/09-train-demo-model.sh` 里怎么
  用 MLflow client——这个脚本是在本机 Python 环境里跑的示范,不是在
  JupyterHub 里跑的,两者环境不完全一样,仅供参考连接方式。

如果这块是接下来要重点补的方向,值得先想清楚几个问题再动手(不是现在
就有答案):notebook 里怎么免密拿到"当前登录用户"对应的 Trino 凭据(不能
明文存密码)、要不要预置连接模板/starter notebook、和 OpenMetadata 目录
打通到什么程度(比如"在目录里点一张表,能直接生成一段可用的查询代码"这种
体验)。这些没有定论,记在这里是为了下次讨论有个起点,不代表已经决定
怎么做。

## 遇到问题去哪查

- 部署/网络类的坑:`docs/operations/troubleshooting.md`
- 想知道某个组件现在到底有没有在跑:`http://portal.local-lite.test`
  (现场探测,不是文档里的静态描述)
- 这个平台的架构全貌和每个组件的定位:`docs/architecture.md`
- 具体某个设计为什么这么做:`docs/decisions/`(按编号找对应的 ADR)
