# 这是 vendor 进来的第三方 chart,不要手改

| | |
|---|---|
| chart | `alloy` |
| 版本 | `1.11.1` |
| 上游 | https://grafana.github.io/helm-charts |
| 打包文件 sha256 | `cc4cd48a885c070fe8b2929971552852375c11e92d95fc6337d0c3fa277ac575` |
| vendor 时间 | 2026-08-22 |

**任何本地修改都会在下次升级时被覆盖**——目标目录是整个删掉重建的。要改
行为请改对应 ArgoCD Application 里的 `helm.valuesObject`,不要改 chart
模板本身。

升级:

```bash
./scripts/28-vendor-helm-chart.sh https://grafana.github.io/helm-charts alloy <新版本> platform/alloy-chart
```

为什么要 vendor 而不是让 ArgoCD 直接拉:见
`scripts/28-vendor-helm-chart.sh` 顶部注释,以及
[ADR-061](../../docs/decisions/061-vendor-grafana-charts.md)。
