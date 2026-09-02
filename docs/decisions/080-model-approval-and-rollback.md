# ADR-080:模型上线要经过审批,而且能回滚(C 线第一步)

日期:2026-08-28
状态:**已实机验证**(2026-08-28):训练→注册→审批→部署→真实推理→回滚守卫,全链路

## 起因:上线的是"最新的那个目录"

看 `scripts/11-deploy-demo-inference-service.sh` 时发现,它决定"部署哪个
模型"的方式是——

> 挑 MinIO 里时间戳最新的一个 model 目录

脚本注释里自己写着这是权宜之计。但代价比"不优雅"严重得多:

- **没有版本概念 ⇒ 谈不上回滚。** 线上模型出问题时,没人知道该切回哪一个;
- **没有审批 ⇒ 任何人跑一次训练,产物就自动成了"下次要上线的那个"**;
- 甚至可能上线一个**失败的或纯实验性**的训练产物,只因为它最新。

而 `docs/project/capability-matrix.md` 里"模型审批 / 灰度 / 回滚"那一格写的是 ❌ 未开始——
**这一格是准确的,但它掩盖了一件事:不是"高级功能还没做",是"基础的版本
概念都还没有"。**

## 决策:上线单位从"最新目录"换成"被批准过的注册表版本"

三个脚本合起来构成一条链:

| 脚本 | 做什么 |
|---|---|
| `41-approve-model.sh <模型> <版本> [批注]` | 给版本打审批 tag(谁批的、何时、批注)+ 把 `production` alias 指过去 |
| `11-deploy-demo-inference-service.sh` | **只部署 `production` alias 指向的版本**,而且要求它带 `approval=approved` |
| `42-rollback-model.sh <模型> [版本]` | 把 alias 切回上一个已批准版本 |

### 几个刻意的设计选择

**审批记录写在 MLflow 上,不另起一个审批系统。** 用 model version 的 tag
存"谁批的/何时/批注",用 alias 存"当前该上哪个"。和
[ADR-064](064-role-based-resource-quota.md) 里"队列按已有的组切,不另发明
组织结构"是同一个判断——多一个系统就多一份要同步、会漂移的真相。

**「批准」和「会被部署」是同一个动作的两面。** 审批时设 alias,部署时只认
alias。这样不会出现"批了但没生效"或者"没批却上线了"——那两种状态在把审批
和部署分成两套记录的设计里必然会出现。

**部署时还要再查一次 `approval=approved` 标记**,不是只看 alias。alias 是
可以手工改的;多这一道检查,手工绕过审批就会被拒。

**回滚只能回到批准过的版本。** 指定一个没批准过的版本会被拒——否则"回滚"
就成了绕过审批的后门,而回滚恰恰是最紧急、最容易图快的时刻。

**`scripts/42` 只改 alias,不自己重新部署。** 部署会重建 InferenceService、
有几十秒不可用,该由人在确认之后显式触发(脚本最后打印那条命令)。把"决定
回滚"和"执行重启"分开,是为了避免手滑跑一下就把线上服务重启了。

**审批脚本没做身份校验**,这是有意的:它需要 kubectl 权限才能
port-forward,而 kubectl 权限本身就是平台管理组才有的。再包一层登录只会多
一套要维护的凭据,真正的边界在 kubectl 那一层。审批人从 `whoami` 取并记进
tag,可追溯。

## 实机验证(cloud-full,2026-08-28)

**完整链路跑通了**:训练 → 注册 → 审批 → 部署 → 真实推理 → 回滚守卫。

| 步骤 | 结果 |
|---|---|
| `41-approve-model.sh demo-rf-classifier 1` | 打上 approval/approved_by/approved_at,alias `production` → v1 |
| `11-deploy-...sh` | 打印「将部署 demo-rf-classifier v1(批准人 使用方,时间 …)」 |
| InferenceService | Ready=True |
| **真实推理请求** | 2 条样本(20 特征)→ 返回 `[0, 1]` |
| `42-rollback` 无更老版本时 | 拒绝:「当前已经是最老的已批准版本(v1),没有可回退的了」 |
| `42-rollback ... 99`(没批准过的版本) | 拒绝:「回滚只能回到批准过的版本,否则等于绕过审批」 |

**2026-08-28 晚补验了两条更有分量的**(在有了第二个版本之后):

| 检查 | 结果 |
|---|---|
| 存在**更新但未批准**的 v2 时,`production` alias 指向 | 仍然是 **v1** —— 审批真正要挡的就是这个场景,之前只有一个版本时验不到 |
| 批准 v2 → 部署 → **回滚到 v1** → 重新部署 | Pod 里挂的 artifact 从 `m-be3f6da3…`(v2)变回 `m-7cb3161…`(v1),推理探针仍然通过 |

