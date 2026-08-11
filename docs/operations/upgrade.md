# 组件升级 / 当前版本清单

原则见 [ADR-005](../decisions/005-argocd-gitops.md):每个组件是独立
ArgoCD Application,升级一个不应牵动其他组件。版本必须显式锁定、可查、
可追溯这条规则见 [ADR-010](../decisions/010-optional-components-versioning.md)
(项目一开始就定了,这份文档是把这条规则真正落地)。

## 标准升级流程

1. 改对应 Application yaml 里的 `targetRevision`(chart 版本号)。
2. 本地跑 `python3 scripts/validate-charts.py` 确认 `helm template` 能正常
   渲染,不代表升级安全,只代表"chart 语法/我们的 values 没写错"。
3. push 到 Git,先在 `local-lite` 用真实功能验证(不是看 Pod 是不是
   `Running` 就算过,历史上好几次真实的坑——比如 Trino 的 livenessProbe
   端口打死、KServe CRD 太大——都是"渲染成功、Pod 起来了,但功能其实是坏
   的",见 [`troubleshooting.md`](troubleshooting.md))。
4. 验证通过后,把变更同步到 `cloud-full`/`prod` 对应的位置。
5. 把这次升级验证过的路径记到下面"已知升级路径"表里,不要让下一个人
   (人或 AI agent)重新摸索一遍。

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
| ingress-nginx | [ingress-nginx](https://kubernetes.github.io/ingress-nginx) | 4.15.1 | 启用 |
| jupyterhub | [jupyterhub](https://hub.jupyter.org/helm-chart/) | 4.4.1 | 启用 |
| kafka-operator(Strimzi) | [strimzi-kafka-operator](https://strimzi.io/charts/) | 1.1.0 | park(按需拉起) |
| keycloak | [keycloakx](https://codecentric.github.io/helm-charts) | 7.2.2 | 启用 |
| kserve-crd | kserve-crd(oci://ghcr.io/kserve/charts/kserve-crd) | v0.19.0 | 启用 |
| kserve-resources | kserve-resources(oci://ghcr.io/kserve/charts/kserve-resources) | v0.19.0 | 启用 |
| kube-prometheus-stack | [kube-prometheus-stack](https://prometheus-community.github.io/helm-charts) | 88.2.0 | 启用 |
| loki | [loki](https://grafana.github.io/helm-charts) | 7.2.0 | 启用 |
| minio | [minio](https://charts.min.io/) | 5.4.0 | 启用 |
| mlflow | mlflow(oci://ghcr.io/mlflow/charts/mlflow) | 0.1.0 | park(按需拉起) |
| oauth2-proxy(MLflow/Spark History Server 各一份) | [oauth2-proxy](https://oauth2-proxy.github.io/manifests) | 10.7.0 | park(按需拉起) |
| openmetadata | [openmetadata](https://helm.open-metadata.org/) | 1.13.3 | park(按需拉起) |
| opensearch | [opensearch](https://opensearch-project.github.io/helm-charts/) | 3.8.0 | park(按需拉起) |
| spark-operator | [spark-operator](https://kubeflow.github.io/spark-operator) | 2.5.2 | park(按需拉起) |
| superset | [superset](https://apache.github.io/superset) | 0.22.4 | park(按需拉起) |
| trino | [trino](https://trinodb.github.io/charts) | 1.42.2 | park(按需拉起) |

**这个仓库自己维护的部分**(没有独立版本号,`targetRevision: main` 就是
这个仓库当前的 git 状态本身——包括建库用的 `*-db-init` Job、`postgres`/
`hive-metastore`/`spark-history-server` 这几个没有官方 chart、自己写的裸
manifest、`coredns-custom`/`grafana-audit-dashboard`/
`cert-manager-issuers` 这类平台自己的配置):跟着这个仓库的 git 历史走,
`git log` 就是它们的"版本历史"。用到的具体镜像(比如 `postgres:16.6`、
`apache/spark:3.5.9`)固定写在各自的 manifest 里,见
[`scripts/list-project-images.py`](../../scripts/list-project-images.py)
的完整镜像清单。

## 已知升级路径

(留空,遇到真实升级并验证过再补,格式:`组件 X.Y → X.Z,验证日期,验证人/agent,注意事项`)
