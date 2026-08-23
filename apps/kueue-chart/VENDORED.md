# 这是 vendor 进来的第三方 chart,不要手改

| | |
|---|---|
| chart | `kueue` |
| 版本 | `0.19.2` |
| 上游 | oci://registry.k8s.io/kueue/charts |
| 打包文件 sha256 | `bbd644b0fddd597d73d73c2d912a200a72a36f62c43d3a43c80585e3726a93c3` |
| vendor 时间 | 2026-08-23 |

**CRD 不在 `templates/` 里**,被摘到了 `crds-out-of-band/crds.yaml`,
需要用 `kubectl apply --server-side` 单独装(ArgoCD 装不了,CRD 超过
262144 字节的 annotation 上限)。原因见 `scripts/28-vendor-helm-chart.sh`
顶部 `--exclude-crds` 那段。

**任何本地修改都会在下次升级时被覆盖**——目标目录是整个删掉重建的。要改
行为请改对应 ArgoCD Application 里的 `helm.valuesObject`,不要改 chart
模板本身。

升级:

```bash
./scripts/28-vendor-helm-chart.sh oci://registry.k8s.io/kueue/charts kueue <新版本> apps/kueue-chart --exclude-crds kueue-system
```

为什么要 vendor 而不是让 ArgoCD 直接拉:见
`scripts/28-vendor-helm-chart.sh` 顶部注释,以及
[ADR-061](../../docs/decisions/061-vendor-grafana-charts.md)。
