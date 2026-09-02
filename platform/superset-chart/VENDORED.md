# 这是 vendor 进来的第三方 chart,不要手改

| | |
|---|---|
| chart | `superset` |
| 版本 | `0.22.4` |
| 上游 | https://apache.github.io/superset |
| 打包文件 sha256 | `296c4cf42de738748fb92fe8d498ceffeb6f1f5d2ce6b7fcdb31d2aec0e69cd5` |
| vendor 时间 | 2026-09-02 |

**任何本地修改都会在下次升级时被覆盖**——目标目录是整个删掉重建的。要改
行为请改对应 ArgoCD Application 里的 `helm.valuesObject`,不要改 chart
模板本身。

升级:

```bash
./scripts/28-vendor-helm-chart.sh https://apache.github.io/superset superset <新版本> platform/superset-chart  
```

为什么要 vendor 而不是让 ArgoCD 直接拉:见
`scripts/28-vendor-helm-chart.sh` 顶部注释,以及
[ADR-061](../../docs/decisions/061-vendor-grafana-charts.md)。
