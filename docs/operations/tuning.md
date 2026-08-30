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

> **⚠️ 不要去改 `apps/definitions/*.yaml`。** 那些是**生成物**,下一次
> `render-environment-config.py` 一跑,改动被静默覆盖 —— 没有冲突、没有
> 报错、没有任何提示。这个坑这个仓库撞过 4 次,记在 `CLAUDE.md` 里。
>
> **这一段 2026-08-30 更正**:原文写的正是"按需要去对应组件的 Application
> yaml 直接改" —— 照着做会白改一遍。

资源规格按环境分档,声明在
[`environments/resource-profiles.yaml`](../../environments/resource-profiles.yaml)
里(ADR-059),组件源码用 `{{RES:xxx}}` 引用:

```bash
# 1. 改档位值
vi environments/resource-profiles.yaml       # 三档:local-lite / cloud-full / prod
# 2. 重新渲染 + push(ArgoCD 读的是 git 远端)
python3 scripts/render-environment-config.py cloud-full
git add -A && git commit -m "tune: 调 xxx 的内存" && git push
```

**新增一个可调规格**:在组件源码(`apps/components/*.yaml` 或
`templates/`)里写 `{{RES:新键名}}`,然后在 `resource-profiles.yaml` 的
**三档里都加**这个键 —— 少一档渲染那一档时会报错(这是有意的:
不允许"某个环境没定义就悄悄用默认值")。

`scripts/check-resource-profiles.py` 会拦住"把规格写死在组件里而不走档位"。

原则不变:**先看 `kubectl top pods` 的实际用量再调,不要凭感觉。**

> **低配额命名空间要格外小心**:RollingUpdate 需要新旧 Pod 同时占配额。
> 调大 resources 时如果新旧加起来超了命名空间的 ResourceQuota,新
> ReplicaSet 会**静默卡在 `exceeded quota`**,而 ArgoCD 显示 Synced/Healthy、
> 流量还在旧 Pod 上 —— 实测卡了一个多小时才发现。mlflow 已经改成
> `Recreate` 策略规避;其它低配额命名空间(见
> `platform/resource-quotas/manifests/quotas.yaml`)改之前先算一下。

## 权限/组织架构类

| 参数 | 文件 | 说明 |
|---|---|---|
| 谁在哪个组、组对应什么角色 | `platform/iam/{roles.yaml,groups.yaml,memberships.csv}` | 见 [ADR-028](../decisions/028-iam-org-model.md),这本身就是设计成要按公司实际组织架构改的,不是"调优"意义上的参数,是核心配置数据 |
| 自助申请门户里可以申请哪些组 | `apps/permission-request-app/src/app.py` 的 `AVAILABLE_GROUPS` | 见 [ADR-032](../decisions/032-permission-request-app.md),新增角色/组之后要记得同步加进这个列表,不然自助门户里选不到 |

## Spark 相关(设计已定,还没有真实部署场景验证)

| 参数 | 说明 |
|---|---|
| `SparkApplication.spec.timeToLiveSeconds` | 每个提交的 Spark 作业自己带,不是 spark-operator 的全局配置——见 [ADR-029](../decisions/029-spark-permissions-and-observability.md)"2026-08-12 补充"部分 |
