# 033. 共享 Postgres 每日自动备份

- 状态: 已采纳,已验证(2026-08-12:手动触发 Job 跑通,确认
  `postgres/postgres-20260812T012950Z.sql.gz`(466KB)真的落进了 MinIO 的
  `backups` bucket;还没验证过恢复流程,见下面"后果")

## 背景

用户复盘架构现状时指出:`docs/operations/backup.md` 从 Phase 0 起就是占位
文档,共享 Postgres(Keycloak/Hive Metastore/MLflow/Airflow/Superset 好
几个组件共用同一个实例)没有任何备份,真丢了就是真没了。这是当前架构里
优先级最高的一个空白,先落地。

## 决策

- `pg_dumpall`(不是逐个组件单独 `pg_dump`):这是共享实例,一次性把所有
  库都备份,不用给每个新增的消费组件都补一份备份配置。
- CronJob 每天 02:00(UTC)跑一次,备份文件传到 MinIO 的 `backups/postgres/`
  路径下,保留最近 14 天(`mc rm --older-than 14d` 自动清理,防止无限
  增长)。
- 用官方镜像分两步:`postgres:16.6`(和 `apps/postgres/` 本体同一个镜像,
  自带 `pg_dumpall`)做 dump,`minio/mc`(这个仓库已经在用的官方 MinIO
  客户端)做上传——没有一个官方镜像同时装了这两样,分两步比额外引入一个
  来源不明的第三方镜像更符合这个项目的原则。
- **恢复刻意不自动化**:`scripts/restore-postgres-backup.sh` 需要人手动
  跑、手动选哪一份备份、手动输入 `yes` 确认才会真的执行——恢复是会覆盖
  当前数据的破坏性操作,不应该有任何脚本替人做这个决定。

## 后果

- 只备份 Postgres,**不覆盖 MinIO 里的 Iceberg 表数据本身**——MinIO 自己
  没有做快照/复制,这块还是空白,理由是 Iceberg 表数据理论上可以从数据源
  重新导入(是"可重建"的数据,和 Postgres 里那些"不可重建"的元数据/账号/
  实验记录不是同一类风险)。如果以后有不可重建的重要数据落进 MinIO,需要
  单独评估要不要给 MinIO 也接一份备份。
- **备份本身没有加密**,`.sql.gz` 里包含 Keycloak 用户密码哈希、各组件的
  连接凭据等敏感信息,存在 MinIO 这个仓库自己管理的对象存储里,访问权限
  等同于 MinIO 本身的权限——cloud-full/prod 阶段如果 MinIO 访问面更广,
  应该重新评估要不要加一层加密。
- 备份/恢复流程还没有真的做过一次完整的"备份 -> 删数据 -> 恢复 -> 验证"
  演练,只验证到"备份文件真的传到 MinIO 了"这一步(见下面"验证记录")。
  `docs/operations/backup.md` 里的"恢复演练记录"部分还是空的,后面应该
  真的找机会跑一次完整演练,不能假设"备份文件存在"就等于"恢复真的能用"。

## 验证记录

2026-08-12:`kubectl create job --from=cronjob/postgres-backup` 手动触发,
两个容器(dump/upload)都 `Succeeded`,直接查 MinIO(用 boto3 列 bucket,
不是只看 Job 状态)确认文件真的在:
`postgres/postgres-20260812T012950Z.sql.gz`,466682 字节。**恢复流程
(`scripts/restore-postgres-backup.sh`)还没有真的跑过一次**,这是明确的
下一步——"备份文件存在"不等于"恢复能用",不能假设脚本写对了就直接信任。
