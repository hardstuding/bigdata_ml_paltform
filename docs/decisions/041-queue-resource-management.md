# 041. 队列资源管理:ResourceQuota + LimitRange + PriorityClass

- 状态: 已采纳,已验证

## 背景

[ADR-040](040-enterprise-governance-roadmap.md) 归档的企业治理需求
清单里,"资源隔离"是唯一一条不依赖其他条目、可以现在就单独做完的——
多个角色(大数据/算法/数分)共用同一个平台时,需要防止一个组件/一个人
的作业把资源占满,影响其他人。2026-08-08 真实发生过一次事故:同时
部署 Kafka/Spark Operator/Airflow 把这台机器 6GB 内存打满,触发
kernel OOM,连 `argocd-application-controller` 都被连带杀掉,靠重启
colima 才恢复——这不是假设性风险。

## 决策

k8s 原生的 `ResourceQuota` + `LimitRange` + `PriorityClass` 三件套,
不引入额外工具(Trino 自己的 resource group、YARN capacity scheduler
那类专门的调度器队列,local-lite 单机场景下用不上,见"后果"部分)。

### ResourceQuota:每个共享命名空间一个保护性上限

覆盖 `trino`/`jupyterhub`/`superset`/`openmetadata`/`airflow`/
`spark-operator`/`mlflow` 这几个多人共用的命名空间,只限制
`requests.cpu`/`requests.memory`/`limits.memory`。**这是保护性上限,
不是精确的容量分配承诺**——这台机器总共只有 4 vCPU/10Gi 内存,下面每
个命名空间的配额单独看都合理,但全部同时打满会远超机器总容量,这是
刻意的(这个项目一贯是"验证一个组件、park 回去",不是所有组件同时
常驻满载,见 `environments/cloud-full/pending-definitions/README.md`)。
要防的是"一个组件本身失控增长拖垮整台机器",不是要在多个组件之间做
精确仲裁。

### LimitRange:配额落地的必要配套,不是可选项

k8s 的行为是:一旦命名空间里有 `ResourceQuota` 管了 CPU/内存,这个
命名空间里**所有**新建 pod 的**所有**容器都必须显式声明 `requests`,
没有例外,少一个就整个 pod 创建被拒绝。部署前实测检查过
(`kubectl get pods -o json` 扫一遍每个容器的 `resources.requests`)
确认这台机器上不是所有容器都做到了这一点:JupyterHub 的
`continuous-image-puller`(提前把 singleuser 镜像拉到节点,不是用户
会话本身)完全没配 `resources.requests`——这类容器现在能正常跑是因为
创建时命名空间里还没有配额,一旦上线 ResourceQuota,它下次被重建
(节点重启、DaemonSet 更新)时会直接被拒绝创建。与其去审计、修改每个
组件各自的 chart values(不可能穷尽,以后新增组件也可能重蹈覆辙),
给每个命名空间配一个 `LimitRange` 提供默认值(容器没写 requests 时
自动补上,写了的话以容器自己的为准)——这是 ResourceQuota 落地的标准
配套,不是专门为这一个组件发明的变通方案。

### PriorityClass:资源紧张时"优雅降级"而不是随机杀

三档:`platform-critical`(1000,Keycloak/Postgres/MinIO/ArgoCD/
ingress-nginx/cert-manager 这类平台底座)、`interactive`(500,Trino/
JupyterHub/Superset/OpenMetadata,用户在直接等结果)、`batch`(100,
Airflow/Spark Operator/Kafka/SeaTunnel/MLflow 训练任务,能容忍被抢占
重跑)。没有显式指定的 pod 默认优先级是 0,比 `batch` 还低——刻意的,
倒逼新增组件明确想清楚自己该归哪一档。

