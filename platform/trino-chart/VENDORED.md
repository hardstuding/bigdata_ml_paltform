# 这是 vendor 进来的第三方 chart,不要手改

| | |
|---|---|
| chart | `trino` |
| 版本 | `1.42.2` |
| 上游 | https://trinodb.github.io/charts |
| 打包文件 sha256 | `0b33da82a36becd8913fef23d99f470bb6b87bce29bbc219089a5bbd4159266d` |
| vendor 时间 | 2026-09-03 |

**任何本地修改都会在下次升级时被覆盖**——目标目录是整个删掉重建的。要改
行为请改对应 ArgoCD Application 里的 `helm.valuesObject`,不要改 chart
模板本身。

升级:

```bash
./scripts/28-vendor-helm-chart.sh https://trinodb.github.io/charts trino <新版本> platform/trino-chart  
```

为什么要 vendor 而不是让 ArgoCD 直接拉:见
`scripts/28-vendor-helm-chart.sh` 顶部注释,以及
[ADR-061](../../docs/decisions/061-vendor-grafana-charts.md)。
