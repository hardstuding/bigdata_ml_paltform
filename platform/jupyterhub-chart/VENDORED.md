# 这是 vendor 进来的第三方 chart,不要手改

| | |
|---|---|
| chart | `jupyterhub` |
| 版本 | `4.4.2` |
| 上游 | https://hub.jupyter.org/helm-chart/ |
| 打包文件 sha256 | `0ddfa517050d16f2e1b50c62b5b6377b12574f9a2b88f3b352bd211ef3173c3e` |
| vendor 时间 | 2026-09-02 |

**任何本地修改都会在下次升级时被覆盖**——目标目录是整个删掉重建的。要改
行为请改对应 ArgoCD Application 里的 `helm.valuesObject`,不要改 chart
模板本身。

升级:

```bash
./scripts/28-vendor-helm-chart.sh https://hub.jupyter.org/helm-chart/ jupyterhub <新版本> platform/jupyterhub-chart  
```

为什么要 vendor 而不是让 ArgoCD 直接拉:见
`scripts/28-vendor-helm-chart.sh` 顶部注释,以及
[ADR-061](../../docs/decisions/061-vendor-grafana-charts.md)。
