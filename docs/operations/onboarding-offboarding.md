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

每个操作统一成六段:**触发条件 / 影响 / 前置检查 / 操作 / 验证 / 回滚**。

---

## 接入一个新人

**触发条件**:新同事入职,或者现有同事换组。

**影响**:同步生效后这个人会拿到新组映射的全部权限(能查哪些表、能不能
进 SQL 工作台、审批链里的位置)。**加错组 = 越权**,所以"验证"那段要真的
去看他拿到了什么,不是看命令有没有报错。

**前置检查**:确认要加进哪个组 —— `platform/iam/groups.yaml` 是组的定义,
`roles.yaml` 是组 → 权限的映射。**不要凭组名猜权限。**

**操作**(两条路等价):

1. **自助申请**(推荐,门槛低):对方从门户进「权限申请门户」,提交想加入
   哪个组,管理员在同一个界面点批准 —— 批准会自动把这行写进
   `memberships.csv`、commit、push(需要 `permission-request-app-git` 这个
   Secret 配了 GIT_TOKEN,见 [ADR-032](../decisions/032-permission-request-app.md))。
2. **直接改 CSV**:在 `platform/iam/memberships.csv` 加一行
   `<username>,<组名>`,push。

**新人第一次同步必须手动跑一次**:

```bash
python3 scripts/12-sync-iam.py        # 不加 --no-create-users
```

因为 `iam-sync` 这个 CronJob 跑的是 `--no-create-users` 模式
([ADR-031](../decisions/031-iam-auto-sync-cronjob.md)),**不会建号,只会打印
警告跳过** —— 干等 CronJob 的下一次自动执行,账号永远建不出来。

**验证**:

1. Keycloak 里这个用户在预期的组里。
2. **让他真的登录一次某个组件**,确认拿到的角色是对的。加错组这件事,
   只有在这一步才会暴露 —— 2026-08-29 就实测过一次:Superset 上任何人
   登录都是 Admin,配置层面完全看不出来。

**回滚**:删掉 `memberships.csv` 里那行,push,再同步一次。组成员同步做的
是**完整对账**(不只加不减),所以删行真的会把人移出去。

## 撤销一个人的权限 / 移交

**触发条件**:离职、转岗、或者发现某人权限过大。

**影响**:**只收回组权限,不删账号。** 被移出组之后这个人**还能登录
Keycloak**,只是拿不到任何组映射的角色,各组件落到自己的最低权限默认值
(Grafana 是 `Viewer`,ArgoCD 是"零权限报错")。这是刻意的 —— 留痕、方便
审计和以后恢复,不是疏漏。

**前置检查**:一个人可能属于**多个组**,先把他所有的行都找出来:

```bash
grep "^<username>," platform/iam/memberships.csv
```

**操作**:删掉这些行,commit,push。下次同步(自动或手动跑
`scripts/12-sync-iam.py`)会把他从对应的 Keycloak group 移除。这条移除
路径用真实数据测过([ADR-028](../decisions/028-iam-org-model.md))。

**验证**:Keycloak 里这个用户的组列表为空(或只剩该保留的);让他登录
一次,确认拿到的是最低权限。

**回滚**:把删掉的行加回去,再同步。

### 连账号本身一起删(更彻底,更难撤销)

**触发条件**:离职且账号要完全失效。

**影响**:**不可撤销。** 用户 id 变了,历史留痕对不上人。

**这一步刻意没有做成声明式/自动化的** —— 删账号比"移出组"彻底得多,不该
由改一行 CSV 就自动触发。

```bash
# 登录方式和 scripts/03-configure-keycloak.sh 里的写法一致
KC_ADMIN_PW=$(kubectl -n keycloak get secret keycloak-admin -o jsonpath='{.data.password}' | base64 -d)
kubectl -n keycloak exec keycloak-keycloakx-0 -- /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080/auth --realm master --user admin --password "$KC_ADMIN_PW"

# 先查 id,确认查到的确实是要删的那个人,再删
kubectl -n keycloak exec keycloak-keycloakx-0 -- /opt/keycloak/bin/kcadm.sh get users -r platform -q username=<username>
kubectl -n keycloak exec keycloak-keycloakx-0 -- /opt/keycloak/bin/kcadm.sh delete users/<上面查到的id> -r platform
```

**验证**:`get users -q username=<username>` 返回空。

**回滚**:**没有。** 只能重新建号,原来的 user id 回不来。做之前先确认
"移出组"这条更轻的路径不够用。

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
