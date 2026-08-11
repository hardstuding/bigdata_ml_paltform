# 调优指南

这个项目给的默认值是"local-lite 单机能跑起来"的合理值,不是"每个部署场景
都最优"的值。这份文档收集**预期会被按自己情况调整**的参数,和它们分别在
哪个文件——不用翻遍 30 多个 Application yaml 才知道能改什么。

对应的文件里会有一行 `【可调参数,见 docs/operations/tuning.md】` 的注释
标出具体位置,这份文档只是给一个跨组件的总览,方便一次性了解"大概有哪些
东西可以调",具体值还是要去对应文件改。

**这份清单目前只覆盖这次会话里实际调过的参数,不是全量扫描结果**——每次
发现一个新的"这个值大概率需要按情况调"的参数,应该顺手加进来,不用等
专门做一次全量梳理。

## 会话/超时类

| 参数 | 文件 | 默认值 | 什么时候要调 |
|---|---|---|---|
| JupyterHub notebook 空闲自动关闭 | `apps/definitions/jupyterhub.yaml` 的 `cull.timeout`(秒) | 7200(2 小时) | 团队习惯不一样——写代码中途开会/思考的人多,调大;资源紧张、想更快回收,调小。`cull.every` 是检查频率,不用跟着改 |
| Keycloak 会话超时 | `scripts/03-configure-keycloak.sh` 的 `ssoSessionIdleTimeout`/`ssoSessionMaxLifespan`(秒) | 28800/86400(8/24 小时) | **这两个值目前是为 local-lite 开发联调场景放宽的,cloud-full/prod 部署前必须按公司安全基线重新评估**,不能直接照抄这个值上生产 |

## 资源(CPU/内存)类

每个组件的 Application yaml 里都有 `resources.requests`/`resources.limits`,
这次没有系统性调过(local-lite 单机资源紧张,基本是"能跑就行"的最小值),
上 cloud-full/prod 时大概率整体需要往上调,不是这次会话的重点,不在这里
逐个列——按需要去对应组件的 Application yaml 直接改,原则是先看
`kubectl top pods` 实际用量再调,不要凭感觉。

## 权限/组织架构类

| 参数 | 文件 | 说明 |
|---|---|---|
| 谁在哪个组、组对应什么角色 | `platform/iam/{roles.yaml,groups.yaml,memberships.csv}` | 见 [ADR-028](../decisions/028-iam-org-model.md),这本身就是设计成要按公司实际组织架构改的,不是"调优"意义上的参数,是核心配置数据 |
| 自助申请门户里可以申请哪些组 | `apps/permission-request-app/src/app.py` 的 `AVAILABLE_GROUPS` | 见 [ADR-032](../decisions/032-permission-request-app.md),新增角色/组之后要记得同步加进这个列表,不然自助门户里选不到 |

## Spark 相关(设计已定,还没有真实部署场景验证)

| 参数 | 说明 |
|---|---|
| `SparkApplication.spec.timeToLiveSeconds` | 每个提交的 Spark 作业自己带,不是 spark-operator 的全局配置——见 [ADR-029](../decisions/029-spark-permissions-and-observability.md)"2026-08-12 补充"部分 |
