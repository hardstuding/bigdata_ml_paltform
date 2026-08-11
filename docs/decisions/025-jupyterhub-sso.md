# 025. JupyterHub 接 Keycloak SSO

- 状态: 已采纳,**已用真实浏览器完整验证**(2026-08-11:登录 -> 拉起
  notebook pod -> JupyterLab 界面加载成功,全流程走通)。过程中改过三次:
  首次只验证了 OAuth2 授权跳转;真实登录后发现双域名模式不可用,改单一
  issuer;修完又碰到 403 "not authorized",补上 `allow_all`;修完又碰到
  singleuser 内存单位 500,`Mi` 改 `M` 才最终跑通。四个坑分别记在下面。

## 决策

官方 `jupyterhub/jupyterhub` chart(hub.jupyter.org 发布,和否决 PostHog 是
同一条"只用官方或官方支持的部署方式"筛选标准)。hub 镜像自带
`oauthenticator` 包(`pip show oauthenticator` 实测确认过,不是猜的),用
`GenericOAuthenticator`(entry point 名字是 `generic-oauth`,同样实测确认
过)接 Keycloak。

**域名解析最终用的是 ArgoCD/Trino/Argo Workflows 那套单一 issuer 模式**
(`authorize_url`/`token_url`/`userdata_url` 三个全部配同一个外部域名
`keycloak.local-lite.test`),不是最初以为能用的 Grafana 双域名模式——
下面"2026-08-11 更正"记录了为什么最初的判断是错的。hub pod 靠
`platform/coredns-custom/` 的自定义 DNS zone(集群内全局生效)解析
`keycloak.local-lite.test`,不需要额外配 hostAliases。

其他:
- db 用 chart 默认的 `sqlite-pvc`,不接共享 Postgres——hub 自己的状态
  (谁登录过、谁的 notebook 在跑)不是其他组件需要联动读取的数据,没有
  必要为了这个单独接一次 Postgres。
- `proxy.service.type` 从默认的 `LoadBalancer` 改成 `ClusterIP`——已经有
  ingress-nginx 做外部入口了,不需要 chart 再单独暴露一次。
- `singleuser` 的内存/CPU/存储都做了缩减(默认 1G 内存保证 + 10Gi 存储,
  对 local-lite 偏大),数据科学常用包(pandas/scikit-learn/mlflow 这些)
  默认镜像不带,和 `scripts/09-train-demo-model.sh` 在本机 Python 环境里
  手动 `pip install` 是同一个思路——用户在 notebook 里自己按需装,不为了
  这个单独 build 一份自定义镜像。

## 后果(首次验证时的记录,已被下面"2026-08-11 更新"取代)

- ~~只验证了 OAuth2 授权跳转...没有验证完整的登录链路~~——见文末
  "后果(2026-08-11 更新)",已经用真实浏览器走完全流程。
- `hub.config.JupyterHub.admin_access: true`(chart 默认值,没改)让 Hub
  管理员能访问所有用户的 server,`Authenticator.admin_users` 现在已经配了
  `admin`/`zhenghe`(见更正 2)。

## 2026-08-11 更正:双域名模式实测不可用

用户真实用浏览器登录后,`/hub/oauth_callback` 报 500。查 hub pod 日志
(kubectl logs 在这台机器上一直会被本机代理拦截,`colima ssh -- sudo
crictl logs <container>` 是这个项目里一直在用的绕过办法):

```
tornado.httpclient.HTTPClientError: HTTP 401: Unauthorized
Error Fetching user info... 401 GET http://keycloak-keycloakx-http.keycloak.svc.cluster.local/.../userinfo
```

再查 Keycloak 自己的日志,真正原因写得很清楚:

```
type="USER_INFO_REQUEST_ERROR" ... error="invalid_token"
reason="Invalid token issuer. Expected 'http://keycloak-keycloakx-http.keycloak.svc.cluster.local/auth/realms/platform'"
```

根因:Keycloak 签发的 access token,`iss` claim 固定成发起 `/auth`
授权请求时浏览器用的域名(`keycloak.local-lite.test`)。但 userinfo 端点
校验 token 时,会按**当前这次请求实际打进来用的 Host** 重新计算一遍
"期望的 issuer",这里请求是从 hub pod 发到集群内部域名
(`keycloak-keycloakx-http...svc.cluster.local`),算出来的期望值跟 token
里实际的 `iss` 对不上,直接 401 拒绝——这是 Keycloak 自己的 token 校验
行为,不是这边配置写错。

