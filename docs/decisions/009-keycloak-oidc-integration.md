# 009. ArgoCD / Grafana 接入 Keycloak OIDC

- 状态: 已采纳(2026-08-08)

## 背景

Keycloak 在 Phase 0 就上了,但只是"装了个空的身份服务",没有组件真的用它登录。用户提出既然有了 Keycloak,能接的组件应该现在就接上,不要等到后面才做。

## 决策

新建一个 `platform` realm(和 Keycloak 自己的 `master` 管理 realm分开),给 ArgoCD、Grafana 各建一个 confidential client,配好 OIDC 后两者都能用 Keycloak 账号登录。

RBAC 简化处理:ArgoCD `policy.default: role:admin`,Grafana `role_attribute_path: "'Admin'"`——只要是 platform realm 里的账号,登进去就是管理员。

## 理由

- 现在就接,比"先攒着以后一起做"成本低——组件数量还少,一个个接的边际成本不高,拖到后面组件多了反而要一次性做一堆。
- RBAC 从简是有意选择,不是漏做:现在只有一个受信用户在用,提前做精细的角色映射是在为不存在的需求设计。等真的有多个人/多个信任级别的账号要区分权限时,再按 Keycloak group 做映射(ArgoCD 和 Grafana 都支持通过 `groups` claim 映射角色),不需要重新架构,只是加配置。

## 实现细节(方便以后接新组件抄作业)

- Realm: `platform`,issuer(集群内部)`http://keycloak-keycloakx-http.keycloak.svc.cluster.local/auth/realms/platform`。注意 `/auth` 前缀——这是 codecentric/keycloakx 这个 chart 的默认约定,虽然 Keycloak 26.x 本身默认已经不用这个前缀了,但这个 chart 用 `KC_HTTP_RELATIVE_PATH=/auth` 保留了旧路径,踩过一次坑才发现(kcadm 登录报 404,不是密码错,是路径错),见 troubleshooting.md。
- Client secret 不进 git:每个组件建一个 `<name>-oidc-secret` Secret 存 `clientSecret`,组件的 Helm values 里通过 `$__env{}`(Grafana)或 `$<key>`(ArgoCD 专用的 argocd-secret 变量替换语法,注意 ArgoCD 只认自己那个 `argocd-secret` 里的 key,不认别的 Secret)引用。
- **浏览器地址和后端地址要分开配置**:用户浏览器跳转登录用的 URL(`auth_url`)必须是用户电脑能访问到的地址(本地是端口转发的 `localhost:8180`);组件后端自己发起的 token 交换/用户信息请求(`token_url`/`api_url`)必须走集群内部 DNS,两者不一样是正常的。以后接 Superset/Airflow/JupyterHub/OpenMetadata 照这个模式来。

## 后果

- Keycloak realm/client/user 是通过 `kcadm.sh` 命令行手动建的,不是声明式配置,不在 GitOps 管理范围内,机器重建(比如换集群)时需要重新跑一遍——已脚本化为 `scripts/03-configure-keycloak.sh`,并已补进 README"从零拉起"手册(2026-08-11)。

## 2026-08-11 更正(几处随项目演进已经过时的描述)

- 上面"实现细节"里说的 issuer 是"集群内部地址"(`keycloak-keycloakx-http...svc.cluster.local`),这是 ADR-016/017 引入真实 Ingress + CoreDNS 自定义 zone **之前**的早期状态。现在 ArgoCD(以及 Trino、Argo Workflows)的 issuer 统一是外部域名 `http://keycloak.local-lite.test/auth/realms/platform`,靠 `platform/coredns-custom/` 让集群内 pod 也能解析这个域名——见 ADR-016/017。
- 上面"浏览器地址和后端地址要分开配置...以后接 Superset/Airflow/JupyterHub/OpenMetadata 照这个模式来"这条建议**不是普遍成立的**,已经在 JupyterHub 上踩过反例(见 ADR-025 的"2026-08-11 更正"):能不能分开配,不只取决于 OIDC 客户端库支不支持两个 URL 分开填,还取决于这个客户端库内部**会不会真的对 userinfo 端点单独发一次请求**——如果会,Keycloak 会校验 token 的 issuer 和这次请求实际用的域名是否一致,双域名模式就会失败。只解码 `id_token` claim、不额外请求 userinfo 的客户端(比如 Grafana 的 `auth.generic_oauth`)才能安全用双域名模式。**每接一个新组件,双域名模式配完之后必须真的用浏览器登录一次验证到底,不能只看跳转链接对不对就判定"能用"。**
- 之前写的 `configs.cm.url` 写死 `http://localhost:8080` 这条也已经不成立,现在是 `http://argocd.local-lite.test`(ADR-016 引入真实域名时改的)。
