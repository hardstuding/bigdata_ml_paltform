# 048. AI 运维角色:给 AI 一个独立身份,阶段性收紧权限,危险操作走审批链

- 状态: 已提议,设计稿,未实现(需要 zhenghe 确认"危险操作"边界和阶段
  切换时机之后再落地)

## 背景

用户提出:希望 AI(Claude Code)在这个平台里有一个"单独的角色"——开发
阶段给 AI 全权限,方便快速迭代;后续进入运维管理阶段,权限要收紧,一些
危险命令需要人类介入。用户同时问:既然 Claude Code 不能交互式输入密码,
要怎么参与运维?

先分清两件容易混在一起的事:

1. **AI 怎么执行操作**——这个从会话一开始就是"用预先配置好的服务身份
   (kubeconfig、K8s Secret 里的 API token、`kcadm` 服务账号)",不是"登录
   页面手动输密码"。这条不需要改设计,是 Claude Code 自身的安全规则(禁止
   交互式凭据输入,允许程序化使用已授权的 token),这个项目从头到尾都是
   这么运作的。
2. **平台要不要给 AI 一个正式的、和 zhenghe 本人分开的身份/权限边界**——
   这个目前是空白:AI 复用的是执行操作那台机器上已有的 kubeconfig,没有
   独立的 K8s ServiceAccount,权限边界等于"这台 colima 集群的 admin
   kubeconfig 能做什么",没有和"人类账号"区分开,也没有"阶段性收紧"这个
   机制。本 ADR 只解决第 2 件事。

## 决策

### 给 AI 一个独立的 K8s ServiceAccount,不复用 admin kubeconfig

新增 `ai-operator` ServiceAccount(建在 `platform-portal` 命名空间,和
其他平台级身份放一起)。这不是马上要求 Claude Code 切换成用这个身份
运作(现阶段本地开发,继续用现有 kubeconfig,成本效益不划算),而是先把
"AI 操作者"这个身份在 RBAC 里定义出来,为将来云端/生产阶段切换做准备——
到那个阶段,Claude Code(或者其他 AI agent)应该挂着这个身份操作,而不是
挂着人类管理员的身份。

### 两档 ClusterRole,对应"开发阶段"/"运维阶段"

复用 `platform/iam/roles.yaml` 现有的角色定义模式,新增:

```yaml
ai-operator-dev:
  description: >
    AI 运维角色·开发阶段——接近 platform-admin 的权限,可以自由创建/改/删
    大部分资源,方便快速迭代。仍然不含:删除 PVC/PV(数据丢失不可逆)、
    改 ClusterRoleBinding/RBAC 本身(防止自己给自己加权限)、访问其他
    命名空间的 Secret 明文(除非该 Secret 本来就标注给 AI 用)。
ai-operator-ops:
  description: >
    AI 运维角色·运维阶段——常规操作(读、扩缩容、重启、查日志、改
    ConfigMap)保留;delete(除了自己创建的临时调试资源)、force 类操作
    (force-push、--force 的 kubectl 命令)、跨命名空间的批量操作,都不在
    这个 ClusterRole 的权限范围内,RBAC 层面直接拒绝,不是靠"AI 自觉不做"
    这种软约束。
```

两档角色对应两个 ClusterRoleBinding,同一个 ServiceAccount,`kubectl
apply` 哪一个 Binding 决定当前生效哪一档——阶段切换是一次性的
`kubectl delete/apply`,由 zhenghe 手动执行(这本身就是一个"危险操作
边界收紧"的动作,不该由 AI 自己触发)。

### "危险命令需要人类介入" = 复用已经建好的审批链,不新起一套机制

`ai-operator-ops` 档位权限范围之外的操作,不代表"AI 永远不能做",而是
"AI 不能自己直接做,要走审批"。这条不用重新设计——
`apps/permission-request-app` 已经有一套完整的多级审批状态机
(`approval_steps`,ADR-044/045),只需要新增一种请求类型
`dangerous_operation_request`:

- AI 判断某个操作超出 `ai-operator-ops` 权限范围时,不尝试绕过 RBAC(本来
  也绕不过去),而是调用 permission-request-app 的 API 提一条
  `dangerous_operation_request`(字段:操作描述、目标资源、执行命令原文、
  为什么需要这么做),状态是 `pending`。
- 走和表访问申请一样的路由逻辑(`build_approval_steps`),默认路由给
  `DESIGNATED_ADMIN`(zhenghe)。
- zhenghe 在门户里批准后,记录里状态变成 `approved`,AI 才执行——执行本身
  还是 AI 做(有能力做,只是需要先拿到批准记录),不是审批人代替 AI 手动
  操作。
- 全部记录进 `/audit` 看板(已经存在,不需要新增审计机制),谁提的、谁批的、
  批准后实际执行了什么,完整可查。

这样"人类介入"不是靠我在对话里口头承诺"这次我会问你",是有 RBAC 硬边界
+ 有审批记录的机制,禁止走的操作在权限层面就执行不了。

## 涉及的文件(未来实现时)

- `platform/iam/roles.yaml`:新增 `ai-operator-dev`/`ai-operator-ops`
- 新增 `platform/iam/manifests/ai-operator-rbac.yaml`:ServiceAccount +
  两个 ClusterRole + 两个 ClusterRoleBinding(默认只 apply dev 档)
- `apps/permission-request-app/src/app.py`:新增
  `dangerous_operation_request` 类型(复用 `create_table_access_request`
  同一套 `build_approval_steps`/`activate_next_step` 逻辑,不重写状态机)

## 明确不做的

- 不做"AI 自己判断该走哪个权限档位并自动切换"——阶段切换(dev→ops)是
  人手动做的决定,不是 AI 自己的判断,防止"AI 自己决定收紧/放松自己的
  权限"这种循环。
- 不做细粒度到"每条 kubectl 命令单独审批"——太琐碎,不可用。审批粒度是
  "RBAC 权限范围之外的操作"这一类,不是逐条命令。
- 现阶段(本地 colima 开发)不强制切换到用 `ai-operator` 身份执行——继续
  用现有 kubeconfig,这个 ADR 是为云端/生产阶段准备的设计,不是要求
  立刻改变现在的工作方式。

## 验证

未实现,暂无验证记录。落地时需要覆盖:RBAC 边界确实生效(用
`ai-operator-ops` 身份尝试一个越权操作,确认被拒绝而不是静默失败)、
`dangerous_operation_request` 走完整审批链后 AI 能拿到批准结果并执行、
`/audit` 能看到完整记录。
