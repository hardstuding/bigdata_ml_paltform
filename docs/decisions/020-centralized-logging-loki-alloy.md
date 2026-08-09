# 020. 集中日志:Loki(SingleBinary)+ Grafana Alloy,不用 Promtail

- 状态: 已采纳(2026-08-09,已验证:8 个命名空间的日志真实进了 Loki,Grafana 数据源可查)

## 背景

这次调试 Ingress/SSO/CoreDNS 的过程里,排障手段全靠一个个 `kubectl logs`
(经常被本机代理拦截,见 troubleshooting.md)或者 `crictl logs`(要先找容器
ID,一次只能看一个)。没有集中日志是这次整理架构缺口时明确提出来要补的一项,
现在着手做。

## 决策

- **Loki 存储**:官方 `grafana/loki` chart,`deploymentMode: SingleBinary`,
  `storage.type: filesystem`(不用 SimpleScalable/Distributed——那两种模式
  要求对象存储,local-lite 单节点用不上这份复杂度)。`loki.useTestSchema:
  true` 用 chart 自带的 schema 预设(v13/tsdb),不用手写 `schema_config`
  那一大段——这不是"仅供测试不能用"的意思,是"不需要自己管理存储生命周期"
  的简化预设,数据本身真实落盘,和 kube-prometheus-stack 里 Prometheus
  关掉持久化、缩短 retention 是同一类"local-lite 不追求生产级可靠性"的取舍。
  关掉了 chart 默认自带的两个 memcached(chunks-cache/results-cache)——
  默认给 chunks-cache 分配 8Gi 内存,这台机器的日志量级完全用不上,开着
  反而先把内存打爆。
- **采集用 Grafana Alloy,不用 Promtail**:Promtail 已经在 2026-03-02 EOL,
  官方明确建议迁移到 Alloy。这个时间点再新装一个已经停止维护的组件,等于
  一上线就是要迁移的技术债,没有理由这么做。
- **Alloy 用 hostPath 读日志文件,不用 `loki.source.kubernetes`**:后者是
  官方更推荐的新方式(通过 K8s API 拉日志,不需要 hostPath),但在这台机器
  上完全拉不到数据(`Internal Privoxy Error`,和 `kubectl logs` 同一个坑,
  见 troubleshooting.md)。改用传统方式:`discovery.kubernetes` 只拿 pod
  元数据做 relabel,`local.file_match` + `loki.source.file` 直接从 hostPath
  挂载的 `/var/log/pods` 读文件内容,完全绕开 API server -> kubelet 这条
  路径。这台机器上 `/var/log/pods/.../0.log` 是指向
  `/var/lib/docker/containers/...` 的符号链接(colima 这个 profile 用
  docker 的 json-file 日志驱动),所以 `alloy.mounts.varlog` 和
  `alloy.mounts.dockercontainers` 两个挂载都要开,只开一个会导致符号链接
  指向的路径在容器里够不着。
- **Grafana 加 Loki 数据源**(`additionalDataSources`):指标(Prometheus)
  和日志(Loki)能在同一个 Grafana 界面查,不用来回切换工具。

## 后果

- 这台机器的网络环境(本机代理拦截 `containerLogs` 这条路径)决定了
  hostPath 方案更适合这里,不代表 `loki.source.kubernetes` 本身有问题——
  cloud-full/IDC 网络环境正常的话,两种方式理论上都能用。但既然 hostPath
  方案本身也是更常见、更不依赖特定网络环境的做法,没有计划在网络环境变了
  之后切回 `loki.source.kubernetes`,除非 hostPath 方式暴露出别的问题。
- `useTestSchema: true` + 无持久化保证这套简化配置,和 Prometheus 一样是
  local-lite 专属的取舍——cloud-full/prod 需要重新评估,大概率要写正式的
  `schema_config`(tsdb 索引 + 真实的对象存储,可以复用现有的 MinIO 或者
  云上的对象存储服务),不能直接照搬这份配置。
- 目前没有配日志保留策略(retention)、没有告警规则(比如"某个组件突然
  大量报错"触发通知)——这些是"有了集中日志之后"自然的下一步,但不在这次
  范围内,先把"日志能查到"这个基础打通。
