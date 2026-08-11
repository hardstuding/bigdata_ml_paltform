# 029. Spark 权限 + 可观测性(YARN 的替代方案)

- 状态: 已采纳,**配置就绪但未验证**(Spark Operator 本身还在
  `pending-definitions/` park 状态,没有真实作业跑过,这次没法像其他组件
  一样用真实数据验证,留到 Spark Operator 真正启用时一起测)

## 背景

用户之前的 CDH 平台上,YARN ResourceManager UI 能看所有队列/所有人的作业,
点进去还能看具体 Spark 执行的 Stage/Task 明细。这个 k8s-native 架构没有
YARN,原生 Spark-on-k8s(spark-operator)也没有一个一一对应的东西,需要拼
出等价能力。同时 Spark 本身目前完全没有鉴权——UI 默认公开,任何能访问集群
内网的人都能看到作业细节。

## 决策

拆成三部分,对应 YARN 提供的三种能力:

### 1. 单个作业的实时 UI(4040 端口)—— 不对外暴露

Spark 作业跑的时候,driver pod 会开一个 4040 端口的 Web UI。这个不像别的
组件一样配 Ingress+SSO——它是每个作业动态起的,没有固定地址,而且只有
作业在跑的时候才存在,给它单独接 SSO 的性价比不高。提交作业的人自己
`kubectl port-forward` 看,和训练任务直连 MinIO/MLflow(ADR-023)是同一个
"谁的东西谁直接连,不额外包一层"的思路。

### 2. 作业结束后还能查(等价于 YARN 点进具体作业看明细)—— Spark History Server

Spark 官方组件,但**没有官方 Helm chart**(Spark 项目本身不维护),所以
写成裸 manifest(`apps/spark-history-server/manifests/`),和
`platform/coredns-custom/`、`platform/grafana-audit-dashboard/` 是同一类
"没有官方 chart 时怎么办"的处理方式。镜像用 Apache 官方发布的
`apache/spark:3.5.9`,不是社区/第三方构建(和 ADR-008 拒绝 Bitnami 的
筛选标准一致)。

前面挂 oauth2-proxy 做 SSO,和 MLflow(ADR-019)是同一个模式——Spark
History Server 本身也没有原生 OIDC。

作业要把 eventLog 写到 `s3://spark-logs/`(MinIO 里新建的 bucket,声明式
配在 `apps/definitions/minio.yaml` 的 `buckets` 列表里)才能被 History
Server 读到,这不是全局能强制的配置(spark-operator chart 没有"所有作业
默认加这些 sparkConf"的机制),每次提交作业时要在
`SparkApplication.spec.sparkConf` 里带上,具体清单写在
`environments/cloud-full/pending-definitions/spark-operator.yaml` 的注释
里,方便以后抄。

### 3. "现在所有人在跑什么"的实时全局视图(等价于 YARN RM 首页)—— Grafana 面板(未建)

spark-operator chart 默认就会暴露 Prometheus 指标
(`prometheus.metrics.enable` 默认 true),这次额外开了
`prometheus.podMonitor.create: true` 让 kube-prometheus-stack 的
Prometheus Operator 真的去抓。**没有实际去建 Grafana 面板**——没有真实
作业在跑,面板查询语句写出来也没法验证对不对,和这个项目一贯的"不做没法
验证的东西"原则一致(ADR-024 的审计看板是先有真实数据流入 Loki 才建的
面板,不是反过来)。留到 Spark Operator 真正启用、有真实指标产生时再建。

## 2026-08-12 补充

Spark History Server 这份 oauth2-proxy 还没被真实拉起来验证过(Spark
Operator 本身还是 park 状态),但共用的 `scripts/00-generate-secrets.sh`
里生成 `cookie-secret` 那段代码有一个之前没发现的长度 bug(`openssl rand
-base64 32` 编码后是 44 字符,oauth2-proxy 要求原始字符串长度必须是
16/24/32),已经在 ADR-019 的更正里改成 `openssl rand -base64 24` 并说明
了根因——这个组件目前还没建到这份 Secret(namespace 不存在),不需要
额外的热修复,以后第一次生成时就会是对的。

## 顺带修的东西

- `scripts/validate-charts.py` 新增 `KNOWN_CLUSTER_API_VERSIONS`
  常量:`helm template` 离线渲染时不知道目标集群装了哪些 CRD,
  spark-operator 的 PodMonitor 模板对这种情况是直接 `fail`,不是优雅跳过。
  真实集群里 `monitoring.coreos.com/v1` 这个 CRD 是有的(kube-prometheus-
  stack 装的,这个仓库另一个 Application 负责),只是单独渲染 spark-
  operator 这个 chart 时"看不到"别的 Application 装了什么。补一份"最终会
  在同一个集群里"的 CRD 清单让离线校验更贴近真实部署目标。**踩了一个格式
  坑**:`--api-versions monitoring.coreos.com/v1` 这种粗粒度写法不够,这个
  chart 的判断条件写的是 `group/version/Kind` 完整格式
  (`monitoring.coreos.com/v1/PodMonitor`),两种格式都得给全。

## 后果

- Spark 的鉴权目前只覆盖到"History Server 这个汇总视图有 SSO",单个作业
  的实时 UI 仍然是任何能访问集群内网的人都能 `port-forward` 看到——这不算
  疏漏,是刻意的取舍(见上面第 1 条),但如果以后有更严格的合规要求
  (比如要求所有 Spark UI 访问都留痕),需要重新评估。
- Hive Metastore 目前也完全没有鉴权(查过,没有 auth/kerberos 相关配置),
  这次没有一起处理,是另一块待补的空白,不在这次范围内。
