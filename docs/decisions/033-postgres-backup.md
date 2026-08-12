# 033. 共享 Postgres 每日自动备份

- 状态: 已采纳,已验证(2026-08-12/13:备份 + 恢复流程都真的跑通过,见
  "验证记录")

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
- 恢复演练用的是**独立的一次性 Postgres pod**,不是直接对着共用的活实例
  跑`scripts/restore-postgres-backup.sh`——那个脚本会真的覆盖 Keycloak/
  Hive Metastore/MLflow/Airflow/Superset 共用的当前数据,这类破坏性操作
  不应该在没有人在场确认的情况下执行。这次验证证明的是"备份文件本身是
  真实可恢复的 SQL、恢复机制没问题",不是"跑过 restore-postgres-backup.sh
  这个脚本本身"——脚本的 psql/port-forward 那部分逻辑和这次手动验证的
  是同一套(见脚本本身),只是目标从共用实例换成了一次性 pod,可以认为
  已经间接验证过。真要在共用实例上跑一次完整恢复,需要人明确安排一个
  可以接受短暂中断的窗口。

## 验证记录

2026-08-12:`kubectl create job --from=cronjob/postgres-backup` 手动触发,
两个容器(dump/upload)都 `Succeeded`,直接查 MinIO(用 boto3 列 bucket,
不是只看 Job 状态)确认文件真的在:
`postgres/postgres-20260812T012950Z.sql.gz`,466682 字节。

2026-08-13:恢复流程验证。从 MinIO 下载这份备份,起一个独立的一次性
`postgres:16.6` pod(全新、空的实例),把 `.sql.gz` 直接 `gunzip | psql`
灌进去——完整跑完,3894 行 SQL 只有一行 `ERROR: role "postgres" already
exists`(`pg_dumpall` 输出本身包含重建 postgres 这个角色的语句,全新
容器已经自带这个角色,属于预期内的良性冲突,不影响其余内容执行,这也是
真实场景下"恢复到已经在跑的实例"会遇到的同一种情况)。恢复后 `\l` 确认
所有预期的库都在(keycloak/metastore/mlflow/openmetadata_db/superset),
直接查了一张真实业务表验证不是空壳:`keycloak.user_entity` 里 3 条真实
用户记录(admin/admin/zhenghe,和当时 Keycloak 里的真实账号对得上)。
备份文件本身是真实、完整、可恢复的 SQL,不是只是"看起来传上去了"。
