# 025. JupyterHub 接 Keycloak SSO:复用 Grafana 的双域名模式

- 状态: 已采纳(2026-08-11,已验证:OAuth2 授权跳转确认可用,client_id/redirect_uri/PKCE 都对)

## 决策

官方 `jupyterhub/jupyterhub` chart(hub.jupyter.org 发布,和否决 PostHog 是
同一条"只用官方或官方支持的部署方式"筛选标准)。hub 镜像自带
`oauthenticator` 包(`pip show oauthenticator` 实测确认过,不是猜的),用
`GenericOAuthenticator`(entry point 名字是 `generic-oauth`,同样实测确认
过)接 Keycloak。

**域名解析用的是 Grafana 那套模式,不是 ArgoCD/Trino 那套**:
`authorize_url` 配浏览器能到达的域名(`keycloak.local-lite.test`),
`token_url`/`userdata_url` 配集群内部域名
(`keycloak-keycloakx-http.keycloak.svc.cluster.local`)。原因是
`GenericOAuthenticator` 和 Grafana 的 `auth.generic_oauth` 一样,天然把
"浏览器跳转用的地址"和"后端自己发请求用的地址"分成两个独立配置项;而
ArgoCD 的内置 OIDC 只有一个 `issuer` 字段,浏览器跳转和后端 token 交换
共用同一个值,才不得不用 `hostAliases`/CoreDNS 把同一个域名在集群内外
解析到不同地方(见 ADR-016/017)。**每接一个新组件先看它的 OIDC 客户端库
支不支持这种"两个 URL 分开配"的模式,能分开配就优先用,不用默认走
hostAliases/CoreDNS 那条更复杂的路**。

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
