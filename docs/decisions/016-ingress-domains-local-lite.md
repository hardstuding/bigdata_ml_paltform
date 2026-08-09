# 016. local-lite 用真实 Ingress + 静态域名替代 port-forward,TLS 留到 cloud-full

- 状态: 已采纳(2026-08-09,已验证:ArgoCD/Keycloak/Grafana)

## 背景

之前每个要在浏览器里访问的组件都要单独 `kubectl port-forward`,而且 ArgoCD 的
OIDC 登录为了让浏览器和集群内部 pod 用同一个 issuer 域名,专门搭了一套
split-horizon DNS 的土办法(`apps/keycloak-local-access/`,见
troubleshooting.md 里已标记废弃的那条记录)。组件一多,端口转发管理成本上升,
而且这套 hack 不通用,每接一个新组件的 OIDC 都要重新想一遍怎么绕。

## 决策

- **域名约定**:`<组件>.local-lite.test`,统一在 Mac 的 `/etc/hosts` 里指向
  `127.0.0.1`。
- **入口**:全部走已经装好的 ingress-nginx,不再单独 port-forward。关键发现:
  colima 的 docker runtime 会自动把 k3s LoadBalancer service 暴露的 80/443
  转发到 Mac 的 `localhost`,不需要额外配置(细节见 troubleshooting.md)。
- **TLS**:local-lite 阶段先不接——ArgoCD 切到 `server.insecure: true`,让
  ingress-nginx 用明文 HTTP 转发,这是 ArgoCD 官方文档里"跑在反向代理后面"的
  标准姿势之一,不是本地专用的临时妥协。cert-manager 已经装了但一直没真正用起来,
  留到 cloud-full 接真实域名 + ACME(Let's Encrypt 或内部 CA)时再启用,不在
  local-lite 用自签证书折腾 Mac 的信任链(收益低,还要碰系统信任设置)。
- **OIDC 域名解析**:浏览器端靠 `/etc/hosts`。集群内部需要主动发起 OIDC
  discovery/token 交换的 pod(ArgoCD、Trino,后续 Superset/OpenMetadata/MLflow
  大概率也需要)一开始给 ArgoCD 用的是 `global.hostAliases` 指向
  `ingress-nginx-controller` 的 ClusterIP,但装 Trino 时发现它的官方 chart不
  支持 hostAliases(per-pod 加不了),这条路对所有组件都通用不了。改成在
  **CoreDNS 层面**一次性解决:`platform/coredns-custom/` 用 k3s 自带的
  `coredns-custom` 扩展点开一个专门服务 `local-lite.test` 这个 zone 的
  server block(细节和踩的坑见 ADR-017),所有 pod 不用任何额外配置就能解析
  `*.local-lite.test`。ArgoCD 那条 `global.hostAliases` 配置目前还留着
  (不冲突,只是冗余),后续可以清掉统一成这一种机制。

## 连带修的问题

调试过程中发现 Keycloak 用 `start-dev` 自带的临时 H2 数据库,pod 一重启
(比如 colima 重启)就把 realm/client 全部清空,而且 ArgoCD 完全看不出来
(Application 照样显示 Healthy)。顺手把 Keycloak 也接上了共享 Postgres,
和 hive-metastore/mlflow 同一个模式,数据落盘。这不是这次的直接目标,但是
在同一个文件里、同一次改动的自然延伸,拆开单独走一次评估价值不大。

## 后果

- Superset/OpenMetadata/MLflow 重新启用时,应该同步接上各自的 Ingress 域名,
  而不是继续用 port-forward——统一域名也是给它们接 Keycloak SSO 的前提
  (原因同 ArgoCD:回调地址要是浏览器和后端都能达成一致的固定地址)。Trino
  已经这么做了,见 ADR-017。
- cloud-full 起,`hostAliases`、`coredns-custom`、`server.insecure`、`http://`
  协议这些 local-lite 特有的配置都要换成真实域名 + TLS,届时这个 ADR 里描述的
  接线方式整体替换,不是在此基础上叠加。
- cert-manager 已经在 Trino 身上第一次真正用起来了(自签证书给它的 OAuth2
  内部 HTTPS 监听器用,见 ADR-017),cloud-full 阶段要换成真正的 ACME 流程,
  需要验证能不能被 Let's Encrypt 挑战验证到,还是要用内部 CA。
