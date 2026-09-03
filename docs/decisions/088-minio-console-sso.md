# ADR-088:MinIO 控制台接 Keycloak,策略只给 platform-team

日期:2026-08-31
状态:**已部署验证 —— 但 SSO 登录这一半没成立,见文末「实机验证的结果」**

## 问题

zhenghe 2026-08-31 问:"minio 的管理界面,我看你好像在门户上没有提供这个
链接"。

查下来比"漏加一个链接"更彻底:`apps/components/minio.yaml` 里
`consoleService: type: ClusterIP`,**MinIO 控制台压根没有 Ingress,从集群外
访问不到**。要看对象存储只能 `kubectl port-forward`。

这是个真实的能力缺口:数据工程师排查"这张 Iceberg 表的文件到底长什么样"
"备份真的传上去了吗",现在只能靠命令行。
`platform/network-policies/manifests/minio.yaml` 里那句"9001 没有开……真要
用再加"写在那儿好几天了,现在就是那个时候。

## 决定

### 1. 接 Keycloak,不用 root 账号登录

MinIO chart 5.4.0 原生支持 `oidc`,不需要 oauth2-proxy 那层。接上之后它是
平台第 14 个走 SSO 的工具,和其余 13 个一致:**谁登录的可追溯**,而不是
所有人共用 `minio-root` 那个密码。

Keycloak 那边需要一个**单独的 client scope**(`minio-policy`),不能复用
现有的 `groups`:MinIO 找的 claim 叫 `policy`
(`MINIO_IDENTITY_OPENID_CLAIM_NAME`),而 `groups` mapper 输出的 claim 叫
`groups`,**名字对不上**。改现有 mapper 的名字会波及所有依赖 groups claim
的组件(Superset / Trino / 门户 / 权限门户……)—— 那正是 2026-08-29 一连修
了三处的那类问题,不能为了 MinIO 再动它。

`full.path=false` 让 claim 里是 `platform-team` 而不是 `/platform-team`。
带斜杠的话 MinIO 按字面匹配策略名,**永远匹配不上而且不会报错**,只表现为
"登录成功但什么桶都看不到"。

### 2. 策略**只给 platform-team**,这是安全决定不是偷懒

**MinIO 里放的是 Iceberg 的 parquet 原始文件。谁能读 `lakehouse` 桶,谁就
绕过了整套 OPA 权限。**

平台的数据权限模型([ADR-051](051-trino-opa-access-control.md) 的表级授权、
[ADR-074](074-superset-impersonation.md) 的行列级脱敏)有一个隐含
前提:**数据只通过 Trino 访问**。对象存储是那层策略的下面一层,直接读文件
就完全绕开了。分析师在 Superset 里被脱敏的手机号,从 MinIO 控制台点一下
就能下载明文。

所以:

| 组 | MinIO 策略 |
|---|---|
| `platform-team` | 全权(`s3:*` + `admin:*`) |
| `data-analysts` / `algorithm-team` / `viewers` | **没有策略** —— 能通过 SSO 登录,但看不到任何桶 |

platform-team 拿全权**不增加暴露面**:他们本来就能通过 Trino 读全部数据
(`is_platform_admin` 不受表级授权约束,见 `apps/opa/policy/trino.rego`)。

门户上这张卡片放在「运维」分类,而那一类在 `CATEGORY_AUDIENCE` 里只对
platform-team 可见 —— 两边一致。**有一条测试专门守这个**
(`test_必须在运维分类里`),理由写在测试里:把卡片挪进别的分类,轻则给所有
人显示一个点进去会被拒的链接,重则有人顺手把 MinIO 策略也放开。

**想给别的组开权限之前,先回答:它绕过的那些行列级策略,你打算怎么办?**
一个可能的答案是按桶/前缀细分(比如只给 `mlflow` 桶不给 `lakehouse`),
但那需要先想清楚模型产物算不算敏感数据 —— 没有真实需求之前不预先设计。

