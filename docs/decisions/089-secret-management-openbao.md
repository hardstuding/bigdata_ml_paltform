# ADR-089:用户凭据托管 —— OpenBao

日期:2026-08-31
状态:**第一阶段完成,2026-09-01 实机验证通过**

## 问题

zhenghe 2026-08-31 问的其实是两件事,而它们的答案应该是同一个:

1. **"生产环境的账号密码你是怎么管理的?是用了专门的组件还是?"**
2. **"有些是用户的数据库账号,还有一些工具的 token,这些用户要存在哪里?
   这样 jupyterhub、上线的代码等需要用到这些的,都能读取到"**

### 现状:第一件事有个凑合的答案,第二件事**完全是空的**

**平台自己的凭据**由 `scripts/00-generate-secrets.sh` 一次性生成,直接建成
Kubernetes Secret,组件通过 `existingSecret` / `secretKeyRef` 引用。刻意
不走 GitOps(公开仓库,密码不能进 git 历史)。

这套的真实短板,如实列:

- **没有轮换机制。** 想换一个密码得手动删 Secret 再重跑脚本,然后自己想
  清楚哪些组件要重启。
- **没有审计。** 谁在什么时候读过哪个 Secret,答不上来 —— K8s Secret 本质
  是 etcd 里的 base64,任何有 `get secret` 权限的人都能看,而且看了不留痕。
- **`secrets/generated-credentials.txt` 已经坑过一次**:它是追加的快照,
  2026-08-27 实测 42 条里 **26 条已经失效**。后来加了
  `scripts/show-credentials.sh` 直接从活集群读才算兜住。

**用户自己的凭据**——他自己的源库账号、某个外部服务的 token ——**今天没有
任何地方能放**。全仓库搜不到任何面向用户的凭据入口。一个人要在 notebook
里连自己的 MySQL,只能:写死在代码里(会进 git)、或者每次手动 `export`
(重启就没了,定时作业更拿不到)。

**这是"上线的代码能跑起来"这条能力的真实缺口**,不是锦上添花。

## 决定

### 1. 用 OpenBao,不用 Vault

HashiCorp Vault 2023 年改成 BUSL 许可证,**不再是开源软件**。OpenBao 是
Linux Foundation 旗下的开源分支,API 兼容。

这和这个项目一贯的取舍一致:Feast 的 Redis 从浮动 `7-alpine` 固定到 8.4.5,
理由之一就是 7.4.x 落在 Redis 的非开源许可证区间(见 roadmap 的版本审计
那条)。**在许可证上踩坑的代价是以后想升级却发现不能升**,而那时候已经
深度绑定了。

### 2. 两件事用同一套后端,但**分阶段迁**

- **第一阶段(现在)**:OpenBao 起来,承载**用户凭据**这个全新的、今天完全
  没有的能力。平台自己那些 Secret **原地不动**。
- **第二阶段(以后)**:把平台 Secret 迁进去(经 External Secrets Operator
  或直接改组件引用)。

**为什么不一起做**:平台 Secret 是集群跑起来的前提 —— Keycloak、Postgres、
MinIO 全靠它。迁移过程中出任何问题,整个平台起不来,而且**分不清是
OpenBao 的问题还是迁移的问题**。这个仓库因为"一次改太多、失败了分不清是
哪一步"吃过亏(见 roadmap 里 platform-runtime 切 ACR 那件事,刻意分了两轮)。

### 3. 解封:这是最难的一环,而且现在的方案**只适合开发测试**

OpenBao 每次重启都是**封印**状态,要用解封密钥解开才能服务。而这台云主机
是竞价实例,经常关机重开 —— 如果每次开机都要人工解封,整个平台就等于
"每次开机都要人来一趟",这和"一键拉起"直接冲突。

三种做法:

| 做法 | 适用 |
|---|---|
| 人工解封 | 不可接受 —— 会打断一键部署 |
| **解封密钥存进 k8s Secret,启动时自动解封** | **现在这一档** |
| 云 KMS 自动解封(阿里云 KMS / AWS KMS) | prod |

**必须说清楚现在这档牺牲了什么**:解封密钥和被它保护的数据放在同一个
集群里,**静态加密的意义大打折扣** —— 拿到 k8s Secret 读权限的人,能解开
OpenBao。

**但它仍然严格优于今天**,而这才是做这件事的理由:

| | 今天(裸 K8s Secret) | OpenBao(开发档) |
|---|---|---|
| 谁读过 | **无记录** | 审计日志,每次读都留痕 |
| 按人隔离 | 无(同命名空间全可见) | 策略模板,一个人只看得到自己的路径 |
| 轮换 | 手动删了重建 | 版本化,可回滚 |
| 静态加密 | base64,不是加密 | 加密,但密钥在隔壁(见上) |

`seal_mode` 是环境配置项:`dev-autounseal` / `kms`。prod 那档默认 `kms`,
**没配 KMS 参数会直接拒绝渲染**,不会静默退化成开发档 —— 这类"配置没配对
就悄悄降级"的坑,这个项目踩过太多次。

