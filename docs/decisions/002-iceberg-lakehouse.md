# 002. 表格式用 Iceberg,不锁死在某个引擎的私有格式

- 状态: 已采纳(2026-08-08)

## 背景

需要选一个"新数据"的存储层。CDH 时代默认是 Hive 表 + HDFS。现在有 Iceberg、Delta Lake、Hudi 等开放表格式可选,也可以什么都不用直接裸文件。

## 决策

新写入的数据统一用 Iceberg 表格式,存储在 MinIO(S3 兼容对象存储)上,元数据登记在 Hive Metastore。

## 理由

- Iceberg 是当前生态支持最广的开放表格式,Spark/Trino/Flink 都是一等公民支持,不绑定单一计算引擎。
- 对比 Hive 表直接管理 HDFS 目录:Iceberg 有 schema 演进、分区演进、时间旅行、更好的并发写入语义,运维负担反而更低。
- Hive Metastore 只是元数据服务,不需要把整套 HDFS/YARN 一起搬进来,见 [ADR-003](003-no-hdfs-on-k8s.md)。

## 后果

- Spark/Trino 都要装对应的 Iceberg 连接器/runtime。
- 现有遗留集群里的 Hive 表(非 Iceberg)不会自动获得这些能力,需要按需转换或者继续用 Trino 联邦查询直接读旧表。
