# 备份与恢复

## Postgres

见 ADR-033。共享 Postgres 实例(Keycloak/Hive Metastore/MLflow/Airflow/
Superset 共用,2026-08-13 起由 CloudNativePG operator 管理,见 ADR-038,
但备份机制本身不受影响——CronJob 照样是连
`postgres.data.svc.cluster.local` 这个域名做逻辑备份,不关心背后是
StatefulSet 还是 CNPG Cluster)每天 02:00 UTC 由
`apps/postgres-backup/manifests/cronjob.yaml` 这个 CronJob 用
`pg_dumpall` 整体备份,传到 MinIO 的 `backups/postgres/` 路径下,保留
最近 14 天。

**2026-08-13 真实事故**:曾经因为 `pg_dumpall | gzip > file` 这种写法在
管道里悄悄吞掉失败,产出过一次 20 字节的空"备份"文件,Job 还报告成功
——详细过程和修复见 ADR-033 的补充记录。教训是备份这类系统不能只信任
Job/Pod 的退出码,要定期抽查产出物本身。

恢复用 `scripts/restore-postgres-backup.sh`——不带参数列出所有可用备份,
带上文件名会真的执行恢复(需要手动输入 `yes` 确认,这是刻意设计,恢复是
破坏性操作,不应该由脚本自动帮人决定)。

## MinIO 里的 Iceberg 表数据

**没有备份**——理由见 ADR-033 的"后果"部分:Iceberg 表数据理论上可以从
数据源重新导入,和 Postgres 里那些"不可重建"的元数据/账号/实验记录不是
同一类风险。如果以后有不可重建的重要数据落进 MinIO,需要单独评估。

## Keycloak realm 配置

包含在 Postgres 备份里(Keycloak 的 realm/client/用户数据都存在它自己的
Postgres 库里),不需要单独处理。

## 恢复演练记录

- **2026-08-12**:手动触发备份 Job,确认备份文件真实落到 MinIO(见
  ADR-033)。
- **2026-08-13**:恢复演练。从 MinIO 下载最新备份,恢复进一个独立的
  一次性 Postgres pod(不是直接覆盖共用的活实例——那是破坏性操作,需要
  人明确安排窗口才能做),确认所有预期的库都恢复了、抽查了一张真实业务表
  (`keycloak.user_entity`)确认不是空壳数据。详细过程和结果见 ADR-033
  的验证记录。
- 还没做过的:**对着共用的活实例**跑一次完整的
  `scripts/restore-postgres-backup.sh`。这个脚本本身的核心逻辑
  (下载备份 → port-forward → `gunzip | psql`)已经在上面那次演练里间接
  验证过(手动跑的是同一套命令,只是目标从共用实例换成了一次性 pod),
  但脚本本身作为一个整体、对着真实共用实例、包括它的确认交互流程,还没
  有真的执行过一次。需要人明确安排一个可以接受短暂中断的窗口。