**回滚是真的会改变线上服务的**,不是只改了个 alias——这一点必须实测,因为
`scripts/42` 刻意不自己重新部署(见上面的设计说明),很容易误以为"切了 alias
就完事了"。

### 中途撞到两个真实缺陷,都不是这次改动引入的

**一、`model_version.source` 是逻辑地址不是存储路径。** MLflow 3.x 给的是
`models:/m-7cb31…`,KServe 的 storageUri 认不了。第一版**明确报错而不是猜一个
转换规则**,实测之后才补上正确的解法(`GET /api/2.0/mlflow/logged-models/<id>`
→ `artifact_uri`)。**这个选择得到了回报**:如果当时随便猜一个路径拼法,部署会
「成功」,然后几十秒后以 Pod 拉不到模型的形式失败——而那个报错离真正的原因隔着
好几层。

**二、`kserve-demo` 不在 MinIO 的 NetworkPolicy 白名单里。** storage-initializer
报 `Could not connect to the endpoint URL`。也就是说 ADR-035 上线之后**这条推理
链路就已经断了**,而没人发现——因为从那以后没人再跑过 `scripts/11`。

后者还暴露了检查器的一个盲区:`check-networkpolicy-consumers.py` 只扫仓库里的
manifest,而 `kserve-demo` 是脚本运行时 `kubectl` 建的,它看不到。已加
`EXTRA_CONSUMERS` 手工登记并写明盲区——**报绿而实际是断的,比没有检查器更糟,
因为它让人更信任一个并不配被信任的绿灯**。

## 灰度:实测发现这套架构不支持,改成明确拒绝

第一版把灰度做成 `scripts/11` 的一个参数
(`CANARY_PERCENT=10 ./scripts/11-...`),依据是 KServe 原生的
`canaryTrafficPercent`。**实测发现它在这个平台上不生效。**

原因是架构选择:`apps/components/kserve-resources.yaml` 里
`deploymentMode: Standard`(RawDeployment)——**刻意不装 Knative**,避免为了
推理再引入一整套 Serverless 组件。而 `canaryTrafficPercent` **依赖 Knative 的
流量切分**。

实测过程和证据:

| 检查 | 结果 |
|---|---|
| `spec.predictor.canaryTrafficPercent` | `10`(CRD 老实收下了) |
| `kubectl get revision` | **0 个**(没有 Knative) |
| `kubectl get deploy -n kserve-demo` | **1 个**(灰度应该有两个在跑) |
| Pod 里挂的模型 | `m-be3f6da3…` = **v2**,也就是新版本拿走了 **100%** |
| `inferenceservice-config` 的 `deploy` | `{"defaultDeploymentMode": "Standard"}` |

**这正是这个仓库反复吃亏的那种形态**:字段被接受、`apply` 成功、状态 Ready,
而语义完全没有实现。一个自以为在灰度、实际全量切换的上线,**比不做灰度危险
得多**——人会因为"我只放了 10%"而降低警惕。

所以改成:`scripts/11` 检测到 RawDeployment 模式时**明确拒绝** `CANARY_PERCENT`,
并说明原因。**留一个不生效的参数比没有这个参数更糟。**

### 真要做灰度,有两条路

1. **装 Knative Serving**,把 `deploymentMode` 换成 `Serverless`。代价是引入
   一整套组件(Activator/Autoscaler/网络层),而当初不装它是有意的取舍——
   要重开这个话题,得先说清楚为了灰度值不值。
2. **在入口层做流量切分**:部署成两个 InferenceService(`-v1`/`-v2`),用
   ingress-nginx 的 `canary-weight` 注解分流。不引入新组件,代价是要自己管
   两个服务的生命周期,而且"哪个是当前版本"这件事从 KServe 挪到了 Ingress。

**两条都没做。** 现在的状态是:审批和回滚可用且验证过,灰度明确不可用且会
被拒绝——这比"有一个看起来能用的灰度参数"诚实。

## 还没做的

1. **没部署验证。** MLflow 3.x 的 `model_version.source` 到底是 `s3://` 还是
   `models:/m-<id>` 这种逻辑地址,得在真集群上确认——脚本里对后一种情况是
   **明确报错**而不是猜一个转换,免得部署出一个 KServe 认不了的 storageUri
   却在几十秒后才以 Pod 启动失败的形式暴露。
2. ~~灰度还没做~~ —— **2026-08-28 补上了**(见下面"灰度"一节),但**还没在
   集群上验过**:要验得先有第二个已批准的版本。
3. **没有自动回滚。** 推理服务的健康指标(延迟/错误率)还没接进告警
   (`docs/project/capability-matrix.md` 里"推理可观测"仍是 🟡),所以回滚现在必须靠人判断。
   先有"能回滚"再谈"自动回滚"。