**为什么 Grafana 用同一套双域名模式没出问题**:Grafana 的
`auth.generic_oauth` 拿到 token 后是直接解码 `id_token` 里的 claim,
没有额外对 userinfo 端点发请求;JupyterHub 的 `GenericOAuthenticator`
则会真的对 `userdata_url` 发一次独立的 HTTP 请求(见
`oauthenticator/oauth2.py` 的 `token_to_user` -> `httpfetch`),才踩中这个
issuer 校验。也就是说"双域名模式能不能用"不只取决于 OIDC 客户端库
"支不支持两个 URL 分开配"(它确实支持),还取决于这个客户端库**内部会不会
真的对 userinfo 端点发起独立请求**——只看配置项存不存在、只测到授权跳转
这一步,测不出这个坑,必须走完真实浏览器登录才会暴露。

**教训(和 ADR-017 的 livenessProbe 坑同一个教训的再一次印证)**:OIDC/SSO
类集成只验证"跳转链接参数对不对"不够,必须真的登录一次、走到回调成功、
拿到实际身份为止。以后任何新组件的 SSO 集成,"验证完毕"的判断标准都应该是
真实浏览器登录成功,不是 curl 确认跳转 URL 或 client_id 对。

修复:`token_url`/`userdata_url` 改回和 `authorize_url` 一样的外部域名,
变成单一 issuer 模式,和 ArgoCD/Trino/Argo Workflows 统一。改完后
issuer 从头到尾都是同一个值,不会再触发这个校验。

## 2026-08-11 更正 2:issuer 坑修好之后,又冒出"403 not authorized"

上一条更正修完 issuer 问题后,用户真实登录,Keycloak 认证走完了(能看到
JupyterHub 的 403 页面,而不是卡在 Keycloak 登录页或跳转失败),但页面
显示:

```
403 : Forbidden
Sorry, you are not currently authorized to use this hub.
Please contact the hub administrator.
```

这是 JupyterHub 3.x 起的默认行为:`Authenticator.allow_all` 默认是
`false`。**认证(你是谁,Keycloak 说了算)和授权(JupyterHub 允不允许这个
身份用这个 hub)是两件独立的事**,OIDC 配置全对、登录流程全通,不代表
JupyterHub 就会放行——没有任何 `allow_all`/`allowed_users`/`admin_users`
配置的情况下,默认谁都不在白名单里,直接 403。之前只验证到"OAuth2 授权跳转
参数对不对",这个坑要走完真实登录才会暴露,和更正 1 是同一个教训的再一次
印证。

修复:加 `Authenticator.allow_all: true`(local-lite 阶段就一两个人用,
不维护白名单),同时把 `admin`、`zhenghe` 都加进 `admin_users`。

## 2026-08-11 更正 3:allow_all 修完,登录成功但拉 notebook 时 500

403 修好、真的能登录进 Hub 首页之后,JupyterHub 要给这个用户建 spawner
时又报 500,hub pod 日志:

```
traitlets.traitlets.TraitError: 1536Mi is not a valid memory specification.
Must be an int or a string with suffix K, M, G, T
```

`singleuser.memory.guarantee`/`limit` 这两个字段最终传进 KubeSpawner 自己
的 `mem_guarantee`/`mem_limit` traitlet,是 Python 端做校验,只认十进制
`K/M/G/T` 后缀,不认 Kubernetes 资源规格惯用的二进制 `Ki/Mi/Gi`
后缀——写成 `1536Mi` 直接 `TraitError`。和 `cpu.guarantee` 必须写纯数字
不能带单位是同一类"这个字段实际不走 k8s API 校验,走 chart/Python 自己的
校验"的坑。`512Mi`/`1536Mi` 改成 `512M`/`1536M` 后解决。

## 后果(2026-08-11 更新)

用 [Claude in Chrome](https://chromewebstore.google.com/detail/fcoeoabgfenejglbffodgkkbkcdhcgfn)
插件跑通了完整的真实浏览器验证:点"Sign in with Keycloak" -> 复用已有
Keycloak 会话直接登录 -> `/hub/spawn-pending/admin` 显示"Server
requested" -> `jupyter-admin` pod 在集群里 `1/1 Running` -> 浏览器自动跳
`http://jupyterhub.local-lite.test/user/admin/lab` -> JupyterLab 界面
完整加载,文件浏览器显示 `/home/jovyan`。这是这个项目里第一次由 Claude
自己(不是让用户代劳)走完一个组件的完整浏览器 OIDC 登录验证,之前
ArgoCD/Grafana/Trino 等组件都止步于"curl 确认跳转参数对",没有真正测过
浏览器交互这一层——上面三个坑全是只有走到这一步才会暴露的。
