# 组件升级 / 当前版本清单

原则见 [ADR-005](../decisions/005-argocd-gitops.md):每个组件是独立
ArgoCD Application,升级一个不应牵动其他组件。版本必须显式锁定、可查、
可追溯这条规则见 [ADR-010](../decisions/010-optional-components-versioning.md)
(项目一开始就定了,这份文档是把这条规则真正落地)。

## 升级一个组件

统一成六段:**触发条件 / 影响 / 前置检查 / 操作 / 验证 / 回滚**。

**触发条件**:上游发了新版本且我们需要它带的东西(修了我们踩的 bug、
解开了某个版本约束)。**不为了"跟上最新"而升** —— 每次升级都是一次真实
的风险。

**影响**:只影响这一个组件(每个组件是独立的 ArgoCD Application,
[ADR-005](../decisions/005-argocd-gitops.md))。**但要单独想一下版本
耦合** —— Iceberg 表格式那一层是所有引擎共用的,Spark/Flink/Trino 的
Iceberg 版本必须一起考虑(见下面 Spark 4 那条)。

**前置检查**:

1. 逐条核对上游的 breaking changes,`helm show values <chart> --version <旧>`
   和 `--version <新>` diff 一遍我们实际用到的键。
2. `python3 scripts/validate-charts.py` —— **它只证明 chart 语法和我们的
   values 没写错,不证明升级安全**。
3. 如果这个组件的 CRD 很大(kube-prometheus / CloudNativePG /
   argo-workflows / Kueue 这四个),CRD 要单独装,ArgoCD 装不了。

**操作**:改 `apps/components/<组件>.yaml` 里的 `targetRevision` →
重新渲染 → commit → push。**不要改 `apps/definitions/` 下的生成物。**

**验证** —— **判据必须是业务结果,不是 Pod 状态**:

这个平台好几次真实的坑都是"渲染成功、Pod Running、ArgoCD 绿,但功能是坏
的"(Trino 的 livenessProbe 端口打死、KServe CRD 太大、OpenMetadata 连不上
OpenSearch)。所以:

```bash
./scripts/46-verify-p15.sh          # 产品层功能回归
kubectl -n monitoring get pods --sort-by=.metadata.creationTimestamp | grep goldenpath
```

**回滚**:把 `targetRevision` 改回去、重新渲染、push。

> **回滚不总是可行** —— 数据库 schema 迁移过的组件(OpenMetadata、
> Keycloak、Airflow)升级时会改表结构,降版本可能起不来。这类组件
> **升级前先确认当天的 Postgres 备份是好的**(见
> [`backup.md`](backup.md)),那才是真正的退路。

最后:**把这次验证过的路径记进下面那张表**,不要让下一个人(人或 AI)
重新摸索一遍。

## 当前版本清单

跑 `python3 scripts/list-component-versions.py` 生成最新版本,这里贴的是
生成时的快照,可能已经过时——**遇到版本问题以脚本实际跑出来的结果为准,
不要相信这份文档本身没更新过的旧数字**。

**标准开源组件**(官方 chart/镜像,我们只是配了 values,组件本身的文档/
issue/升级指南去官方仓库找,不在这个项目里重复维护):

<!-- 由 scripts/list-component-versions.py 生成,过滤掉了这个仓库自己维护
     的裸 manifest(下面单独列) -->
