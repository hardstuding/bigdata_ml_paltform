# 038. CloudNativePG 评估:给共享 Postgres 找 HA 升级路径

- 状态: 已采纳,**已完成实际迁移和切流量**(2026-08-13,用户在场安排的
  窗口,见文末"实际迁移记录")

## 背景

ADR-033(Postgres 备份)的"后果"部分提到过:备份有了,但真正的高可用
(自动故障转移、多副本)还是空白——现在的 `apps/postgres/` 是单实例原生
k8s manifest(ADR-008 的决定,`local-lite` 阶段刻意简化),这个共享实例
挂了,Keycloak/Hive Metastore/MLflow/Airflow/Superset 全部一起挂,是当前
架构里单点故障风险最集中的一个组件。用户确认过"换组件也是可以换的,
问题不大",这次做评估。

## 决策

### 选 CloudNativePG,不是 Bitnami/Zalando 等其他 Postgres operator

CNCF 沙箱项目,EDB(PostgreSQL 官方商业公司之一)主导维护,不是社区
业余维护——和这个项目一直以来"官方/主流维护的组件优先"的取舍一致(同类
考量见 ADR-008 否决 Bitnami chart、ADR-011 选 SeaTunnel 不选 Airbyte)。

### CRD 走一次性脚本装,不靠 Helm/ArgoCD

实测:`clusters`/`poolers` 这两个 CRD 太大(`clusters` 内嵌了完整的
PostgreSQL 配置 schema),超过 client-side apply 的 262144 字节
annotation 上限。一开始以为和 KServe(ADR-027)是同一个坑,加
`syncOptions: [ServerSideApply=true]` 却不管用,还是报一模一样的错——
没有深挖具体原因(ArgoCD 处理 Helm chart `crds/` 目录这条路径本身不完全
遵守这个 sync option,还是这两个 CRD 就是比 server-side apply 能处理的
上限还大),不重要,反正 GitOps 这条路走不通。退回一次性脚本
(`scripts/16-install-cloudnative-pg-crds.sh`),下载官方 release 清单、
只挑出 CRD 部分、`kubectl apply --server-side --force-conflicts`——和
KServe 的 ClusterServingRuntime(`scripts/10-install-kserve-serving-
runtimes.sh`)是同一类"GitOps 这条路走不通,退回一次性脚本"的情况。
Helm chart 的 `crds.create` 关掉,避免同一份资源被两条路径重复管理。

## 验证记录(2026-08-13)

- operator 装好后(`apps/definitions/cloudnative-pg-operator.yaml`)真的
  健康跑起来(之前被 CRD 缺失卡在 crash loop,`no matches for kind
  "Cluster"`,CRD 脚本跑完之后自愈)。
- 建了一个一次性、单实例的测试 `Cluster`(`instances: 1`,不在 GitOps
  管理范围内,用完即删,不是要长期保留的资源):
  - 从 `Setting up primary` 到 `Cluster in healthy state` 大约 90 秒。
  - 真实连进去跑了 DDL/DML(`CREATE TABLE`/`INSERT`/`SELECT`),不是只看
    Cluster 状态字段——`SELECT version()` 确认是真实的 PostgreSQL 18.4。
  - 资源占用:operator 本身 ~79Mi,单实例测试 Cluster ~59Mi(闲置状态)。
    现有 `apps/postgres/` 单实例(`postgres-0`)闲置约 154Mi,数量级接近,
    CNPG 的 operator 常驻开销(~80Mi)是额外要付的成本。
- 测试完已经删除(`kubectl delete -f` 那个一次性 Cluster 定义),operator
  本身保留,为后续迁移做准备。

## 实际迁移记录(2026-08-13,用户在场)

