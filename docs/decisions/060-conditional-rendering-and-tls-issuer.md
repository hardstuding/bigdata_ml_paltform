# ADR-060:条件生成机制(`render-if`)+ TLS 证书签发方按环境切换

日期:2026-08-22
状态:已实现;ACME 那一档**还没有在真实环境验证过**(缺真实域名和 ICP 备案)

## 背景

[ADR-059](059-resource-profiles.md) 把"同一份定义、不同环境不同数字"这层
解决了(`{{RES:key}}` 占位符)。但还剩一类差异**占位符表达不了**:几个
环境需要的不是同一个结构里换数字,而是**互斥的几选一**。

第一个真实例子是 cert-manager 的 ClusterIssuer:

- local-lite / cloud-full 用自签(`spec.selfSigned: {}`)——没有真实域名,
  ACME 的 HTTP-01 挑战根本走不通;而且这里的证书只用于集群内部一跳
  (ingress-nginx → Trino 的 https 监听器,Trino 的 OAuth2 认证硬性要求
  它自己的 https 打开),不需要被浏览器信任。
- prod 用 ACME(`spec.acme.{server,email,solvers}`)。

两个 spec 结构完全不同,拼不出一份带占位符的通用模板。而且**同时存在是
错的**:ACME issuer 部署在没有真实域名的环境里会一直报错重试。

用户 2026-08-22 明确要求的是"留好配置项,能够生效就好"——不测试,但改
配置要真的能切过去。写一段"以后要怎么改"的注释不满足这个要求:注释不会
生效,也没有任何机制保证它和代码不脱节。

## 决策

### 1. 模板支持条件生成

`templates/` 下的模板文件,第一行可以写:

```
# render-if: <config键> == <值>
```

渲染时:

- 条件成立 → 剥掉这一行,其余按原有规则渲染;目标文件**不存在也会建出来**
  (对条件生成的文件,"不存在"是正常的——上一次渲染的是别的环境)。
- 条件不成立 → 目标文件如果存在就**主动删除**。

**为什么条件不成立时要删而不是放着不管**:放着不管的话,从 prod 切回
cloud-full 会留下一个 ACME issuer 的残留文件,ArgoCD 照样会把它同步上去
——正是这个项目反复踩的"看起来切干净了,其实没有"。`--check` 模式下这两种
情况(该有的缺了 / 不该有的还在)都算漂移,退出码非 0,CI 能拦住。

条件里引用的键在当前环境的 `config.yaml` 里不存在,直接报错退出,不当成
"条件不成立"处理——不给默认值悄悄兜底,和这个仓库其它地方一致。

### 2. ClusterIssuer 三个环境同名

名字统一叫 `platform-issuer`,换环境变的是它背后接自签还是真实 CA。

原来叫 `local-lite-selfsigned`——名字里同时写死了**环境**和**签发方式**,
等于把"这是哪一档"泄漏给了所有引用方,换环境时每个 Certificate 资源的
`issuerRef.name` 都得跟着改。改成同名之后,`apps/trino-tls/manifests/
certificate.yaml` 这类引用方一行都不用动。

### 3. ACME 参数进 `environments/prod/config.yaml`

`tls_issuer_mode`(selfsigned | acme,三个环境都必填,值不合法直接报错)、
`tls_acme_server`、`tls_acme_email`。

后两个**不进"每个环境都必填"那份校验**——local-lite 的配置里塞一个假的
ACME 邮箱纯属噪音。代价是模板里用到了但配置里没有时要在替换那一步报错,
报错信息里说清楚该去哪补,不能只抛一个 KeyError。

`tls_acme_server` 默认给的是 **Let's Encrypt staging** 地址,不是
production。理由:production 有每周签发限额,配错了反复重试会打光额度,
被限流之后只能等一周。正确顺序是 staging 跑通一次再换 production,这一点
写在 `environments/prod/config.yaml` 的注释里。

## 后果

- 现在有了一个通用的"互斥几选一"表达方式,不只服务 TLS 这一个场景。预计
  后面会用到的地方:prod 专属的 nodeAffinity/taint 相关 manifest、
  local-lite 专属的 `platform/coredns-custom/`(prod 阶段应该整个不生成,
  见 `environments/prod/README.md`)。
- **ACME 这一档仍然是"结构就位、没跑通过"。** 真正生效还依赖三件这个
  仓库控制不了的事:真实域名解析指到集群入口、80 端口从公网可达(HTTP-01
  挑战要用)、国内还要 ICP 备案。不要把"配置项存在"读成"证书能签下来"。
- 用公司内部 CA 而不是 Let's Encrypt 的话,把
  `templates/platform-cert-manager-issuers/clusterissuer-acme.yaml` 整个
  换成 CA 类型的 ClusterIssuer(`spec.ca.secretName` 指向根证书 Secret),
  名字保持 `platform-issuer` 不变,其余不用动。