| 组件 | chart / 来源 | 版本 | 状态 |
|---|---|---|---|
| airflow | [airflow](https://airflow.apache.org) | 1.22.0 | park(按需拉起) |
| alloy | [alloy](https://grafana.github.io/helm-charts) | 1.11.1 | 启用 |
| argo-workflows | [argo-workflows](https://argoproj.github.io/argo-helm) | 1.0.24 | 启用 |
| cert-manager | [cert-manager](https://charts.jetstack.io) | v1.21.1 | 启用 |
| cloudnative-pg-operator | [cloudnative-pg](https://cloudnative-pg.github.io/charts) | 0.29.0 | 启用(2026-08-13 起共享 Postgres 迁移到这个 operator 管理,见 ADR-038) |
| ingress-nginx | [ingress-nginx](https://kubernetes.github.io/ingress-nginx) | 4.15.1 | 启用 |
| jupyterhub | [jupyterhub](https://hub.jupyter.org/helm-chart/) | 4.4.1 | 启用 |
| kafka-operator(Strimzi) | [strimzi-kafka-operator](https://strimzi.io/charts/) | 1.1.0 | park(按需拉起) |
| keycloak | [keycloakx](https://codecentric.github.io/helm-charts) | 7.2.2 | 启用 |
| kserve-crd | kserve-crd(oci://ghcr.io/kserve/charts/kserve-crd) | v0.19.0 | 启用 |
| kserve-resources | kserve-resources(oci://ghcr.io/kserve/charts/kserve-resources) | v0.19.0 | 启用 |
| kube-prometheus-stack | [kube-prometheus-stack](https://prometheus-community.github.io/helm-charts) | 88.2.0 | 启用(含 Alertmanager,2026-08-12 起打开,见 ADR-034) |
| loki | [loki](https://grafana.github.io/helm-charts) | 7.2.0 | 启用 |
| minio | [minio](https://charts.min.io/) | 5.4.0 | 启用 |
| mlflow | mlflow(oci://ghcr.io/mlflow/charts/mlflow) | 0.1.0 | park(按需拉起) |
| oauth2-proxy(MLflow/permission-request-app/Spark History Server 各一份) | [oauth2-proxy](https://oauth2-proxy.github.io/manifests) | 10.7.0 | permission-request-app 那份启用,其余 park(按需拉起) |
| openmetadata | [openmetadata](https://helm.open-metadata.org/) | 1.13.3 | park(按需拉起) |
| opensearch | [opensearch](https://opensearch-project.github.io/helm-charts/) | 3.8.0 | park(按需拉起) |
| spark-operator | [spark-operator](https://kubeflow.github.io/spark-operator) | 2.5.2 | 启用 |
| superset | [superset](https://apache.github.io/superset) | 0.22.4 | park(按需拉起) |
| trino | [trino](https://trinodb.github.io/charts) | 1.42.2 | park(按需拉起) |

**这个仓库自己维护的部分**(没有独立版本号,`targetRevision: main` 就是
这个仓库当前的 git 状态本身——包括建库用的 `*-db-init` Job、`postgres`/
`hive-metastore`/`spark-history-server`/`permission-request-app` 这几个
没有官方 chart、自己写的裸 manifest、`coredns-custom`/
`grafana-audit-dashboard`/`cert-manager-issuers`/`network-policies`/
`postgres-backup` 这类平台自己的配置):跟着这个仓库的 git 历史走,
`git log` 就是它们的"版本历史"。用到的具体镜像(比如 `postgres:16.6`、
`apache/spark:3.5.9`)固定写在各自的 manifest 里,见
[`scripts/list-project-images.py`](../../scripts/list-project-images.py)
的完整镜像清单。

## 已知升级路径

| 组件 | 版本 | 日期 | 注意事项 |
|---|---|---|---|
| OpenMetadata | 1.13.3 → **2.0.0** | 2026-08-26 | 大版本,**GA 才两天就升的**,所以逐条核对了 breaking changes([ADR-072](../decisions/072-openmetadata-2-upgrade.md))。`targetRevision` 和 `ingestionImage` 两处都要改(只改一处的话采集容器还是旧版)。验证判据是**目录里的表还在不在、采集还能不能跑出新结果**,不是 Pod 状态 |
| Spark | 3.5.9 → **4.1.3** | 2026-08-29 | **和 Iceberg 1.10.0 → 1.11.0 一起升**,不能分开:Spark 3.5.9 是 Java 11/Scala 2.12,Iceberg 只能停在 1.10.0;Spark 4 换到 Java 17/Scala 2.13 才解得开这个结([ADR-076](../decisions/076-spark-4-evaluation.md))。**所有引擎读写同一份 Iceberg 表格式,版本必须统一** —— 升 Spark 就要同时想 Flink 和 Trino 那边。验证:`SPARK_ICEBERG_DEMO_OK` |
| Iceberg | 1.10.0 → **1.11.0** | 2026-08-29 | 同上,跟着 Spark 4 一起 |

**格式**:`组件 X.Y → X.Z,日期,注意事项`。注意事项那栏写"下一个人不知道
就会踩的东西",不是复述 changelog。
