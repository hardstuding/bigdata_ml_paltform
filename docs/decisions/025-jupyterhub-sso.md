# 025. JupyterHub 接 Keycloak SSO

- 状态: 已采纳,决策内容已更正(2026-08-11 首次验证 OAuth2 授权跳转;
  2026-08-11 真实浏览器登录后发现双域名模式实际不可用,已改为单一 issuer
  模式并验证通过——见下面"2026-08-11 更正")

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

## 后果

- 只验证了 OAuth2 授权跳转(client_id/redirect_uri/PKCE 都对),没有验证
  完整的"登录 -> 拉起 notebook -> 跑代码"这条链路——PKCE 流程需要浏览器
  保存 code_verifier,curl 测不了完整流程,留给用户在自己机器上用真实
  浏览器验证。
- `hub.config.JupyterHub.admin_access: true`(chart 默认值,没改)让 Hub
  管理员能访问所有用户的 server,但"谁是管理员"这次没配
  (`Authenticator.admin_users` 留空)——local-lite 阶段只有一个人用,
  这个问题不大,多人使用之前需要补上。

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