**这是"队列资源管理需要保证弹性"这条诉求在单节点场景下唯一诚实能
做到的等价物**:没有多节点、没有 cluster-autoscaler,做不到真正的
弹性扩容,能做的是资源紧张时优雅降级(先牺牲 batch 类负载),不是
弹性扩容。cloud-full/prod 阶段接入真实多节点集群、有 cluster-
autoscaler 之后,这几档优先级语义依然适用,届时再叠加真正的弹性
扩容能力。

## 后果

- **`priorityClassName` 的实际覆盖情况,按组件类型分两半**:
  - **裸 manifest / CRD 原生支持的组件已经加上了**:Postgres(CNPG
    的 `Cluster` CRD 原生支持这个字段,`kubectl explain
    cluster.spec.priorityClassName` 确认过)、Hive Metastore、
    `permission-request-app`、`postgres-backup`/`iam-sync` 这两个
    CronJob——这几个我直接控制完整的 pod spec,加一行字段的事。
  - **Helm chart 管理的组件目前做不到,不是漏做,是查证过确实没有
    现成的路径**:实测检查过 `codecentric/keycloakx`、`minio/minio`、
    `trinodb/trino`、`open-metadata/openmetadata`、`apache/airflow`、
    `kubeflow/spark-operator`、`apache/superset` 这几个 chart 的
    `helm show values`,**都没有暴露 `priorityClassName`(或者
    `podSpec`/`extraPodSpec` 这类能间接塞进去的字段)**——不是这次
    没查,是查了确实没有。要接这几个组件,需要 Helm `postRenderers`
    或者 ArgoCD 的 Kustomize patch 机制在渲染后二次改写 pod spec,
    这类"绕过 chart 本身限制"的方案有实际的维护成本(chart 升级后
    patch 可能失效,需要人回头确认),这次判断不值得为了这一项相对
    次要的收益(PriorityClass 只在节点资源紧张、真的要驱逐 pod 时才
    起作用,不是日常路径)引入这层复杂度,留着不做,不是遗漏。
  - 换句话说:**平台底座(Postgres/Hive Metastore)和这次新加的
    `permission-request-app` 已经受保护,真正会抢占资源的"消费型"
    组件(Trino/JupyterHub/Superset/OpenMetadata/Airflow/Spark
    Operator/Kafka/MLflow)目前还是默认优先级(0,比 batch 还低)**——
    ResourceQuota/LimitRange 已经在生效,这些组件本身不会失控增长,
    只是"节点整体资源紧张时谁先被驱逐"这一层保护还没覆盖到它们。
    如果以后真的要补这一块,从 Helm `postRenderers` 或者
    Kustomize patch 这个方向展开。
- 没有引入 Trino 自己的 resource group 或者其他专门的调度器队列
  ——local-lite 单机、组件轮流验证的使用模式下,k8s 原生这三件套已经
  够用,更专门的调度器队列留到真的有多用户并发访问 cloud-full/prod
  时再评估要不要加。
- `kafka`/`seatunnel`/`opensearch` 这几个当前是 park 状态的命名空间
  还不存在,配额没有覆盖到——它们被 un-park 时需要补一份同款的
  ResourceQuota/LimitRange,不然会变成没有保护的空档。
- ResourceQuota 的具体数值是按这台机器当前的资源画像估算的,不是精确
  容量规划的结果,cloud-full/prod 阶段需要按真实硬件和真实并发用户数
  重新核算,见 `environments/cloud-full/README.md`。

## 验证记录

2026-08-14:`kubectl apply` 之后确认 ResourceQuota/LimitRange 在全部
7 个命名空间创建成功;故意在 `trino` 命名空间起一个请求量超过配额的
测试 pod,确认被 admission 拒绝、报错信息是预期的 `exceeded quota`;
确认 `jupyterhub` 命名空间原有的 pod(包括没有独立配 requests 的
`continuous-image-puller`)在配额上线后重新创建时,靠 LimitRange 补上
默认值,没有被拒绝创建。测试用的临时 pod 已清理。
