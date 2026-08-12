# 038. CloudNativePG 评估:给共享 Postgres 找 HA 升级路径

- 状态: 已采纳,**operator 已装、单实例测试 Cluster 已验证,真正的迁移
  (切现有共享实例)还没做**(2026-08-13)

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

## 后果

- **真正的迁移(切现有共享实例)这次没做**,评估的是"CNPG 这条路能不能
  走通",不是"已经切过去了"。迁移涉及:建一个新的 HA Cluster(至少
  2-3 副本)、把现有数据(`pg_dumpall`,ADR-033 已经验证过备份/恢复机制
  真实可用)导进去、把 Keycloak/Hive Metastore/MLflow/Airflow/Superset
  等所有组件的连接串/Secret 逐个切过去、验证、最后下线旧实例——这是
  牵一发动全身的动作(几乎所有组件都连着这个共享实例),需要用户在场
  安排一个可以接受短暂中断的窗口,不适合无人监督执行。
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