### 4. 认证:两条路,对应两类调用方

- **Kubernetes auth** —— 给 Pod 用(定时作业、notebook 服务端)。Pod 拿
  自己的 ServiceAccount token 换 OpenBao token,**不需要分发任何密钥**。
- **OIDC auth(Keycloak)** —— 给人用。和平台其余 14 个工具同一个身份源。

### 5. 路径和策略:隔离由 OpenBao 强制,不靠调用方自觉

```
secret/users/<username>/<名字>     只有本人能读写
secret/shared/<组名>/<名字>        组内成员能读,platform-team 能写
```

用 OpenBao 的**策略模板**(`{{identity.entity.aliases.<accessor>.name}}`)
让"一个人只能碰自己的路径"这条**由 OpenBao 自己判断**,而不是我们在 SDK
或门户里写 if 判断。理由和 ADR-051 把表级授权交给 OPA 是同一条:
**写在调用方的检查,绕过调用方就没了**。

### 6. 作业读凭据:以 owner 身份,而 owner 必须真的对得上

定时作业跑的时候没有"登录的人",它以 `job.yaml` 里的 `owner` 身份读凭据。

**这里有一个必须先解决的前提**:谁能改 `job.yaml`,谁就能把 owner 写成
别人,从而读到别人的凭据。仓库里已经有对账机制(`render-jobs.py` 拿 git
提交邮箱去 `platform/iam/employees.csv` 查),但**因为那份是占位数据
(`@example.com`),现在每次都走"拿不到身份 → 放行"**(见
production-readiness-gaps 第 7 条)。

**所以:作业读 `secret/users/<owner>/` 这条路,在 owner 对账真正生效之前
不开。** 第一阶段只开 notebook(那里的身份来自 JupyterHub,是真的)和
`secret/shared/<组名>/`(组是 Keycloak 里的真实组)。这条限制写进实现里,
不是靠文档提醒。

## 还没定的

- 平台 Secret 什么时候迁、用不用 External Secrets Operator
- 动态凭据(OpenBao 直接管数据库账号、发短期账号)—— 这是 Vault/OpenBao
  最有价值的能力,但需要目标数据库的管理员权限,等有真实源库再说

---

## 实机验证(2026-09-01,第一次真上集群)

### 验过的

**按人隔离 —— 这是整个设计的核心**:

- analyst001 用自己的 token 登录 OpenBao,**自动拿到 `group-data-analysts`
  策略**(Keycloak 组 → OpenBao 身份组的映射生效)
- 自己的凭据能写、能读、能列
- **连 platform-team 的 使用方都读不到 analyst001 的个人凭据(403)** ——
  隔离是 OpenBao 自己判断的,不是任何一处代码里的 if
- 组共享:platform-team 能写、组内成员只读(403)、别的组读不到(403)

**门户「我的凭据」页面**:存 → 列出 → 删除全通;页面上**不出现凭据的值**;
换成 platform-team 的账号打开,列表里**没有**别人的凭据 —— 门户确实是以
用户本人的身份连 OpenBao,不是用自己的高权限身份再过滤。

**自动解封**:机器被竞价回收重启之后 OpenBao 是封印的(`/v1/sys/health`
返回 503),`scripts/49` 重跑一次 —— 跳过初始化、直接用存好的密钥解封,
**全程无人工输入**。这条是整套东西能进"一键拉起"的前提。

### 只有实机才暴露的四个问题(都已修)

1. **`bao operator unseal -` 在 OpenBao 2.6.2 上不读 stdin**(Vault 支持,
   它把 `-` 当成密钥本身)。而镜像里只有 BusyBox 的 wget、没有 curl,
   BusyBox wget 又不支持 `--method=PUT`。最终走 `sys/unseal` 的 POST +
   `--post-file`,密钥全程不进命令行参数。
2. **Keycloak 的 NetworkPolicy 没放行 openbao**(后来发现 minio 也一样)。
   那份文件顶部原本写着"所有组件都经 ingress-nginx 连过来,只放行它一个就够"
   —— **这个判断今天不再成立**:有几个组件是在服务端直连的。
3. **OIDC discovery 在这套部署形态下必然失败**:token 的 `iss` 是带 NodePort
   的外部地址,而集群内连不上那个地址(CoreDNS 解析到 ingress ClusterIP,
   但 ingress 内部监听 80)。**这个仓库给 oauth2-proxy 早就解决过同一个问题**
   (`skip_oidc_discovery` + issuer 和 jwks 分开给),这里用等价做法:
   jwt 认证走 `jwks_url` + `bound_issuer`,不做 discovery。
4. **MLflow 服务名写错**(见 [ADR-087](087-feature-drift-monitoring.md) 文末)
   —— 不是 OpenBao 的问题,但同一轮验证里抓到的。

### 还是没做的(不变)

作业以 owner 身份读 `secret/users/<owner>/` 这条路**仍然没开**,理由见上面
第 6 条:owner 对账要先真的生效。第一阶段开的是 notebook(身份来自
JupyterHub,是真的)和 `secret/shared/<组名>/`(组是 Keycloak 里的真实组)。

