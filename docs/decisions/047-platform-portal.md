# 047. 平台门户:统一入口页面,不是新的认证系统

- 状态: 已采纳,已实现

## 背景

用户提出:这套平台工具越来越多,希望有一个统一门户,登录一次就相当于
全部鉴权,并且能清晰看到现在有哪些工具可用。

调研确认(见对应会话记录):**SSO 免重复登录这个机制本来就已经存在**——
ArgoCD、Grafana、Trino、OpenMetadata、JupyterHub、Superset、MLflow、
Spark History Server、Argo Workflows,以及两个自建门户
(permission-request-app/table-registration-app),全部接的是同一个
Keycloak `platform` realm(`grep -rn "realms/platform"` 核实过)。真正
缺的不是认证机制,是"一个地方能看到现在有哪些工具、分别是干什么的"这个
入口本身不存在。

## 决策

### 只做"入口页面",不做新的认证层

这个门户本身也挂在同一套 oauth2-proxy/Keycloak 后面(和其他自建工具
同一个模式,ADR-032),不是另起一套账号体系或者反向代理网关。打开门户
页面本身就是"SSO 是不是真的生效"的一次现场验证——如果门户不用重新
登录就能看,点进去的其他工具也不用重新登录,这个属性是免费得到的,
不需要额外实现。

### 工具状态现场探测,不写死文字描述

这台机器按需 park/unpark 组件是常态,如果门户页面用静态文字写"现在
xx 工具是常驻的、yy 是 park 的",过几天就会和仓库实际状态脱节——这个
项目已经不止一次因为"写死的状态描述没跟上实际情况"吃过亏(见
`docs/operations/troubleshooting.md`)。门户改成每次打开页面时,服务端
现场对每个工具的集群内部地址发一个短超时的探测请求,连得上就是绿点,
连不上就是灰点,状态自己刷新,不需要人记得回来更新。

### 复用现有信息,不重新维护一份工具清单

工具的名字/分类/一句话说明,是从 `docs/architecture.md` 的组件表和已有
的 ingress 域名整理过来的,不是凭空编的——以后组件清单变化,`app.py`
里的 `TOOLS` 这个列表需要跟着手动更新,这是唯一需要人工维护的部分
(状态本身不用维护,是现场探测的)。

## 涉及的文件

- 新增 `apps/platform-portal/`(和 permission-request-app/
  table-registration-app 同一个"薄自建工具"结构)
- 新增 `apps/definitions/platform-portal.yaml` +
  `platform-portal-oauth2-proxy.yaml`
- 新增 `platform/network-policies/manifests/platform-portal.yaml`——
  这次直接照抄 permission-request-app 那份"对"的 NetworkPolicy 模式,
  不是 table-registration-app 那种漏配状态;另外给
  `permission-request-app` 命名空间补了一条 ingress 规则,放行门户的
  探活请求进来(门户要主动连它,它自己的 NetworkPolicy 默认拒绝外部
  命名空间进来)
- `scripts/00-generate-secrets.sh` / `scripts/03-configure-keycloak.sh`
  各补一段,和另外两个自建工具同一个模式

## 后果 / 明确不做的

- 不做权限差异化展示(比如"这个人只能看到他有权限的工具")——现在的
  门户对所有登录用户展示同一份清单,点进具体工具之后该工具自己的权限
  控制照常生效,门户这一层不重复判断。以后如果真的需要"千人千面"的
  工具清单,再回来加。
- 不做"一键跳转免二次确认"这类更深的集成(比如门户内嵌 iframe 直接
  操作其他工具)——每个工具还是在新标签页打开,是完全独立的应用,门户
  只负责"发现",不负责"聚合操作"。

## 验证

新组件部署+探测机制的验证记录见后续补充(如果验证时发现真实坑会记进
`docs/operations/troubleshooting.md`,不在这里重复)。
