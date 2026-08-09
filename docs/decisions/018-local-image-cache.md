# 018. 本地镜像缓存 + 导出:为公司内网出不去国外做准备

- 状态: 已采纳(2026-08-10)

## 背景

这台 Mac 能自由访问国外的 chart 仓库/镜像源(GitHub、quay.io、ghcr.io、
docker.io 等),但公司内网大概率不行。到 cloud-full/IDC 那一步,如果每个
组件都要重新连国外源拉一次镜像,大概率拉不动或者很慢,之前 local-lite 阶段
积累的"这些镜像版本组合是验证过能跑的"这个事实也没法直接复用。

架构上已经规划了 Harbor(Phase 4,`docs/architecture.md` 里的私有镜像仓库),
但那是"以后 cloud-full/prod 的目标状态"。现在的问题是更早一步:**在真的搭
Harbor 之前,怎么不浪费这台 Mac 上已经验证过、已经拉好的镜像**。

## 决策

- **`scripts/list-project-images.py`**:静态扫描所有 ArgoCD Application 配置
  (`platform/apps/`、`apps/definitions/`、`environments/cloud-full/pending-definitions/`,
  以及手动 bootstrap 的 ArgoCD 自己),对每个 Helm chart 来源跑 `helm template`
  把渲染结果里的 `image:` 字段提出来。用 Python 不用 bash,原因见脚本头部注释
  (YAML 解析 + 子进程调用 + 正则提取,bash 写会更长更难读)。
  - 这是"理论上需要哪些镜像"的清单,是 git 里配置的忠实反映,不依赖当前
    集群是不是真的在跑。
  - 已知局限:有些镜像是运行时才动态决定的(比如 Strimzi 根据 Kafka CR 的
    `version` 字段选具体的 broker 镜像),静态扫描 YAML 扫不出来。
- **`scripts/export-image-cache.sh`**:上面的静态清单 **加上** 这台机器
  `docker images` 里已经缓存的全部镜像(排除两个确认无关的:
  `eipwork/etcd-host`、`eipwork/kuboard`,是这台 Mac 上其他工具留下的,不是
  这个项目的),取并集后逐个 `docker save | gzip` 导出到 `image-cache/`
  (git-ignored,二进制大文件不进仓库历史)。这个"取并集"的设计专门是为了
  补上静态扫描扫不到的运行时镜像——只要真的跑起来验证过,镜像就已经在本地
  缓存里了。
  - 幂等:已经导出过的跳过,不重复打包。
  - 顺手生成 `image-cache/manifest.txt`,记录"镜像名 -> 文件名"的映射。
- **`docker save`/`docker load` 用的是 colima 的 docker 守护进程**,不是
  `crictl`——colima 这个 profile 下 k3s 和 docker 共享同一份镜像存储(实测
  `docker images` 和 `colima ssh -- sudo crictl images` 显示同样的 IMAGE ID),
  所以 `docker save` 导出的就是 pod 实际在用的那份镜像,不用另外通过 crictl
  折腾。

## 后续使用方式(还没做,记在这里备查)

1. 把 `image-cache/` 整个目录搬到一台能连公司内网的机器上(U 盘/内网传输,
   具体方式看到时候的实际条件)。
2. `gunzip -c <文件>.tar.gz | docker load` 把镜像重新加载进那台机器的
   docker/containerd。
3. `docker tag <原名> <公司内部仓库地址>/<原名>` + `docker push`,推到公司
   内部仓库(可能就是规划中的 Harbor,也可能是先用一个更简单的仓库过渡)。
4. cloud-full 环境的 Application 配置需要把 `image.repository` 之类的字段
   改成指向公司内部仓库,而不是继续用 quay.io/ghcr.io/docker.io 这些原始地址——
   这一步目前还没设计,大概率需要给每个 Application 加一层"镜像地址前缀替换"
   的机制(Kustomize 的 image transformer,或者 Helm values 里挨个改
   `image.registry`,取决于各个 chart 支不支持整体覆盖 registry),到真正
   要接 Harbor 时再具体设计。

## 后果

- `image-cache/` 会积累到十几 GB(这台 Mac 磁盘空间够,616GB 可用,不是问题),
  长期看应该定期清理不再需要的旧版本,不属于"一直保留"的东西。
- 这份本地缓存不是 Harbor 的替代品,只是"验证过的东西不浪费"的过渡手段——
  真正的 cloud-full/prod 镜像分发,还是要靠 Phase 4 的 Harbor 常态化运行
  (pull-through cache 或者手动同步),不能长期依赖"从这台 Mac 搬文件"这种
  一次性操作。
- `list-project-images.py` 目前只扫 Helm chart 来源和纯 git manifest 来源,
  如果以后加了用 Kustomize 或者其他打包方式的组件,这个脚本要跟着扩展,
  不会自动适配。
