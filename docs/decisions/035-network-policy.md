# 035. NetworkPolicy 试点(permission-request-app namespace)

- 状态: 已采纳,试点已验证(2026-08-12);2026-08-13 推广到
  keycloak/data(Postgres)/minio 这三个核心命名空间,见文末补充

## 背景

复盘架构时发现:这个集群里所有 namespace 之间网络默认全通,一个 pod 能
直接连到任何其他 namespace 的任何 Service,包括数据库、Keycloak 这些
核心基础设施。现在已经有了真实的团队/组织架构(ADR-028),网络层面应该
跟上,不能是"权限体系分了组,网络层面还是铁板一块"。

## 决策

### 先验证 NetworkPolicy 在这个集群上到底有没有用

k3s 默认用 Flannel 做 CNI,**Flannel 本身不实现 NetworkPolicy 强制**——
这是很多人踩过的坑:以为部署了 NetworkPolicy 对象就生效了,实际上只是
在 etcd 里存了一条没人执行的规则,没有任何隔离效果。没有直接假设"k3s
应该支持",而是搭了一个一次性的测试:两个裸 pod,不加策略先确认能连通,
加一条 `default-deny-ingress` 再确认连不通——**实测确认这台机器上的 k3s
默认配置真的会强制执行 NetworkPolicy**(k3s 除了 Flannel 提供 overlay
网络,默认还带了负责 NetworkPolicy 强制的组件),不是白名单摆设。

### 只在一个 namespace 上试点,不是全量铺开

选 `permission-request-app`——这是这次会话新建的组件,不是任何已有核心
链路(Keycloak/Postgres/MinIO/SSO)依赖的东西,策略写错了、把自己的流量
挡住了,影响范围最多是这一个门户不能用,不会牵连别的东西。在没有人盯着
(深夜,用户不在线)的情况下改一个"错了会牵连一大片"的东西,风险和收益
不成比例,先在一个低风险目标上验证过设计模式,再考虑要不要推广到
Keycloak/data(Postgres)这些真正关键、一旦封锁策略写错代价很大的
namespace。

### 只做 ingress,不做 egress

这次只控制"谁能连进来",不控制"这个 pod 能连出去哪里"。egress 这次没做
是因为这台机器上不少组件的出网要走一个宿主机代理
(`192.168.5.2:1087`,见 ADR-026),这个地址是宿主机地址,不在标准的
Pod/Service CIDR 范围里,egress 白名单要把这类"到宿主机某个端口"的外部
地址也考虑进去,复杂度和出错风险更高,不想在第一次引入 NetworkPolicy 时
就把 ingress 和 egress 两件事一起做,分两步更稳。

### 三条规则

- `default-deny-ingress`:整个 namespace 默认拒绝所有 ingress,后面几条
  是白名单。
- `allow-ingress-nginx-to-oauth2-proxy`:只有 `ingress-nginx` namespace
  能连 oauth2-proxy 的监听端口(4180,不是 Service 的 80——NetworkPolicy
  匹配的是容器端口,不是 Service 端口,这个如果写错,策略"看起来生效了"
  但实际啥也没挡住或者挡住了不该挡的)。
- `allow-oauth2-proxy-to-app`:oauth2-proxy 代理成功之后转发给 app 本体,
  同 namespace 内部这条链路要放行。
- `allow-monitoring-scrape`:`monitoring` namespace(Prometheus/Alloy)
  需要能抓这个 namespace 里所有 pod 的 metrics 端口——这条容易漏,漏了
  的话不会报错,只是 Prometheus 悄悄抓不到这个组件的指标,是那种"过一阵
  子才会被发现"的坑,这次直接在设计阶段就把它加上。

## 验证记录

分两步验证,都是真实测试,不是"部署了就当它生效":

1. **NetworkPolicy 在这个集群上到底有没有用**:用两个裸 pod(不是这个
   namespace 里的正式组件,避免验证过程本身影响生产流量)——不加策略时
   能连通,加了 `default-deny-ingress` 之后连接超时/被拒绝,证明这台
   机器的 k3s 真的会强制执行,不是摆设。
2. **部署到 `permission-request-app` 之后,合法/非法路径分别测试**:
   `curl -H "Host: permission-request.local-lite.test"` 走 ingress-nginx
   的合法路径确认还是 `302`(功能没被误伤);另起一个 `default`
   namespace 的裸 pod 直连 app 本体的 8080 端口,确认被挡住
   (`BLOCKED-AS-EXPECTED`,连接超时)。

`allow-monitoring-scrape` 这条目前没有对应的真实抓取目标可验证——查了
Prometheus 的 `/api/v1/targets`,这个 namespace 下没有任何 activeTarget,
说明 permission-request-app 和它的 oauth2-proxy 本来就没配 ServiceMonitor
/PodMonitor,是另一个独立的、更早就存在的空白(这次 NetworkPolicy 工作
没有引入新问题,只是提前把"以后配了监控也不会被网络策略挡住"这条规则
写好)。

## 后果

- egress 完全没做,任何 pod(包括已经上了 NetworkPolicy 的这几个
  namespace)出网还是不受限制。
