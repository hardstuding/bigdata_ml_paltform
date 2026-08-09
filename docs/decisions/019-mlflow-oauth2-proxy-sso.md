# 019. MLflow 没有原生 OIDC,用 oauth2-proxy 挡在前面接 Keycloak SSO

- 状态: 已采纳(2026-08-09,已验证:OAuth2 授权跳转,client_id/redirect_uri 都对)

## 背景

Trino、Superset、OpenMetadata 都有原生 OIDC/OAuth2 支持,直接在各自的
Helm values 里配置就行(见 ADR-017,以及 troubleshooting.md 里 OpenMetadata
那条)。MLflow 开源版不一样——它只有本地用户名密码的 basic-auth app,不支持
接外部 OIDC provider。

社区有个 `mlflow-oidc-auth` 插件能做到,但要求自己重新 build 一份装了这个
pip 包的镜像。这和这个项目一直坚持的"尽量用官方镜像,不自己维护镜像构建
流水线"取舍冲突(和 ADR-008 拒绝 Bitnami 镜像是同一条理由:多一条自己维护
的镜像构建链路,长期成本不比省下来的东西划算),没有采用。

## 决策

用官方 `oauth2-proxy`(quay.io/oauth2-proxy 项目,不是我们自己拼的 hack)
挡在 MLflow 前面:

- Ingress 直接指向 oauth2-proxy 的 Service,不是 MLflow 自己的 Service。
- 浏览器访问先被 oauth2-proxy 拦截,没登录就跳 Keycloak,登录成功后
  oauth2-proxy 把请求代理给 MLflow(`upstreams` 配 MLflow 的集群内部地址)。
- MLflow 本身完全不需要知道 OIDC 这回事,继续用它自己默认的(无认证)配置。
- Keycloak client 的密钥存在 `mlflow/oauth2-proxy-secret`(`client-id`/
  `client-secret`/`cookie-secret` 三个 key,`existingSecret` 引用),
  `cookie-secret` 必须是 16/24/32 字节的合法 base64(不能用
  `scripts/00-generate-secrets.sh` 里通用的 `gen_password`,那个函数会
  剔除 `+/=` 字符破坏编码,和当初 `airflow-fernet-key` 一样的坑)。
- `skip_provider_button = true`——不加的话未登录访问会先停在 oauth2-proxy
  自己的"Sign In"中间页,要点一下按钮才跳 Keycloak,和其他组件"点开直接
  跳转"的体验不一致。只有一个 provider,没有必要保留选择页。

## 后果

- **这是给"应用本身没有原生 SSO"这类组件的通用解法,不是 MLflow 专属**。
  以后如果加了其他同样缺 OIDC 支持的组件,应该优先考虑复用同一个
  oauth2-proxy 模式,而不是每次都去找有没有专属的第三方认证插件/要不要
  自己 build 镜像。
- MLflow 自己完全没有认证概念,`oauth2-proxy` 只挡住了"进不进得来"这一层
  (必须是 Keycloak 里的合法用户),进来之后 MLflow 内部没有任何按用户区分
  的权限控制——所有登录过的人权限完全一样,是 MLflow 开源版本身的限制,
  不是这次接入方式的问题。如果以后需要更细粒度的权限,那是 MLflow 商业版
  (Databricks)的能力,开源版做不到,不用在这个方向上找变通方案。
- `cookie_secure = false` 是 local-lite 专属(没有 TLS,见 ADR-016),
  cloud-full/prod 接了真实 TLS 之后要改回 `true`(默认值),否则 cookie
  会被浏览器当成不安全连接拒绝存储,登录会一直被弹回登录页。