评估完成后,用户明确表示"反正都要做,不用管优先级",在场安排了这次
迁移。切流量这个动作本身触发了 Claude Code 权限分类器的拦截(连续拦了
两次:一次是给 `postgres` 这个 ArgoCD Application 触发 sync,一次是同一个
动作的重试),按规则停下来跟用户说清楚在做什么、为什么需要这个权限,
用户确认后由用户自己执行了那条 `kubectl patch` 命令触发同步——高风险
动作最终还是要经过人明确点头这一步,不是"之前说过随便做"就能一路做到底。

### 数据迁移:两轮 pg_dumpall/restore,把"最新鲜"这件事当真

第一轮 dump/restore 完成后,又等了一段时间(中间在处理其他事),担心
这段时间里旧实例可能有新写入,于是在真正切流量前又做了一轮:先
`DROP DATABASE`(只删数据库,不删角色,重跑 dump 时角色相关的
`CREATE ROLE` 报"already exists"是预期的良性冲突)、再从旧实例重新
`pg_dumpall`、重新 restore 一遍,把"最后一次快照"和"真正切流量"之间
的时间窗口压到最小。恢复后用真实数据核对过(`keycloak.user_entity` 的
行数和内容),不是只看 `pg_dumpall`/`psql` 命令退出码是 0 就当完事。

### 切流量机制:ExternalName 别名,不是自己重写 selector

一开始想直接把 `postgres` 这个 Service 的 selector 从
`app: postgres`(老 StatefulSet)改成手写的 `cnpg.io/cluster: postgres-cnpg`
+ `role: primary`——**这里写错了一次**:CNPG 打在当前主实例 pod 上的
label 键名是 `cnpg.io/instanceRole`,不是 `role`(用
`kubectl get svc postgres-cnpg-rw -o jsonpath='{.spec.selector}'` 查
CNPG 自己生成的 Service 才发现这个键名不对)。与其自己维护一份"如何
识别当前主实例"的 selector 逻辑(未来 CNPG 换了 label 方案会静默失效),
改用 `type: ExternalName` 直接别名到 CNPG 自己生成、自己维护的
`postgres-cnpg-rw` 这个 Service——这部分逻辑完全交给 CNPG 维护,故障
转移时主实例变了,`postgres-cnpg-rw` 自动跟着变,这一层不用管。真正
执行前用一个临时 Service 名字实测过 ExternalName 在这个集群上确实能
正确解析 DNS + 建立连接,不是先猜后套。

老的 StatefulSet 切完流量后没有立刻删除,只是不再接收流量(Service
改了 selector/type 之后自动生效)——数据还在它的 PVC 里,是最快的回滚
手段。

### 切完流量之后踩到的真实 bug:CNPG 默认 TLS 1.3,老 JDBC 驱动握手失败

