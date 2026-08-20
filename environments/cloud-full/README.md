# cloud-full 环境画像

## 现状:和 ADR-004 最初设想的不完全一样,这里先说清楚

[ADR-004](../../docs/decisions/004-environment-profiles.md) 最初设想的
机制是"三个 profile 共用同一套 chart,只通过 `environments/<profile>/
values.yaml` 覆盖开哪些组件、给多少资源"。**这个 values.yaml 覆盖机制
实际上从来没有真的搭起来**——每个组件是独立的 ArgoCD Application(见
[ADR-005](../../docs/decisions/005-argocd-gitops.md)),resources 直接
写死在 `apps/definitions/*.yaml` 里,不是从外部 values 文件注入的。

真正跑起来的机制(2026-08-20 起,ADR-057 第三批)是
`environments/<env>/config.yaml` 里的 `enabled_components` 列表:
`apps/components/` 是全部 43 个组件定义的唯一源码,某个环境要不要某个
组件,是这份列表里有没有对应文件名,`python3 scripts/
render-environment-config.py <env>` 把列表里的组件渲染进
`apps/definitions/`(`apps-root` 这个 ArgoCD ApplicationSet 扫的就是
这个目录)。以前(2026-08-20 之前)靠 `git mv` 组件文件在
`apps/definitions/` 和 `environments/cloud-full/pending-definitions/`
之间搬来搬去表达"这个环境要不要这个组件"的机制已经退役,`pending-
definitions/` 目录已删除。这套新机制**解决了"哪些组件要开"这个问题,
但没有解决"同一个组件在不同环境给多少资源"这个问题**——`apps/
components/` 里每个组件写的 resources 值就是 local-lite 的降配值,
搬到真正的 cloud-full 硬件上不改直接用会明显浪费资源,也发挥不出硬件的
性能。

**这份文档是当前阶段务实的补丁**:不是一份能直接 `kubectl apply` 生效
的 values 文件,是一份"接入 cloud-full 硬件时,该做哪些事、每个组件
大概该给多少资源"的参考清单。真正做到"改一个 values 文件就切换环境"
需要把每个 Application 的 resources 字段抽成可覆盖的参数(Helm values
或者 Kustomize overlay),这是一个横跨全部 ~30 个组件定义的重构,工作量
不小,建议真正要接入 cloud-full 硬件、有实际动机验证这套机制时再做,
不要为了"看起来更规范"而现在就动这个重构(见项目一贯的"不做没法验证
的东西"原则——没有真实的 cloud-full 硬件,重构完也没法真的验证对不对)。

## 目标画像

参考 [`docs/architecture.md`](../../docs/architecture.md) 的环境画像
定义:公有云或公司 IDC 机房,建议 ≥32GB 内存(对比 local-lite 这台
10GB/4vCPU 的 colima 虚拟机)。目标是功能完整的开发与集成验证环境,
local-lite 全部 + Trino/Superset/OpenMetadata + Airflow/SeaTunnel/
Spark Operator/Kafka + JupyterHub/MLflow/Argo Workflows/KServe 全部
同时常驻,不再是"验证完就 park 回去"这套本机专用的取舍。

## 接入步骤

1. 确认 `environments/cloud-full/config.yaml` 的 `enabled_components`
   列表包含要接入的组件(现状:cloud-full 已经是全部 43 个组件),跑
   `python3 scripts/render-environment-config.py cloud-full` 生成
   `apps/definitions/`,一次性把所有组件收进 GitOps 管理范围。
2. 按下面"资源建议"这一节,把每个组件 Application yaml 里的
   `resources.requests/limits` 从 local-lite 的降配值调大——这一步
   目前是手工改,没有自动化(见上面"现状"那段的说明)。
3. `scripts/00-generate-secrets.sh`(幂等,已有的 Secret 不受影响,
   新组件的 Secret 会补齐)。
4. 走一遍 README 的"从零拉起整套服务"流程(如果是全新集群),或者
   ArgoCD 自动同步(如果是在已有集群上追加组件)。

## 资源建议(这台 local-lite 机器实测数据推算,不是精确容量规划)

下面是 local-lite 阶段每个组件实测的 `resources.requests`(部分组件
还有 `limits`,数字来自各自 Application yaml,不是估的),按层分组。
cloud-full 建议整体 **请求值 × 3、限制值 × 2** 起步(不是精确公式,是
"local-lite 是刻意压到能跑就行的下限,cloud-full 要留出真实并发/多用户
使用的余量"这个方向性判断)——具体到某个组件要不要给更多,等真的有
多用户实际使用之后再按 `kubectl top pods` 的真实数据回调,不要一次性
过度分配。

| 组件 | local-lite requests | cloud-full 起步建议 |
|---|---|---|
| Keycloak | 200m / 512Mi | 600m / 1.5Gi |
| Postgres(CloudNativePG) | 100m / 256Mi | 500m / 2Gi(单实例;要不要多副本见下面"HA"一节) |
| Hive Metastore | 150m / 512Mi | 300m / 1Gi |
| MinIO | 100m / 256Mi | 500m / 2Gi(对象存储量上来之后内存需求会明显增长) |
| kube-prometheus-stack(Prometheus+Grafana+Alertmanager 等合计) | ~225m / ~800Mi | 1 core / 3Gi(retention 也建议从 local-lite 的 6h 拉长,见 `platform/apps/kube-prometheus-stack.yaml` 的 `prometheus.prometheusSpec.retention`) |
| Argo Workflows | 100m / 256Mi | 300m / 768Mi |
| JupyterHub(hub 本身,不含 singleuser) | 100m / 256Mi | 300m / 768Mi;singleuser 每人独立配额,按团队规模乘 |
| Spark Operator | 100m / 256Mi | 200m / 512Mi(实际 Spark 作业的 driver/executor 资源另算,不在这张表里) |
| Trino(coordinator) | 300m / 1Gi | 1 core / 4Gi;cloud-full 起建议真的拆出独立 worker(local-lite 是 `server.workers: 0` 单节点凑合) |
| Superset | 200m / 512Mi | 500m / 1.5Gi |
| OpenMetadata | 300m / 1024Mi | 1 core / 3Gi |
| OpenSearch(OpenMetadata 的搜索后端) | 300m / 768Mi | 1 core / 3Gi;`persistence.size` 也要从 local-lite 的 5Gi 调大 |
| Airflow(webserver+scheduler+dagProcessor+triggerer 合计) | ~600m / ~2Gi | 1.5 core / 5Gi 起步,任务本身走 KubernetesExecutor 单独起 pod,不在这个常驻资源里 |
| Kafka(operator + broker) | 100m / 256Mi(仅 operator,broker 另算) | broker 至少 1 core / 4Gi 起步(local-lite 只验证过单节点 KRaft,cloud-full 建议评估要不要多 broker) |
| MLflow | 200m / 768Mi | 500m / 1.5Gi |

## 关于"弹性"

`docs/decisions/040-enterprise-governance-roadmap.md` 记录过用户对
"队列资源管理需要保证弹性"这个诉求。cloud-full 阶段如果用的是公有云
托管 k8s(比如带 cluster-autoscaler 的节点池),这里可以真正开始做
HPA(水平自动扩缩容)和节点池自动扩容——这是 local-lite 单节点机器
完全做不到的东西。具体怎么做留到真的有 cloud-full 硬件时再展开,这里
先记一笔"这是 cloud-full 阶段才有意义讨论的话题",不在这份模板里假装
写出一套没有真实环境验证过的 HPA 配置。
