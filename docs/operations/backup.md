# 备份与恢复

> 占位文档。local-lite 阶段不做备份(数据可重建/可丢弃),cloud-full 起需要补充下列内容。

## 需要备份的状态

- Postgres(各组件元数据:Hive Metastore、Airflow、Superset、Keycloak 等共用实例)
- MinIO 里的 Iceberg 表数据
- Keycloak realm 配置(用户、client、权限映射)

## 备份策略

(留空,Phase 1 引入 CloudNativePG 之后补充 PITR/定期快照方案)

## 恢复演练记录

(留空)