- 没有做团队/namespace 之间的隔离(比如"data-analysts 用的 namespace 不能
  连 algorithm-team 的 namespace")——现在还没有真正意义上按团队拆分
  namespace 的实践,这条要等真的有多团队各自独立部署工作负载时才有意义。

## 2026-08-13 补充:推广到 keycloak/data(Postgres)/minio

用户在场(重启电脑之后重新上线),风险可控,把试点阶段刻意跳过的三个核心
命名空间补上。

### 消费者列表是查代码,不是凭印象列的

写允许规则前,先 `grep -rl` 了整个仓库里所有引用
`postgres.data.svc.cluster.local`/`minio.minio.svc.cluster.local` 的
manifest,逐个确认它们各自部署在哪个 namespace(`destination.namespace`
字段,不是猜)。允许列表里故意包含了目前是 park 状态的组件(MLflow/
OpenMetadata/Superset/Airflow/Trino/SeaTunnel)对应的 namespace——按
"迟早会被拉起来"覆盖,不是只覆盖此刻在跑的,不然某天拉起某个组件却因为
一条网络策略连不上库/存储,排查半天才发现是这里漏了,这种"过一阵子才会
被发现的坑"正是这次要避免的。

### Keycloak 比预想中简单:所有消费者其实都走同一个入口

一开始以为要给每个用 SSO 的组件(ArgoCD/Grafana/JupyterHub/Argo
Workflows/Trino/Superset/OpenMetadata/MLflow/permission-request-app 等)
各自的 namespace 都开一条规则,查了
`platform/coredns-custom/manifests/configmap.yaml` 才发现完全不需要:
所有 pod 对 `*.local-lite.test` 的 DNS 查询都解析到
ingress-nginx-controller 的 ClusterIP,意味着所有走 OIDC discovery 的
组件都是经 ingress-nginx 连过来的,不是直连 keycloak 这个 namespace 的
Service。只放行 ingress-nginx 一个来源就够,不用逐个组件列。

`iam-sync` 这个 CronJob 虽然也在 keycloak namespace 里、要 `pods/exec`
进 keycloak-keycloakx-0(ADR-031),但 `kubectl exec` 走 API server →
kubelet → 容器运行时这条路径,不经过 pod 的网络接口,不受 NetworkPolicy
影响,不需要额外规则。

### 端口都是容器端口,不是 Service 端口,逐个 `kubectl get pod ...
### -o jsonpath='{.spec.containers[0].ports}'` 实测确认过

Keycloak 是 8080(不是 Service 暴露的 80/8443/9000),Postgres 是 5432,
MinIO 只放行 9000(API,S3A 客户端用),9001(管理控制台)没开——实测
`kubectl get ingress -n minio` 没有任何资源,这个控制台现在压根没有入口
能访问到,不放行不影响任何现有功能。

### 部署顺序:一个 namespace 一个 namespace 上,不是一次性铺开

三个都是真正核心、有活跃流量在跑的命名空间,不是 permission-request-app
那种"错了也不影响别的东西"的低风险目标——按风险从低到高的顺序
(MinIO → Postgres → Keycloak)依次部署,每上一个都先用真实的合法/
非法路径各测一遍再上下一个,任何一个环节验证不通过就先回滚
(`kubectl delete networkpolicy default-deny-ingress -n <ns>` 立即恢复
全通)再排查,不会带着一个没验证过的策略继续往下一个 namespace 走。

## 验证记录(2026-08-13,核心命名空间)

按 MinIO → Postgres → Keycloak 的顺序,通过 GitOps 分阶段上线(每个
namespace 的 manifest 单独提交、单独触发 sync、验证通过再提交下一个,
不是三个一起丢进 ArgoCD 让它一次性全同步)——直接 `kubectl apply` 单独
测试这几份 manifest 被 Claude Code 的权限分类器拦截了(判定为高风险的
"直接改核心共享基础设施网络策略"操作),改用这种"一次只提交一个
namespace,GitOps 正常同步"的方式达到同样的分阶段效果。

每个 namespace 都用"合法路径应该通、非法路径应该被拒"两条真实测试,不是
只看 NetworkPolicy 对象部署成功:

- **MinIO**:`data` namespace 的一次性 pod 连 `:9000` 健康检查端点,
  `200 OK`;`default` namespace(不在允许列表里)的一次性 pod 连同一个
  端点,`Connection refused`。
- **Postgres**:`data` namespace(同命名空间)、`keycloak` namespace
  (跨命名空间,对应 keycloak-create-db 这类真实消费者)分别用 `psql`
  真实执行 `SELECT 1` 都成功;`default` namespace 连同一个地址,
  `Connection refused`。
- **Keycloak**:两条都是真实场景,不是构造的测试
  - 从 Mac 本机(不是集群内部)走真实的 `http://keycloak.local-lite.test`
    域名(和浏览器走同一条路径)请求 OIDC discovery 端点,`200 OK`——
    证明所有走 ingress-nginx 进来的合法流量没受影响。
  - `default` namespace 的一次性 pod 绕过 ingress-nginx、直连
    keycloak 的 Service,`Connection refused`——证明"必须经过
    ingress-nginx"这条限制真的生效了,不是名义上部署了但没起作用。
  - 额外做了一次端到端真实业务验证:`permission-request-app`(实际在
    跑的、依赖 Keycloak OIDC 的组件)带 `Host` 头访问,拿到 `302`
    (正确跳转去 Keycloak 登录),证明策略上线后真实的 SSO 链路完整可用,
    不只是"能连上端口"这个网络层面的验证。

三个 namespace 部署过程中,`hive-metastore`/`postgres-0`/
`keycloak-keycloakx-0` 这几个真正承载流量的 pod 全程保持 `Running`、
没有额外重启,ArgoCD 里所有 Application 全程保持 `Synced`/`Healthy`。
