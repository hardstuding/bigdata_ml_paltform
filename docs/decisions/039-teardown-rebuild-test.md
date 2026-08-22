# 039. 推倒重建测试:验证"一键部署"是不是真的能一键部署

- 状态: 已完成(2026-08-13,用户明确授权)

## 背景

README 的["从零拉起整套服务"](../../README.md#从零拉起整套服务新集群--迁移到-gitlab--生产-idc)
一直是写好的文档,但从来没有人真的从一个空集群跑过一遍——所有组件都是
增量式地一个个加到这个已经跑了很久的 colima 集群上的,不是真的"从零"。
用户问"是不是都部署完了",在确认所有当时 park 的组件(MLflow/
OpenMetadata/Superset/Airflow/Kafka/Trino/SeaTunnel)都验证过部署没问题
之后,同意做一次真正的推倒重建测试。

## 决策

真的把本机的 colima VM 删掉重建(`colima delete -f` + `colima start`),
从空集群开始,严格按 README 文档的步骤跑一遍,不抄近路、不用任何"其实
我知道该怎么绕过去"的经验值。

**一个意外发现**:`colima delete` 不会清空 `disk: 60` 那个"容器数据盘"
(`/dev/vdb1`,单独挂载在 `/var/lib/docker`),只清空根盘(k3s/kubelet
状态、local-path PV 数据都在根盘上)。这意味着已经拉过的镜像会跨重建
保留,但 K8s 层面的状态(Deployment/PVC/数据库内容)是真正全新的——
对这次测试反而是好事:验证的是"GitOps + 密钥 + 空数据库能不能正确
从零构建",不需要为了验证这件事而重新承受几十 GB 镜像下载的时间成本。

## 真实发现的坑(这次测试的核心价值)

严格按文档走的过程中,一共发现并修复了 5 个之前从未暴露过的真实 bug——
全部是"增量式开发从来不会触发,只有真的从空集群重建才会暴露"的那类:

1. **`scripts/00-generate-secrets.sh` 顺序问题**:`copy_secret` 函数在
   `spark-operator`/`seatunnel` 这些命名空间还不存在时(它们是后面
   ArgoCD 才会建出来的)直接报错退出,不像同一个脚本里 oauth2-proxy-secret
   那部分已经有"命名空间不存在就跳过"的判断。修法:给 `copy_secret` 也
   加上同样的判断。

2. **README 漏了 `scripts/16-install-cloudnative-pg-crds.sh`**:CNPG
   引入(ADR-038)之后没人把这一步加进文档的部署步骤列表,导致 `postgres`
   这个 Application 会一直卡在 `Missing`(`Cluster` 这个 kind 不存在)。

3. **hive-metastore 缺一个 `create-db-job.yaml`**:它的 Postgres 角色/
   库以前是靠老 `postgres` StatefulSet 的 `init-configmap.yaml`(
   `docker-entrypoint-initdb.d` 首次初始化脚本)建的,CloudNativePG 迁移
   之后这条路径就已经失效,后来那份 `init-configmap.yaml` 又在下线
   `postgres-0` 时被删掉(同一轮工作里更早的一步),这条创建逻辑彻底
   没了。补了一份和其他 5 个组件同款的 `create-db-job.yaml`。

4. **`coredns-custom` 硬编码了 `ingress-nginx-controller` 的 ClusterIP**:
   ClusterIP 是集群创建时按 Service CIDR 分配的,不是固定值,重建集群后
   分到了新 IP,写死旧值的 DNS 配置导致所有做 OIDC discovery 的组件
   (ArgoCD/Trino/Superset/OpenMetadata/MLflow/Argo Workflows)全部连
   超时。改成用 CoreDNS 的 `rewrite name ... answer auto` +
   `kubernetes` 插件动态解析 Service 名字,不管 ClusterIP 怎么变、
   集群重建几次都不用再改。**这是这次测试挖到的影响面最广的一个坑**——
   静态 IP 这种写法本来就是定时炸弹,只是之前从来没有"重建集群"这个
   触发条件。

5. **`minio` 命名空间的 NetworkPolicy 漏了自己**:MinIO chart 的
   `buckets:` 声明式配置是靠一个 Helm post-install hook Job
   (`minio-post-job`,同样建在 `minio` 命名空间里)执行 `mc mb` 建的,
   这个 Job 连自己命名空间里的 `minio` pod 都被 `default-deny` 挡住。
   之前没暴露是因为这个 Job 是 `post-install`(不是 `post-upgrade`),
   只在最初第一次装的时候跑,而那时候这条 NetworkPolicy 还没加上去。

## 验证记录

- 从 `colima delete -f` 到全部 26 个 ArgoCD Application 变成
  `Synced`/`Healthy`,过程中依次修复上面 5 个坑,全程没有跳过任何一步
  文档步骤,也没有用任何"手动 kubectl 改一下活对象"的临时手段——每个
  修复都是"改 git、push、等 ArgoCD 收敛",符合这个项目"GitOps 是唯一的
  操作接口"的原则。
- 内存全程没有紧张过(最紧张时可用还有 3.1Gi),因为一次只跑核心组件,
  没有同时把 Kafka/Trino/OpenMetadata/Airflow 这些 park 组件也一起拉起来
  ——这些之前已经在 ADR-038 和 pending-definitions/README.md 里单独
  验证过部署,这次不需要重复验证,重点是验证"核心骨架"能不能从零
  正确构建。
- `local-lite-enable-swap.sh` 的 4GB swap 文件在 `/var/lib/swapfile`
  (根盘上),重建后需要重新跑一次——已经在这次测试里做了,值得记一笔:
  这个是根盘上的状态,不会跨 `colima delete` 保留。

## 后果

- `permission-request-app-git` 这个 Secret(GIT_TOKEN)在根盘上,重建后
  丢失,需要用户重新提供 token 或重新走一遍那份 Secret 的创建步骤——
  这个是有意为之(见 `apps/permission-request-app/manifests/
  deployment.yaml` 顶部注释:"git 写权限是敏感凭据,不能由自动化流程
  自己造一个"),不是这次测试的遗留问题,是设计上要求人工介入的部分。
- 没有跑 `scripts/08-create-demo-data.sh`/`09-train-demo-model.sh`/
  `11-deploy-demo-inference-service.sh`/`13-run-spark-iceberg-demo.sh`/
  `15-create-device-events-dashboard.sh` 这几个演示脚本——它们本来就是
  "随时可以重跑重建"的可选验证,不是这次测试的必需项,核心骨架(全部
  Application `Healthy`)已经证明"一键部署"这个承诺是真的。
- 这次没有涉及 Kafka/Trino/SeaTunnel/OpenMetadata/Superset/Airflow/
  MLflow 这几个 park 组件的重新验证(它们本来就是按需 un-park 的,不是
  核心骨架的一部分)——理论上它们的 create-db-job/NetworkPolicy 也应该
  经得住同样的"从零"检验,但这次测试没有把它们也拉起来验证,留给下次
  实际需要用到它们的时候顺带确认。


## 2026-08-22 补充:这套办法搬到 cloud-full 上有两个坑

这份 ADR 记的是 **local-lite(colima)** 上的推倒重建。2026-08-22 想在
cloud-full 上照做一次,撞到两件当时不存在的问题:

### 1. `k3s-uninstall.sh` 清不掉自定义的 `--data-dir`

cloud-full 的 k3s 是 `--data-dir /data/k3s` 装的(见
`scripts/21-bootstrap-cloud-vm.sh`),卸载脚本只处理默认路径。**实测**:
跑完 `k3s-uninstall.sh` 之后 `/data/k3s/server/db` 和
`/data/k3s/storage`(13 个 local-path PV)原封不动;重新装回去,node 的
AGE 还是 6d1h,所有 Application 和数据都回来了。

**这个坑的危险之处不是"没删干净"**,是它会让人得出一个假的结论:
"卸载重装一遍,全部 Synced/Healthy,一键部署验证通过"——实际上一行
新东西都没建。要从空开始必须显式 `rm -rf /data/k3s`。

### 2. cloud-full 这个 k3s 集群不是这个项目独占的

`/data/k3s/storage` 里有 `pvc-..._data-ai-platform-v2_control-api-data`
——Codex 那个并行项目和这个平台**共用同一个 k3s 集群**,不只是共用一台
云主机。所以在 cloud-full 上做推倒重建,不是"清掉我自己的东西"这么简单,
必须先和用户确认另一个项目的数据怎么处理。

（2026-08-22 那次执行了 `k3s-uninstall.sh`,集群短暂中断、Codex 项目也
跟着中断,随后用 `scripts/21-bootstrap-cloud-vm.sh` 原样装回,数据因为
data-dir 没被删而完整恢复,没有发生丢失。）
