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
- **OIDC 域名解析**:浏览器端靠 `/etc/hosts`;集群内部需要主动发起 OIDC
  discovery/token 交换的 pod(目前只有 ArgoCD server,Grafana 走的是
  `auth_url`/`token_url` 分离配置,不需要这个)用 `global.hostAliases` 指向
  `ingress-nginx-controller` 的 ClusterIP——和浏览器走同一个 Ingress 入口,
  只是各自到达 Service 的路径不同,不再需要给 Keycloak 单独开一个"浏览器/集群
  内部各解析一次"的专用 Service。

## 连带修的问题

调试过程中发现 Keycloak 用 `start-dev` 自带的临时 H2 数据库,pod 一重启
(比如 colima 重启)就把 realm/client 全部清空,而且 ArgoCD 完全看不出来
(Application 照样显示 Healthy)。顺手把 Keycloak 也接上了共享 Postgres,
和 hive-metastore/mlflow 同一个模式,数据落盘。这不是这次的直接目标,但是
在同一个文件里、同一次改动的自然延伸,拆开单独走一次评估价值不大。

## 后果

- Trino/Superset/OpenMetadata/MLflow 重新启用时,应该同步接上各自的 Ingress
  域名,而不是继续用 port-forward——统一域名也是给它们接 Keycloak SSO 的前提
  (原因同 ArgoCD:回调地址要是浏览器和后端都能达成一致的固定地址)。
- cloud-full 起,`hostAliases`、`server.insecure`、`http://` 协议这些 local-lite
  特有的配置都要换成真实域名 + TLS,届时这个 ADR 里描述的接线方式整体替换,
  不是在此基础上叠加。
- cert-manager 目前仍然是"装了但没用"的状态,cloud-full 阶段第一次真正启用时
  需要验证 ACME 流程本身能不能跑通(取决于 cloud-full 环境能不能被 Let's
  Encrypt 挑战验证到,还是要用内部 CA)。
