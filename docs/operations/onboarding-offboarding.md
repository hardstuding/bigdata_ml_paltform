# 人员权限的接入 / 移交 / 撤销

这份文档回答"新人怎么拿到权限、人离开/换角色时怎么把权限收回"——机制本身
在 ADR-028(组织架构模型)/ADR-031(自动同步)/ADR-032(自助申请门户)
里都各自记录过决策理由,这里只讲"要做的时候具体怎么操作"。

## 权限模型回顾

一个人在这个平台上的权限 = 他在 `platform/iam/memberships.csv` 里属于
哪些 Keycloak group(`platform/iam/groups.yaml` 定义了 group,
`roles.yaml` 定义了 group → 具体权限的映射)。ArgoCD/Grafana/JupyterHub/
MLflow/Trino/Superset/OpenMetadata/Argo Workflows 这些接了 SSO 的组件,
都是按登录用户所在的 Keycloak group 映射角色,不是给每个组件单独开账号。

`platform/iam/memberships.csv` 是唯一的事实来源(source of truth)——改
这个文件、push,`iam-sync` 这个 CronJob 每 5 分钟会自动把差异同步进
Keycloak(ADR-031),也可以手动立刻跑 `python3 scripts/12-sync-iam.py`
不用等。

## 接入一个新人(onboarding)

两条路径都行,效果一样:

1. **自助申请**(推荐,门槛低):对方打开
   `permission-request.local-lite.test`,提交想加入哪个 group,管理员
   在同一个界面点批准——批准动作会自动把这行加进
   `memberships.csv`、commit、push(需要 `permission-request-app-git`
   这个 Secret 配了 GIT_TOKEN,见 ADR-032)。
2. **直接改 CSV**:管理员自己在 `platform/iam/memberships.csv` 里加一行
   `<username>,<group名字>`,提交 PR 或直接 push(取决于仓库权限模型)。

首次同步时如果这个用户在 Keycloak 里还没有账号,`scripts/12-sync-iam.py`
会顺带建号(临时密码,见脚本里的说明);`iam-sync` 这个 CronJob 跑的是
`--no-create-users` 模式(见 ADR-031),不会自动建号,只会打印警告跳过
——**新人的第一次同步,建议管理员手动跑一次
`python3 scripts/12-sync-iam.py`(不加 `--no-create-users`),而不是干等
CronJob 的下一次自动执行**,因为自动执行那次不会真的建出账号。

## 撤销一个人的权限(offboarding / 移交)

在 `platform/iam/memberships.csv` 里删掉这个人对应的行(可能不止一行,
一个人可以属于多个 group),commit、push。下次同步(自动或手动跑
`scripts/12-sync-iam.py`)会把这个人从对应的 Keycloak group 里移除——
`scripts/12-sync-iam.py` 对 group 成员做的是**完整对账**,不只加不减
(和 role 定义本身"只加不减"是两种不同的策略,分别在 ADR-028 里说明过
理由),这条移除路径已经用真实数据测试过(见 ADR-028)。

**注意这只是收回 group 权限,不是删掉 Keycloak 账号本身**:被移除
group 之后,这个人的登录账号还在,还能登录 Keycloak,只是拿不到任何
group 映射的角色,各组件会落到各自的匿名/最低权限默认值(比如 Grafana
是 `Viewer`,ArgoCD 是"零权限报错",见各组件配置里
`role_attribute_path` 的说明)——这是刻意的默认行为(留痕、方便审计/
以后恢复),不是疏漏。如果确实需要连账号本身都删掉(比如离职、账号要
完全失效),需要额外手动操作:

```bash
# 登录方式和 scripts/03-configure-keycloak.sh 里的写法一致
KC_ADMIN_PW=$(kubectl -n keycloak get secret keycloak-admin -o jsonpath='{.data.password}' | base64 -d)
kubectl -n keycloak exec keycloak-keycloakx-0 -- /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080/auth --realm master --user admin --password "$KC_ADMIN_PW"

# 找到这个用户的 user id,再删掉
kubectl -n keycloak exec keycloak-keycloakx-0 -- /opt/keycloak/bin/kcadm.sh get users -r platform -q username=<username>
kubectl -n keycloak exec keycloak-keycloakx-0 -- /opt/keycloak/bin/kcadm.sh delete users/<上面查到的id> -r platform
```

这一步没有做成声明式/自动化的(删账号是比"移出 group"更彻底、更难撤销
的操作,不应该由改一行 CSV 就自动触发),需要人手动确认执行。

## 这套机制覆盖不到的部分

- **GitHub 仓库本身的访问权限**(谁能 push/merge/管理仓库设置):这是
  GitHub 自己的仓库/组织权限管理,不归 `platform/iam/` 这套 Keycloak
  同步管——移交项目时这部分要单独在 GitHub 上处理(仓库 Settings →
  Collaborators,或者如果挂在组织下,走组织的成员管理)。
- **`permission-request-app-git` 这个 GIT_TOKEN**:是应用级别的共享凭据
  (代表"这个应用有权限往仓库写东西"),不是某个具体人的权限,人员变动
  不影响它,但如果 token 所有者(创建这个 fine-grained PAT 的那个
  GitHub 账号)离职/权限被收回,这个 token 会跟着失效,需要用新账号
  重新生成一份,见 README 里 GIT_TOKEN 的配置说明。
- **这台本机(colima)的直接访问权限**:local-lite 阶段整个集群跑在
  一台 Mac 上,`kubectl`/`colima ssh` 这类直接的宿主机/集群访问权限
  没有做任何隔离——能碰这台机器的人就有集群管理员权限,这是
  local-lite 阶段的现实(单机开发验证环境),不是设计成多人共享的生产
  访问模型。cloud-full/prod 阶段用真实多节点集群时,需要重新设计这部分
  (比如按 RBAC 给 kubeconfig 分级,不能继续假设"能碰机器=管理员")。
