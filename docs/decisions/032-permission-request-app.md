# 032. 权限自助申请门户

- 状态: 已采纳,**核心逻辑本地测试通过,git 写权限凭据待人工配置**
  (2026-08-12)

## 背景

用户选择"自己写一个轻量级表单应用"作为权限申请的落地方式,前提是评估过
Backstage 不满足这个项目"只用官方支持的部署方式"的门槛(见对话记录/
ADR-028"后续"部分——Backstage 需要自己开发维护一个 React+TypeScript 应用
才能用,不是"装了就能用"的产品)。

## 决策

### 权限拆分,和 ADR-031 的 iam-sync CronJob 互补

这是这次两个组件放在一起设计时最重要的一点:权限申请门户**只有 git 写
权限,没有任何集群 RBAC**;iam-sync CronJob(ADR-031)**只有集群 RBAC,
没有 git 写权限**。两者合起来才能完成"申请 -> 批准 -> 写进 git -> 同步进
Keycloak"这条完整链路,但没有任何一个组件同时拿到两种能力——这个门户
被攻破,攻击者能做的最坏的事是往这一个仓库的 `platform/iam/` 目录塞
恶意 commit(还是会留下 git 历史,能追溯、能 revert),碰不到集群本身。

### 不建容器镜像仓库:ConfigMap 挂源码 + 官方 python:3.12-slim 运行时装依赖

和 `apps/iam-sync/` 是同一个模式。这个项目没有现成的容器镜像仓库/CI
流水线能 build+push 镜像(建一个是可以做的,但这次没有必要为了一个
282 行的单文件 Flask app 新增这条基础设施),ConfigMap 有 1MB 大小限制,
远够用。真长到需要拆分成多文件、装非 pip 能解决的系统依赖,才是真的需要
建镜像仓库的信号。

### 身份:自己解 access token 里的 groups claim,不改 oauth2-proxy 配置格式

`oauth2-proxy` 的 legacy config(ini 格式,ADR-019 定的)只会把
`X-Forwarded-User`/`X-Forwarded-Email` 传给上游,不会传 group 信息——
`--allowed-group`/`--oidc-groups-claim` 这类选项只影响"谁能登录"这个
门禁判断,不会把 group 转发成请求头给后端应用用。要拿到 groups 有两条路:
换成更啰嗦的 alpha config 格式(支持自定义请求头映射),或者开
`pass_access_token = true` 把整个 access token 转发给上游、由上游自己解。
选了后者——不想为了这一个组件推翻"legacy 格式够用"这个已经用在
MLflow/Spark History Server 上、工作正常的决定。app 自己 base64 解码
JWT payload 读 `groups` claim,不校验签名(信任边界是"这个 app 的
Service 只应该被 oauth2-proxy 代理到",和这个项目里其他信任反向代理注入
请求头的组件是同一个模型)。

### oauth2-proxy 这一层不设 allowed_groups

刻意的:如果卡在 oauth2-proxy 层要求必须在某个 group 里才能访问这个门户,
会出现"想申请 algorithm-team 权限,但没有 algorithm-team 权限进不了申请
页面"的死锁。谁能提申请、谁能审批,这个区分完全在 app 内部逻辑里做
(任何登录用户都能提申请,只有 platform-team 能看到/操作审批)。

### platform-team 不放进可申请列表

自助申请不能让人给自己批平台管理员权限——这个组永远只能走
`platform/iam/groups.yaml` + `memberships.csv` 手动改 + git PR review 这条
路,不接这个自助门户。

## 已经验证的部分

本地跑通了完整流程(`python3 apps/permission-request-app/src/app.py`,
用构造的假 JWT 模拟不同 group 身份的请求头):

- 普通用户提交申请 -> 记录进 SQLite,状态 `pending`
- 非 platform-team 用户尝试批准 -> 403
- platform-team 用户批准 -> 触发 `apply_to_git()`,用本地临时 git remote
  (不是真实 GitHub)验证了完整的 clone -> 改 `memberships.csv` -> commit
  -> push 链路,`git log` 确认提交真的落地、内容正确
- 没配 `GIT_TOKEN` 时批准 -> 状态变成 `approved_pending_apply`,页面上
  显示需要手动加哪一行,不是静默失败或者报 500
- 拒绝流程、重复申请去重逻辑

## 还没验证/还需要人工介入的部分

- **没有部署到集群里跑通**——只在本机直接跑 Flask 验证过逻辑,ArgoCD
  Application 还没真的 sync 过,`kubectl exec`/真实 Ingress/oauth2-proxy
  转发这条链路没有端到端测过。
- **GIT_TOKEN 这个 Secret 需要人工建**,不是任何脚本自动生成的——git 写
  权限是敏感凭据,不应该由自动化流程自己造一个塞进去。需要去 GitHub 建
  一个 fine-grained personal access token,scope 限定到这一个仓库,权限
  只给 `contents: write`,然后:
  ```
  kubectl -n permission-request-app create secret generic permission-request-app-git \
    --from-literal=token=<token>
  ```
  没建这个 Secret 之前 app 仍然完整可用,只是批准动作不会自动 push,会在
  页面提示手动加哪一行。
- 没有测试真实浏览器 SSO 登录 + 真实 groups claim(本地测试用的是手工构造
  的假 JWT,不是走完整 Keycloak 授权码流程拿到的真 token)。

## 后果

- 目前是单实例 + `ReadWriteOnce` PVC 存 SQLite,没有做高可用/备份——申请
  记录丢了不是灾难性的(git 里的 `memberships.csv` 才是权限的权威数据源,
  这个 SQLite 只是申请流程本身的工作记录),但如果这个门户以后变成核心
  工具,应该重新评估要不要接共享 Postgres(和 ADR-030 的可插拔基础设施是
  同一类考虑)。
- 没有防重复提交(同一个人对同一个组连续点提交会产生多条 `pending` 记录)
  ,不是阻塞性问题,申请人自己应该看得到重复了,如果真的造成困扰再加。
