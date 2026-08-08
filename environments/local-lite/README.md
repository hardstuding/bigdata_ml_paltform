# local-lite

Phase 0(平台底座)目前直接把资源画像内嵌在 `platform/apps/*.yaml` 的
`helm.valuesObject` 里,还没有拆成独立的 values 覆盖文件。

等 Phase 1 引入 MinIO/Postgres/Hive Metastore/Iceberg,组件多起来之后,
再把 local-lite / cloud-full / prod 三份资源画像正式拆到这个目录下,
用 ArgoCD 的多 source(chart + values 文件)方式组装,不再把 values 焊在
Application manifest 里。现在组件少,先用简单的方式,不提前抽象。
