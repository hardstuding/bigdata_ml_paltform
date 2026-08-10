# 017. Trino 接 Keycloak OAuth2 SSO:比 ArgoCD/Grafana 麻烦得多的原因

- 状态: 已采纳(2026-08-09,已验证:OAuth2 授权跳转确认可用,浏览器完整登录待用户在自己机器上二次确认)

## 背景

ArgoCD/Grafana 接 Keycloak SSO 时,浏览器和后端各自的域名/协议对上就够了。
Trino 不一样——它的 OAuth2 认证**硬性要求自己开 HTTPS 监听器**
(`http-server.https.enabled=true`,官方文档原话:"Using TLS ... is required
for OAuth 2.0 authentication"),不能像 ArgoCD 那样把 TLS 完全交给 ingress
边缘终结。这条硬性要求牵出了一连串连锁配置,记录下来避免下次重新踩一遍。

## 决策

- **证书来源**:cert-manager 的 SelfSigned ClusterIssuer(`platform/cert-manager-issuers/`,
  这是 cert-manager 装了很久后第一次真正用上)。这不是浏览器可见的证书,只是
  ingress-nginx 到 Trino coordinator 之间集群内部这一跳,ingress-nginx 默认不
  校验后端证书,自签名够用,不用碰 Mac 的系统信任链。
- **证书格式**:cert-manager 的 `additionalOutputFormats: [{type: CombinedPEM}]`
  直接生成一份 cert+key 拼在一起的 PEM,Trino 原生支持这种格式当 keystore,
  不用转 JKS/PKCS12。**实测这个 cert-manager 版本(v1.21.1)生成的 key 名是
  `tls-combined.pem`**,不是网上常见示例写的 `key+crt.pem`——遇到这个组件的
  下一个人如果照抄网上的例子会踩这个坑,以 `kubectl get secret <name> -o
  jsonpath='{.data}'` 实测结果为准,不要直接抄文档。
- **证书必须写 `commonName`**:不写的话生成的自签证书 issuer DN 是空的,Java
  的 X509 解析器(Trino 用来读 PEM keystore)直接拒绝,报
  `CertificateParsingException: Empty issuer DN not allowed`。
- **证书文件不能挂在 `/etc/trino/` 底下**:那整个目录已经是 chart 自己的
  ConfigMap 挂载点,再嵌套挂一个 Secret 的 subPath 文件会导致 kubelet 建目录
  失败(`ContainerCannotRun`,报 "mounting a directory onto a file (or
  vice-versa)")。放在独立的顶层路径(`/secrets/trino-tls/`),`keystore.path`
  本来就允许任意绝对路径。
- **Ingress 不能用 chart 自带的 `ingress.enabled`**:`helm template` 验证过,
  它硬编码只转发到 Service 的 8080(http)端口,没有开关能改成 8443
  (https)。既然 OAuth2 要求关掉 http 监听器(`http-server.http.enabled=false`,
  不留未认证的明文端口),chart 自带的 ingress 配置根本用不上,自己在
  `apps/trino-tls/manifests/ingress.yaml` 里手写,`backend-protocol: HTTPS`
  annotation 让 ingress-nginx 用 HTTPS 连後端。
- **`internal-communication.shared-secret` 是强制的**:哪怕是单节点
  coordinator-only(`server.workers: 0`,没有独立 worker),开了认证之后
  Trino 启动时仍然强制校验这个配置项,不是可选项。
- **`internal-communication.https.required=true` 也要加**:关掉 http 监听器
  之后,Trino 算自己的 internal announcement URI 找不到 http 端口可用,报
  `NullPointerException: internalUri is null`,加这行让它改用 https 端口算。
- **`http-server.process-forwarded=true`**:ingress-nginx 默认给转发请求加
  `X-Forwarded-*` 头,Trino/Jetty 默认不信任、直接拒绝(406 "does not allow
  processing of the X-Forwarded-For header")。
- **域名解析改用 CoreDNS**:装到一半发现 Trino 官方 chart 不支持
  `hostAliases`(和 ArgoCD 能用不一样),推动了 ADR-016 里提到的 CoreDNS
  层面统一方案(`platform/coredns-custom/`)。过程中还踩了一个 CoreDNS 本身的
  坑:k3s 默认 Corefile 已经有一个 `hosts` 插件,同一个 server block 不能有
  第二个(报错 "this plugin can only be used once per Server Block",导致
  CoreDNS 一度 CrashLoopBackOff,集群 DNS 短暂中断)。必须用 `*.server`(在主
  block **外面**导入,相当于新开一个专门服务 `local-lite.test` 这个 zone 的
  独立 server block),不能用 `*.override`(在主 block 里面导入,会跟已有的
  `hosts` 插件冲突)。
- **2026-08-10 补充,一个隐藏了很久的 bug**:chart 的 coordinator Deployment
  模板把 `livenessProbe` 硬编码成 `httpGet` 打 8080(http)端口的 `/v1/info`,
  `values` 只能覆盖这个探针的数字字段(delay/period/timeout/threshold),换不了
  探针类型,`helm template` 验证过。我们关了 `http-server.http.enabled`
  之后,这个探针**从第一次部署 Trino 起就永远失败**,kubelet 每隔几十秒强杀
  重启一次容器——但 readiness/startup 探针用的是不挑端口的 exec 健康检查
  脚本,一直显示正常,所以 pod 表面看是 `Running`/`1/1 Ready`,实际在后台
  不停被杀重启,很容易被"看起来是健康的"这个表象骗过去(这次是重新验证
  Superset 连 Trino 时,因为查询过程中连接偶尔失败才发现)。chart 这条路
  走不通,改用 ArgoCD 的 `spec.ignoreDifferences`(声明在
  `apps/definitions/trino.yaml` 里,不是绕过 GitOps)+
  `scripts/07-fix-trino-liveness-probe.sh` 一次性 `kubectl patch` 成 exec
  探针。**教训**:验证一个组件"能用"不能只看 `kubectl get pods` 的
  READY 列一次——这个字段反映的是"当前这一刻",不反映"过去几分钟是不是在
  反复重启",部署完之后要么看 `RESTARTS` 列的数字有没有在涨,要么直接
  `kubectl describe pod` 看 Events 里有没有 `Killing`/`Unhealthy`。

## 后果

- 上面这一整串(TLS + OAuth2 + internal-communication + process-forwarded)
  几乎肯定不是 Trino 独有的坑——Superset/OpenMetadata/MLflow 接 SSO 时,
  "OAuth2 库要不要求 HTTPS"、"反向代理头要不要显式信任"这类问题大概率还会
  出现,排查时可以先对照这份清单。
- cloud-full/prod 用真实证书之后,`trino-tls` 这个自签证书 + 手写 Ingress
  的组合大概率要重新评估:真实域名下 chart 自带的 `ingress.enabled` 可能就
  够用(如果那时候 chart 版本已经支持指定 backend port),不代表这套手写
  方案要原样搬过去。
- coordinator-only 模式下的 `internal-communication.shared-secret` 会一直是
  必需项,cloud-full 拆出独立 worker 后,这个共享密钥变得更重要(worker 和
  coordinator 之间真实要用它做双向认证了),不是本地测试专属的摆设配置。
