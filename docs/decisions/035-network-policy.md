# 035. NetworkPolicy 试点(permission-request-app namespace)

- 状态: 已采纳,试点已验证(2026-08-12),**只覆盖一个 namespace,不是全量**

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

用真实的裸 pod(不是这个 namespace 里的正式组件,避免验证过程本身影响
生产流量)确认了 NetworkPolicy 在这个集群上真的会拦截未授权连接,
`default-deny-ingress` 生效后连接会超时/被拒绝,加了明确的 `allow` 规则
之后对应的连接能重新连通。

## 后果

- **`keycloak`、`data`(Postgres)、`minio` 这些真正核心、被好几个组件
  共用的 namespace 现在还没有 NetworkPolicy**——这是刻意的,不是漏做。
  这些 namespace 的入站流量来源比 permission-request-app 复杂得多(好几个
  组件都要连 Postgres/Keycloak),策略写起来风险更高,应该在有人盯着、
  能随时回滚的时候单独做,不适合在这次深夜作业里顺手做掉。
- egress 完全没做,任何 pod(包括这次上了 NetworkPolicy 的
  permission-request-app)出网还是不受限制。
- 没有做团队/namespace 之间的隔离(比如"data-analysts 用的 namespace 不能
  连 algorithm-team 的 namespace")——现在还没有真正意义上按团队拆分
  namespace 的实践,这条要等真的有多团队各自独立部署工作负载时才有意义。