切完流量、重启 `keycloak-keycloakx`/`hive-metastore` 让它们真正用上新
连接(而不是继续用切流量前就已经建立的老连接——这一点专门确认过,
DNS 解析改了不代表已经建立的 TCP 连接会自动切换,必须重启才能验证"真
的切过去了"而不是"看起来切了"),Keycloak 很快恢复正常,但
Hive Metastore 陷入崩溃重启循环。

排查:`crictl exec` 进容器看 `schematool -info` 的输出,报
`SSL error: Received fatal alert: protocol_version`——不是被拒绝、
重试明文连接失败,是 SSL 握手本身失败,`schematool` 因此判定"schema 不
存在"要重新 `initSchema`(所幸 `apps/hive-metastore/manifests/
deployment.yaml` 里那层"先探测 schema 是否已存在"的幂等检查生效,
`initSchema` 卡在 SSL 握手这步就失败了,没有真的跑到会冲突/破坏数据的
那一步——数据全程没有损坏,`kubectl exec ... psql ... SELECT` 直接查
证实过)。

查证:`SHOW ssl_min_protocol_version` 确认 CNPG 默认给的值是
`TLSv1.3`——比大多数生产环境的默认值都严格。旧的 `postgres:16.6` 官方
镜像默认没开 SSL,现有所有组件的连接方式从来没考虑过这一层,不会只有
Hive Metastore 会踩到,只是它先撞上了(apache/hive:3.1.3 镜像自带的
JDBC 驱动比较老)。修法:在 Cluster 的 `spec.postgresql.parameters` 里把
`ssl_min_protocol_version` 降到 `TLSv1.2`(仍然是加密连接,只是对老
客户端更宽容),这是**服务端一次性配置**,不用逐个组件的连接串加
`sslmode=disable`。改完不需要重启 Postgres(reload 级别的参数),
`SHOW ssl_min_protocol_version` 确认生效后,`hive-metastore` 的 pod
不用手动干预,几分钟内自己从崩溃重启循环里恢复,`schematool -info`
探测正确识别出 schema 已存在,启动了真正的 Hive Metastore 服务
(日志里能看到 "Starting Hive Metastore Server",不是卡在探测步骤)。

**教训**:换 Postgres 发行版/管理方式的时候,不能只关注"数据能不能
迁移过去"这一个维度——连接层面的默认行为(这次是 TLS 最低版本)也可能
和旧环境不一样,而且不一定在迁移那一刻就暴露,是"服务重启、真正走
新连接"的时候才会暴露,验证清单里要包含这一类"连接协议层面的默认值
差异",不只是"数据对不对"。

### 最终验证

切流量 + 修完 SSL 问题之后:
- `keycloak-keycloakx-0`、`hive-metastore` 两个 pod 都是 `1/1 Running`,
  重启次数不再增长。
- 真实业务检查:外部走 `keycloak.local-lite.test`(和浏览器同一条路径)
  请求 OIDC discovery 端点,`200 OK`——这个端点要读 Postgres 里的 realm
  配置,能返回正确内容证明不是"进程活着但读不到数据"这种假健康。
- 老的 `postgres-0`(StatefulSet)全程没有被删除,保留作为回滚安全网。
- ArgoCD 全部 Application 保持 `Synced`/`Healthy`。

## 后果

- **这台本机大概率跑不起真正的 HA(2-3 副本)配置**——单实例测试已经
  接近现有 Postgres 的资源画像,3 副本 HA 会是现在的 2-3 倍开销,加上
  WAL 归档/备份组件的额外开销,`local-lite` 这台 10GB 内存的机器上,
  HA 模式的实际收益(容错)和本来就没有多节点可以做真正故障隔离的现实
  不太匹配——CNPG 在 `local-lite` 大概率还是只会跑单实例(比现在的
  好处是有了 operator 管理的自动化运维能力:自动备份、更规范的升级流程),
  真正体现 HA 价值要等接入 cloud-full/生产环境、有多个节点可以分布副本
  的时候。
- 没有评估 CNPG 自带的备份能力(`Backup`/`ScheduledBackup` CRD,原生支持
  对象存储)是否应该取代 ADR-033 那套手写的 CronJob 方案——两者做的是
  同一件事,真正迁移到 CNPG 的时候需要决定留哪一个,不是并存。
- 没有评估 CNPG 的 Pooler(内置 PgBouncer 连接池)要不要用——现在的
  组件都是直连,连接数还没到需要连接池的规模。
- **2026-08-13 补充:老的 `postgres-0` StatefulSet 已正式下线。** 切完
  流量后刻意保留了一段时间作为回滚安全网,MLflow 验证通过、确认新实例
  稳定运行之后,用户明确同意清理(这是不可逆操作,提前问过):删除
  `apps/postgres/manifests/statefulset.yaml`/`init-configmap.yaml`
  两个文件(git commit + push,ArgoCD 自动 prune 掉 StatefulSet),
  再手动删除它的 PVC(`data-postgres-0`,5Gi)释放存储空间。回滚安全网
  正式撤除,后续如果新实例出问题,只能靠 ADR-033 的每日备份恢复,不再
  有"秒级切回老实例"这条路。
- MLflow/OpenMetadata/Superset/Airflow 这几个目前是 park 状态的组件,
  还没有实际验证过它们连新的 CNPG 实例(包括这次发现的 TLS 版本问题)
  没有问题——按"迟早会被拉起来"的原则,`platform/network-policies/
  manifests/postgres.yaml` 的允许列表已经覆盖了它们的 namespace,
  `ssl_min_protocol_version` 的修复也是服务端全局生效,理论上应该没
  问题,但没有真的拉起来跑一遍验证过,等这几个组件下次被拉起来时需要
  留意。

  **2026-08-13 补充:MLflow、OpenMetadata、Superset、Airflow 已验证,
  四个都过了,`park 状态的组件` 这份清单到此清空。**

  OpenMetadata(Java/JDBC 客户端,和当初撞坑的 Hive Metastore 同一类
  技术栈风险)un-park 后:`run-db-migrations` 这个 initContainer(Flyway
  schema 迁移)对着新 CNPG 实例干净跑完,没有重复 Hive 那次的 TLS 协议
  版本报错——推测是 OpenMetadata 打包的 PostgreSQL JDBC 驱动版本比较新,
  原生支持 TLS 1.3,不像 Hive 那次的老驱动。主容器 `1/1 Running`,
  `/api/v1/system/version` 返回 200,日志里没有任何 postgres/jdbc/ssl
  相关报错。验证完按本机资源紧张的惯例重新 park 回去(OpenSearch + 
  OpenMetadata 一起跑对内存压力不小)。

  Superset(Python/psycopg2,和 MLflow 同一类技术栈)un-park 后:
  `superset-init-db` 这个 chart 自带的 init Job 里 alembic 迁移干净跑完,
  日志里能看到"Admin user already exists, skipping"——确认迁移前的
  admin 账号数据完整保留,不是全新空库。主 pod `1/1 Running`,
  `/health` 返回 200。验证完同样重新 park 回去。

  Airflow(同样 psycopg2)un-park 后:`airflow-migrate-db`(自己手写的
  普通 Job,不是 chart 的 hook,原因见 apps/airflow/manifests/
  migrate-db-job.yaml 的注释)日志里明确一行"Database migration done!"。
  webserver(api-server)/scheduler/dagProcessor/triggerer 全部
  `Running`/`Ready`,健康检查 API 返回 200,直接查 Postgres 里的
  `dag` 表确认之前配的 `seatunnel_device_events` 这条 DAG 记录还在,
  不是空库。验证完重新 park 回去——这是四个里资源占用最重的一个
  (webserver+scheduler+dagProcessor+triggerer 四个常驻组件),过程中
  持续盯着 `free -h`,内存最紧张时可用还剩 2.5Gi,没有复现
  2026-08-08 那次同时拉起多个重组件导致 VM 打满的情况(这次是一次
  只拉一个、验证完立刻 park 回去,不是同时叠加)。 un-park 后真实调用 REST API
  (`POST /api/2.0/mlflow/experiments/create` + `search`)确认:能正常
  写入新数据(拿到新 `experiment_id`),旧数据(迁移前就有的
  `demo-experiment`/`demo-classification` 等)完整保留,psycopg2 连
  CNPG 没有像 Hive 的老 JDBC 驱动那样撞上 TLS 协议版本问题。验证过程中
  顺带发现并修复了一个和 CNPG 本身无关、但同一批组件共用的真实 bug:
  `create-db-job.yaml` 这个模板(mlflow/openmetadata/keycloak-db-init/
  airflow/superset 五个组件共用同一套模式)会在"刚创建的 pod 首次连接
  Postgres"这个已知延迟窗口里把 `backoffLimit` 耗尽而彻底失败(不是
  等一下自己会好,是 4 次重试全灭),因为每次重试都是全新 pod、各自
  重新踩一次同一个延迟——已经给全部 5 个组件加上 `pg_isready` 重试
  循环(和 ADR-033 补充里 `postgres-backup` 的修法同一个原则),
  OpenMetadata/Airflow/Superset 还没有实际验证但用的是同一个模板,
  等它们下次被拉起来时应该会直接受益。
