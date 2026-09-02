# 033. 共享 Postgres 每日自动备份

- 状态: 已采纳,已验证(2026-08-12/13:备份 + 恢复流程都真的跑通过;
  2026-08-13 CloudNativePG 迁移验证期间还发现并修复了一个真实的"备份
  静默失败"事故,见文末"2026-08-13 补充")

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
用户记录(admin/admin/使用方,和当时 Keycloak 里的真实账号对得上)。
备份文件本身是真实、完整、可恢复的 SQL,不是只是"看起来传上去了"。

## 2026-08-13 补充:一次真实的"备份静默失败"事故

CloudNativePG 迁移(ADR-038)切完流量后,顺手验证了一下 postgres-backup
这条链路受不受影响,结果发现了一个和 CNPG 无关、早就存在的真实 bug——
**Job 报告成功,但传到 MinIO 的备份文件只有 20 字节,是空的**。

### 根因:`pg_dumpall | gzip > file` 在管道里会把失败悄悄吞掉

`set -e` 在 POSIX shell 的管道语法下,只检查管道里**最后一个命令**
(这里是 `gzip`)的退出码——这是 shell 的标准行为,不是这个镜像的 bug。
`pg_dumpall` 真的连接失败报错(`Connection refused`)时,它的 stdout 是
空的,但 `gzip` 照样能"成功"把空输入压缩成一个 20 字节的合法 gzip 文件,
整条管道的退出码是 `gzip` 的 0,`set -e` 完全没察觉,Job 正常退出、
报告成功。

查了历史记录才发现**这不是第一次发生**——2026-08-13 凌晨 02:04 UTC(用户
重启电脑、colima 重新拉起的那个窗口期)那次每日定时备份,产出的也是
同样 20 字节的空文件,当时完全没人注意到,一直"看起来在正常运行"。

### 修复

1. `pg_dumpall` 先落盘(`> /tmp/dump.sql`)、再单独 `gzip` 压缩,两步
   分开,`set -e` 才能真正捕捉到 `pg_dumpall` 自己的失败,不依赖
   `bash` 才有的 `set -o pipefail`(这个 `/bin/sh` 不一定是 bash)。
2. 上传步骤加一道体积检查(`<1KB` 直接拒绝上传、Job 失败)——第二道
   防线,不指望第一步是唯一防线,以防以后出现别的、还没遇到过的失败
   模式产出异常小的文件。
3. 修复过程中验证时又抓到一个真实现象:刚创建的 pod 第一次连 Postgres
   有一定概率报 `Connection refused`(不是超时、不是 DNS 解析失败),
   几秒后用新 pod 重试就正常——怀疑是这台机器 k3s/Flannel 的
   NetworkPolicy 规则对新创建的 pod 生效有短暂的编程延迟(ADR-035 验证
   NetworkPolicy 时也见过同一类现象),没有深挖确认根因,给
   `pg_dumpall` 加了个简单重试(5 次、间隔 5 秒)。

修复后手动触发验证:`postgres-backup-verify3` 这个 Job 首次尝试即
成功,MinIO 里的文件是 **485KiB**(和之前正常时的 456KiB 同一个数量级,
不是又一次巧合的小文件)。已清理掉 MinIO 里那两份 20 字节的坏文件
(2026-08-13 02:04 UTC 那份、以及验证过程中手动触发产生的那份)。

### 教训

这次是在验证"CNPG 迁移有没有连带影响其他组件"时**顺手**发现的,不是
专门去查备份系统才发现的——`docs/operations/troubleshooting.md`/
各 ADR 里已经反复出现"Job/CronJob 报告成功不代表真的做对了"这类教训
(iam-sync 的 apt delayed-item 问题是另一个例子),这次是同一类问题在
备份这个更关键的系统上的又一次体现:**光看 Job/Pod 的退出码和状态不够,
对于备份这种"平时用不上、真正需要的时候才发现有没有用"的系统,必须
定期抽查产出物本身**(文件大小、内容),不能只信任"绿色的勾"。
