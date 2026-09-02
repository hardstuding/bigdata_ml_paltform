# ADR-083:公司内部包怎么共享 —— MinIO 上的静态索引,不引入包服务器

日期:2026-08-29
状态:**已实机验证**(2026-08-29):pod 里不加任何参数 `pip install platform-helpers` 装上并能用

## 问题

使用方:「公司内部自己开发的一些 python 包、java 包等,怎么共享和管理」。

现状是**完全没有**:

- 平台自己的 `platform-sdk` 靠 `COPY platform-sdk /tmp/...` 打进镜像 —— 改一行
  SDK 就要重建所有用到它的镜像。
- 使用者自己的包**没有任何落脚点**。想在 notebook 里用一个内部包,只能把
  代码拷进 notebook,或者让平台组帮忙塞进镜像重建。

## 约束(这几条决定了方案)

1. **GitHub Actions 到不了集群里的 MinIO** —— MinIO 没有公网入口(有意的)。
   所以"CI 构建完上传"这条最直觉的路走不通。
2. 集群在境内,拉境外东西不稳(见 `docs/operations/image-registry.md`)。
   方案不能依赖一个境外服务。
3. 这个仓库的既定原则:能不新增常驻组件就不新增;GitOps 是操作接口。

## 候选方案

| 方案 | 怎么做 | 为什么没选 |
|---|---|---|
| **A. MinIO 上的静态索引** | 集群内的 Job 构建 wheel,传到 MinIO,生成 PEP 503 索引 | **选这个** |
| B. 跑 devpi / pypiserver | 真正的包服务器,有上传 API 和账号体系 | 多一个常驻组件要运维、备份、升级。而"上传"这个动作在这套体系里本来就该走 git,不该有第二条旁路 |
| C. 复用公司已有的 Nexus / Artifactory | 如果有,这是最省事的 | **需要 使用方确认公司有没有、以及网络通不通**。这条没被排除,只是现在没法验证——真有的话,换成它只需要改 pip 的 index 地址 |

## 决策:MinIO 上的静态索引

```
packages/<名字>/          ← 使用者写的(pyproject.toml + 源码)
        ↓  集群内的 CronJob 每小时:clone → 构建 wheel → 传 MinIO → 重生成索引
s3://packages/simple/     ← PEP 503 静态索引
        ↓  pip install <名字>
notebook / 作业 pod
```

**为什么发布走 git 而不是 `twine upload`**:上传 API 意味着"包的来源"多了一条
不经过 review 的旁路。走 git 的话,内部包的每一次变更都有 commit、有 diff、
可回溯 —— 和这套平台其它东西一致。代价是发布不是立即的(最长等一个周期),
对内部包可以接受。

**为什么是静态索引而不是包服务器**:PEP 503 的"简单索引"就是一堆目录和
HTML 链接,MinIO 直接就能提供。不需要任何服务进程,也就没有它的运维、
备份、升级问题;MinIO 本来就在备份范围内。

**Maven 同理**:Maven 仓库也只是一套目录布局,同一个 bucket 换个前缀就行。
这次只做 Python(先有真实需求的那个),Java 的留到有人真的要发第一个 jar
时再做——**不预先造一个没人用的东西**。

## 谁能读

MinIO 上这个 bucket 设成集群内匿名可读(`mc anonymous set download`)。
不是"公网可读":MinIO 没有对外入口,而且 NetworkPolicy 限制了哪些命名空间
能连它(`platform/network-policies/manifests/minio.yaml`)。

**明确的取舍**:内部包对集群内所有工作负载可读,不做按组隔离。做隔离要给
pip 配凭据、要按组分 bucket、要处理凭据轮换 —— 对"公司内部共享的工具库"
这个场景,复杂度远大于收益。如果哪天有真的不能互相看的包,那时再说,
而不是现在为一个假想需求先付代价。

## 后果

- 使用者:`packages/` 下加一个目录、push,一小时内 `pip install <名字>` 就能用。
  notebook 和作业 pod 的 pip 已经配好了内部索引,不用自己加 `--extra-index-url`。
- **不解决**:版本冲突、依赖解析的复杂场景、以及"包坏了怎么回滚"——回滚就是
  git revert 之后等下一个发布周期。这些等真的撞到再说。
- 平台自己的 `platform-sdk` 暂时**不改**成走这个索引。它是镜像构建期的依赖,
  换过去会让镜像构建依赖一个运行中的集群,那是更糟的耦合。
