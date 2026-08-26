# 015. OpenMetadata:Postgres 后端 + k8s 原生采集编排

- 状态: 已采纳(2026-08-09,已验证)

> **2026-08-26 后续**:OpenMetadata 已经从 1.13.3 升到 **2.0.0**
> ([ADR-072](072-openmetadata-2-upgrade.md))。这份 ADR 定的
> `pipelineServiceClientConfig.type: k8s`(不额外维护一个 Airflow 只为跑采集)
> 这个取舍**在 2.0 里得到了回报**:上游宣布 2.1 将废弃"Airflow 作为内部
> 编排器",官方推荐迁到 k8s 原生编排——我们不用做任何迁移。

## 决策

- **数据库用 Postgres,不用 chart 默认的 MySQL**——官方支持,复用我们已有的
  共享 Postgres 实例(建独立库 `openmetadata_db`,见 [ADR](../architecture.md)
  里"共享实例分库"的原则),不额外起一个 MySQL。
- **采集任务编排用官方的 `pipelineServiceClientConfig.type: k8s` 模式,不用
  Airflow**——原生 k8s Job,不是变通方案。权衡过三条路:复用现有业务
  Airflow(会把 OpenMetadata 的升级节奏和承载核心业务调度的 Airflow 耦合,
  采集插件版本要跟着 OpenMetadata 对齐,可能逼着业务 Airflow 跟着重建)、
  拆分一个独立 Airflow(干净但多背一份实例运维成本)、k8s 原生模式(两个
  问题都不存在)。选第三条。
- **搜索后端 OpenSearch,单节点**,`sysctlInit.enabled: true` 让 chart 自带的
  特权 initContainer 处理 `vm.max_map_count` 内核参数,不用手动登 VM 改。

## 踩的坑(已解决,记进 troubleshooting.md 的简要版本)

- OpenSearch 2.12+ 的 security 插件强制要求 `OPENSEARCH_INITIAL_ADMIN_PASSWORD`,
  不设直接拒绝启动;这个密码要和 OpenMetadata 的 `elasticsearch.auth` 配置一致。
- 各组件的 create-db-job 如果不和 Postgres 同命名空间,连接串必须用完整域名
  `postgres.data.svc.cluster.local`,短名 `postgres` 只有同命名空间才能解析。
- `postgres-root`(建库要用的管理员密码)本身也是跨命名空间引用不了,要复制
  一份到目标命名空间——和 MinIO 凭据同样的坑,现在 `scripts/00-generate-secrets.sh`
  里有两套复制清单(`MINIO_CONSUMER_NAMESPACES` / `POSTGRES_ROOT_CONSUMER_NAMESPACES`)。

## 后果

- OpenMetadata 的用户认证(SSO 对接 Keycloak)、SAML/LDAP 都还没配,现在是
  chart 自带的默认认证方式,接 Keycloak OIDC 是后续待办,不在这次范围内。
- k8s 模式下,OpenMetadata 后端需要有权限在 `openmetadata` 命名空间创建 Job
  (`serviceAccountName: openmetadata-ingestion`),这次验证只跑到了"应用起来、
  web 界面能访问",还没真正触发过一次采集任务,RBAC 权限是否完全够用留到
  真正配置数据源采集时验证。
