# 059. 规格分档:同一份组件定义,三个环境不同资源规格

- 状态: **机制已实现并验证(2026-08-21)**,helm 类和裸 manifest 类组件
  都已证明可覆盖(各有一个样本:Trino、Postgres HA),其余组件按需铺开。

## 背景

`CLAUDE.md` 的三条核心要求里有一条是"local-lite/cloud-full/prod 未来要能
通过改配置切换,不是维护三套手动漂移的副本"。`docs/BACKLOG.md` 1.1 把这件事
拆成两半:

- **组件选择**(这个环境要哪些组件)—— 2026-08-20 做完,`enabled_components`
- **规格分档**(同一个组件在不同环境给多少资源)—— 一直没做

没做的后果不是"不够优雅",是**"改配置就能上生产"这条承诺根本不成立**:
所有组件的 resources/副本数写死的是 local-lite 那套小规格(Trino
coordinator 300m CPU、`server.workers: 0`),把这套配置照搬到 6 台物理机的
生产硬件上,等于用一台笔记本的配置跑生产。

## 决定

复用已有的占位符渲染机制,新增 `{{RES:<key>}}` 一类占位符,值来自
`environments/resource-profiles.yaml`。

**为什么三个环境并排写在同一个文件里**,而不是各自放进
`environments/<env>/config.yaml`:

1. 并排能一眼看出差异(prod 的 Trino 到底比 local-lite 大多少),这正是
   "分档"要表达的信息;拆开三个文件反而要来回对照。
2. 更难漏配——渲染时会强制校验**三档的键集合完全一致**,给 prod 加了新
   可调项却忘了给别的环境补,任何一次渲染都会立刻报错。这条是刻意加的:
   CI 只跑 `cloud-full --check` 一档,不加这个校验的话,prod/local-lite 的
   缺键要等到真去渲染那个环境才暴露。

**缺键不给默认值兜底,直接报错退出。** 和这个项目其它地方一致——用了一个
profile 里不存在的 key,渲染直接失败,而不是留下字面量 `{{RES:xxx}}` 让它
渲染成一份语法合法、语义错误的 YAML,部署上去才发现。那正是这个仓库反复
吃亏的"看起来成功了"。

## 为什么不用别的方案

- **Kustomize overlay**:K8s 原生的正解,但引入它等于推翻 ADR-004/005 定下的
  "每个组件一个独立 ArgoCD Application + 直接写 values"这套结构,是一次
  横跨 44 个组件的重构,和这个项目"不做没法验证的大改"的原则冲突。
- **按 YAML 路径打补丁**(config 里写 `spec.source.helm.valuesObject.x.y: 值`):
  实现上要 YAML 反序列化再序列化,**会把生成产物里的注释全部冲掉**。这个
  仓库的 manifest 注释密度很高(很多是踩坑记录),`apps/definitions/` 虽然是
  生成产物但人和 ArgoCD 都会去读,丢注释的代价太大。占位符是纯字符串替换,
  注释原样保留。
- **Jinja2 之类完整模板引擎**:ADR 早就判过对这个规模过重,不重新翻案。

## 验证

用 Trino 做的样本(它三个环境的规格差异最大):同一份
`apps/components/trino.yaml`,

- cloud-full 渲染出 `workers: 0` / `cpu: 300m` / `memory: 1Gi` / 限制 `2Gi`
  —— **和改造前逐字节一致**(只多了一行注释),证明对正在运行的环境零影响
- prod 渲染出 `workers: 3` / `cpu: 2` / `memory: 8Gi` / 限制 `16Gi` /
  JVM 堆 `12g` / 查询内存 `20GB`

prod 那次是在临时 clone 里渲染验证的,不在主工作区——`apps/definitions/`
同一时刻只能代表一个环境的渲染结果,对着服务 cloud-full 的工作区渲染别的
环境会污染它(这个坑当天刚踩过一次)。

键集合一致性校验也自测过:临时给 prod 加一个别的环境没有的键,渲染立刻
报出"local-lite/cloud-full 缺 [zzz_test_key]"。

## 覆盖范围(含一次自我修正)

**初版判断是错的,已修正。** 第一版这里写的是"只能覆盖 helm 类组件(19 个),
裸 manifest 类(25 个)覆盖不到,要覆盖需要把 `apps/<x>/manifests/` 纳入
渲染管线并逐个改 Application 的 `path:`,属于架构级改动"。

写完之后重新想了一遍,发现**这个判断过重**:现有 `templates/<x>/ → 目标目录`
这套机制本来就允许"生成产物落在任意路径"。把需要分档的裸 manifest 源文件挪进
`templates/`,让渲染产物仍然落在 ArgoCD 原本读的那个路径上就行——
**Application 的 `path:` 一个字都不用改**,和 `platform/apps/*.yaml` 早就在用的
是同一个模式。

已经用 **Postgres 的 HA 副本数**验证过这条路(prod 的头号硬需求):
`templates/apps-postgres-manifests/cluster.yaml` 是源,渲染产物仍然是
`apps/postgres/manifests/cluster.yaml`。结果:cloud-full 渲染出
`instances: 1`(和改造前功能上完全一致,只多一行注释),prod 渲染出
`instances: 3`(1 主 2 备)。

所以当前的真实状态是:**机制对两类组件都成立,只是还没逐个铺开**。已覆盖的
是 Trino(资源+worker 数)和 Postgres(HA 副本数)两个样本;其余组件按需
补占位符即可,是机械工作不是设计问题。

## 后续

- 把剩下的 helm 类组件按需补上占位符(Superset/Airflow/MLflow/OpenSearch/
  Kafka/MinIO 这些资源大户优先),现在只做了 Trino 这一个样本
- 裸 manifest 类组件的覆盖方案单独立项
- prod 那一档的数字是**按方向性判断给的起步值,不是实测容量规划**,真上
  生产后要按 `kubectl top pods` 回调
