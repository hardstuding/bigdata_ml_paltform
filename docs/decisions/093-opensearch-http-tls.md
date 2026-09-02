# ADR-093:OpenSearch 的 HTTP 层不启用 TLS(传输层照旧)

日期:2026-09-02
状态:**已实现并实机验证(血缘推送恢复,产物核对过)**

## 问题

`seatunnel_device_events` 这条 DAG 的 `push_lineage` 任务每天失败,
OpenMetadata 返回 HTTP 500。OpenMetadata 自己的日志里是:

```
SSLHandshakeException: No subject alternative DNS name matching
opensearch-cluster-master found
```

OpenSearch 镜像自带的是 demo 证书:`CN=node-0.example.com`,SAN 里只有
`node-0.example.com` / `localhost` / `127.0.0.1` —— **没有集群内的服务名**。
OpenMetadata 用服务名连它,Java 的主机名校验直接拒。

**影响面比看起来大得多。** 表现只是一条 DAG 的一个任务红了,实际是:
OpenMetadata 在 `PUT /api/v1/lineage` 时要同步更新搜索索引,连不上就整个
请求 500 —— **血缘这条能力整个是坏的**,而能力矩阵上那一格当时还是绿的。

另外要说清楚:`scripts/20-configure-openmetadata-search-truststore.sh` 那
一步解决的是"证书不被信任"(PKIX path building failed),和"主机名对不上"
**是两件事**。加多少信任也解决不了后者。

## 决定

关掉 OpenSearch 的 **HTTP 层** TLS,传输层(节点间)照旧开着。OpenMetadata
改用 `http` 连,truststore 相应关掉。

**为什么不签一张带正确 SAN 的证书。** 那要替换镜像里的 demo 证书、重做
truststore、改 security 插件的一整段配置。而这一跳是**同一个命名空间内、
只有 OpenMetadata 一个客户端、还有 NetworkPolicy 挡着**的内部通信。
按这个阶段的取舍,代价和收益不成比例。

**生产环境应该签**,记在
[`production-readiness-gaps.md`](../project/production-readiness-gaps.md),
没有假装这一档已经做完。

**实现细节:用同名环境变量覆盖 `opensearch.yml` 的设置**,而不是提供
`config.opensearch.yml`。后者是整个文件替换,会把镜像里 security 插件那
一整段 demo 配置一起顶掉 —— 那才是真正危险的改法。

## 验证(2026-09-02,cloud-full)

| 步骤 | 结果 |
|---|---|
| OpenSearch 明文 9200 | `_cluster/health` 返回 yellow(单节点,预期) |
| 重跑那条 DAG | 三个任务全 success(此前 push_lineage 每次 failed) |
| 血缘产物 | `/api/v1/lineage/pipeline/name/airflow-platform.seatunnel_device_events` 返回 1 节点 1 边,指向 `trino.iceberg.demo.device_events` |

**这个根因是靠 [ADR-092](092-airflow-remote-logging.md) 才查出来的**:
在配远程日志之前,task pod 一删日志就没了,能看到的只有
"push_lineage failed" 五个字。

## 一并暴露出来的第二个问题

修完之后 OpenMetadata 起不来,`Init:0/1` 反复重启,报
`remaining connection slots are reserved for roles with the SUPERUSER
attribute` —— 共用 Postgres 的连接槽被占满(91/100,其中 hive 40 个、
openmetadata 30 个,**全是 idle**)。

`max_connections` 2026-08-30 提到过 200,但**只在活实例上 ALTER,没写进
声明式配置**,被 ArgoCD 的 selfHeal 打回了默认值。已经写进
`templates/apps-postgres-manifests/cluster.yaml`。

真正的根因(各组件连接池上限之和远大于 max_connections)记在 roadmap。
