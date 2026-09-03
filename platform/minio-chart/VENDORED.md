# 这是 vendor 进来的第三方 chart,不要手改

| | |
|---|---|
| chart | `minio` |
| 版本 | `5.4.0` |
| 上游 | https://charts.min.io/ |
| 打包文件 sha256 | `25fa2740480d1ebc9e64340854a6c42d3a7bc39c2a77378da91b21f144faa9af` |
| vendor 时间 | 2026-09-03 |

**任何本地修改都会在下次升级时被覆盖**——目标目录是整个删掉重建的。要改
行为请改对应 ArgoCD Application 里的 `helm.valuesObject`,不要改 chart
模板本身。

升级:

```bash
./scripts/28-vendor-helm-chart.sh https://charts.min.io/ minio <新版本> platform/minio-chart  
```

为什么要 vendor 而不是让 ArgoCD 直接拉:见
`scripts/28-vendor-helm-chart.sh` 顶部注释,以及
[ADR-061](../../docs/decisions/061-vendor-grafana-charts.md)。
