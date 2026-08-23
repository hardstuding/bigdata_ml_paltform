# ADR-064:按角色/组分配计算资源,支持空闲时互相借用(Kueue)

日期:2026-08-23
状态:设计已定,**尚未实现**

## 背景:现在这套和用户要的不是一回事

zhenghe 2026-08-23 提出:"不同角色资源管理(什么角色默认可以用多少资源,
甚至可以临时借用其他组的资源,CDH 就有类似的)做好没有"。

**答案是没做。** 现在只有 [ADR-041](041-queue-resource-management.md) 那套
`ResourceQuota`,粒度是**命名空间**(trino / jupyterhub / superset /
openmetadata / airflow / spark-operator / mlflow 各一份),而且它的定位在
那份文件的注释里写得很清楚:

> 这些是**保护性上限**,不是精确的容量分配承诺 …… 这里要防的是"一个组件
> 本身出问题失控增长,把整台机器拖死",不是要在多个组件之间做精确的资源仲裁。

也就是说,现有机制回答的是"**这个组件**最多能用多少",而用户问的是
"**这个组**能用多少、闲的时候能不能借别人的"。这是两个不同的问题,
前者按部署单元切,后者按组织结构切。

zhenghe 的参照物是 YARN 的队列:每个队列有保证容量(guaranteed capacity),
空闲时可以超用到最大容量(max capacity),别的队列要用时再抢占还回去。
**这是这套平台目前完全没有的能力。**

## 决策:引入 Kueue,和现有的 ResourceQuota 分层共存

### 为什么是 Kueue,不是 YuniKorn / Volcano

| | 说明 |
|---|---|
| **Kueue** | Kubernetes SIG 自己的项目(sig-scheduling)。**不替换调度器**——它是一个准入控制层,作业先进队列、拿到配额才被放行给默认调度器。`ClusterQueue` 分配配额,`Cohort` 把多个队列组成可互借的池,`borrowingLimit` 控制最多借多少。 |
| YuniKorn | 能力更接近 YARN(它本来就是从 Hadoop 生态来的),但它**替换整个调度器**。 |
| Volcano | 批处理调度能力强,同样**替换/接管调度**。 |

**决定性因素是这个集群不是我们独占的**(见 `CLAUDE.md`):Codex 那个并行
项目跑在同一个 k3s 集群的 `data-ai-platform-v2` 命名空间。替换调度器是
彻头彻尾的集群级改动,会影响它那边**所有** Pod 的调度行为——按仓库既定
规则这类事必须先停下来问,而且即便问过,为了一个配额需求去换整个集群的
调度器,风险和收益完全不成比例。

Kueue 是**加法**:装一个控制器 + 一批 CRD,作业通过 `kueue.x-k8s.io/queue-name`
标签**主动加入**才受它管;不打标签的工作负载(包括 Codex 那边的)完全不受
影响。这个隔离性是选它最主要的理由,不是因为它功能最强。

### 分层:两层各管各的,不是替换关系

```
第一层(已有,不动):namespace ResourceQuota
    管"单个组件失控时不要拖死整台机器" —— 保护性上限
第二层(新增):Kueue ClusterQueue + Cohort
    管"哪个组能用多少计算资源、闲时能借多少" —— 组织级仲裁
```

两层的对象也不一样:ResourceQuota 管**所有** Pod 含常驻服务;Kueue 只管
**批作业**(Spark / Flink / Argo Workflows / Airflow 拉起的任务 Pod)。
Trino coordinator、Superset 这类常驻服务不进队列——让一个 BI 前端排队等
配额是荒谬的,它们继续由第一层的 ResourceQuota 兜底。

### 队列按组织结构切,直接复用 platform/iam/

`platform/iam/groups.yaml` 已经有四个组:`platform-team`、`data-analysts`、
`algorithm-team`、`viewers`。**队列就按这四个组建,不另发明一套分组**——
这个仓库在权限、审批、Keycloak 同步上都已经是这套模型,资源配额再搞一套
组织结构,只会出现"权限上他属于算法组、配额上他属于另一个组"这种没人能
维护的分裂。

- `algorithm-team`:训练任务是大头,给最多
- `data-analysts`:主要是查询和小规模批处理
- `platform-team`:平台自己的运维/演示作业
- `viewers`:只读角色,**不给计算配额**(它本来就不该提交作业)

四个队列放进**同一个 Cohort**,于是空闲配额可以互相借——这就是用户要的
"临时借用其他组的资源"。`borrowingLimit` 限制单个队列最多借多少,避免一个
组把整个集群吃光;别的队列有作业进来时,Kueue 会回收借出去的配额。

### 具体数字放进 environments/resource-profiles.yaml

和 Trino/Kafka/Flink 那些一样走 `{{RES:...}}` 分档(ADR-059):local-lite
这种单机环境几乎没有可分配的余量,配额只能是象征性的;prod 才谈得上真正
的多组仲裁。**不要在 local-lite 上按生产比例配**,那样只会让所有作业都
排队排死。

## 后果与还没解决的问题

- **Airflow 拉起的任务 Pod 怎么进队列**:Airflow 的 KubernetesExecutor 直接
  建 Pod,不是 Job,Kueue 对裸 Pod 的支持需要单独开
  (`pod` integration + 命名空间选择器),这一块要实测。
- **"某个人属于哪个组"怎么传到作业上**:作业要打
  `kueue.x-k8s.io/queue-name` 标签才受管。谁提交的作业该打哪个队列的标签,
  需要在 `platform_sdk.submit_job()` 这一层根据提交者的组自动填,而不是
  让人手写——手写就一定会有人填错队列去占别人的配额。这条是这个设计里
  **最容易被做成摆设**的地方。
- **借用行为要能看见**:借了多少、被抢占了几次,如果没有看板,用户只会
  感觉"我的任务有时候慢有时候快"。Kueue 有 Prometheus 指标,要接进现有的
  Grafana(平台已经有 kube-prometheus-stack)。
- 引入 CRD 是集群级对象。虽然对 Codex 那边的工作负载无影响(不打标签就
  不受管),但**装之前仍然要按 `CLAUDE.md` 的规则跟用户说一声**。