### 3. NetworkPolicy 单独一条,只放行 ingress-nginx

不并进现有那条消费者列表:那条放行的是十几个业务命名空间(它们要连 S3
API 存数据),而控制台是给人用的管理界面,唯一合法的来源是 ingress-nginx。
混在一起等于让任何一个业务 Pod 都能打管理面。

## 还没做的

- **没上过集群**。验证步骤在 `docs/project/next-boot-checklist.md`。
- **审计**:MinIO 自己的 audit log 没有接到平台的日志/审计链路上。所以
  "谁登录了"在 Keycloak 那边有记录,"他下载了哪个文件"没有。真要做数据
  出境审计的话这是必须补的一环 —— 但那和 [ADR-066](066-trino-query-audit.md)
  是同一类工作(接一条 Kafka → Iceberg 的链路),不在这次范围内。
- **`minio-root` 那个密码仍然存在**,并且仍然能用(chart 的 `existingSecret`
  是组件之间连接用的,不能去掉)。SSO 是**多了一条**登录路径,不是关掉了
  原来那条。想真正关掉 root 登录要设 `MINIO_BROWSER_LOGIN_ANIMATION` 之外
  的东西,而且会让排障时"SSO 挂了就进不去"——现阶段不做这个取舍。

---

## 实机验证的结果(2026-09-01):**SSO 登录那一半没成立**

如实记,因为这份 ADR 的标题就是「接 Keycloak」。

**成立的部分**:

- 控制台**现在有对外入口了**(`minio.<域名>`,之前完全访问不到,只能
  port-forward)—— 这是这次最实际的收益
- `platform-team` 策略建出来了(`mc idp openid ls` / `mc admin policy ls` 都确认)
- 服务端 OIDC **确实生效**:`mc idp openid info` 显示 `enable: on`,
  9 个 `MINIO_IDENTITY_OPENID_*` 环境变量齐全,配置从 Keycloak 拉得到
- Keycloak 侧的 `minio` client 和 `minio-policy` claim mapper 都建好了

**没成立的部分**:控制台的登录页**不提供 Keycloak 登录按钮**。
`/api/v1/login` 一直返回 `{"loginStrategy":"form","redirectRules":null}`。

排查过的(都不是原因):

- 服务端 OIDC 没生效 —— 不是,`enable: on`
- 连不上 Keycloak —— 一开始确实是(NetworkPolicy 漏了 minio,已修),
  修完之后 MinIO 干净启动、0 重启,discovery 拿得到
- 反向代理后面缺 `MINIO_BROWSER_REDIRECT_URL` —— 试过,加了没变化(已还原)
- 请求的 Host 带不带端口 —— 两种都试过,一样

**结论**:`RELEASE.2025-09-07` 这个版本的内嵌控制台不再暴露 OIDC 登录入口。
MinIO 近年把完整的 Console/SSO 挪进了商业版(AIStor),开源服务端保留的是
精简版对象浏览器。**这是上游的产品决定,不是配置问题** —— 所以没有继续
往下试(改配置试不出来一个上游没有的功能)。

**现在的实际状态**:控制台可访问,用 root 账号登录
(`./scripts/show-credentials.sh --show` 看密码)。

**为什么 OIDC 的配置保留着,不删**:

1. 它对 **STS 是有用的** —— `AssumeRoleWithWebIdentity` 用同一份配置,
   程序拿 Keycloak 的 token 换 MinIO 的临时凭据这条路是通的,和控制台无关。
2. 哪天上游把控制台的 SSO 加回来,或者换成别的控制台(比如单独部署一个
   开源 Console),配置现成的。
3. 删掉它反而会让"为什么 MinIO 不接 SSO"这个问题以后被重新问一遍。

**没有降级成"给所有人开 root 密码"**:策略只给 platform-team 这条设计
不变(理由见上面「策略只给 platform-team」那节 —— 读 lakehouse 桶等于绕过
整套 OPA 行列级权限),而 root 密码本来就只有 platform-team 拿得到。

