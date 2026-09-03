# 验证记录归档(2026-08 ~ 2026-09-02)

> 这份是**历史记录**,不是待办。当时 `next-boot-checklist.md` 里逐条累积的
> 验证项,验完之后结论已经沉淀进各自的 ADR 和
> [`capability-matrix.md`](../project/capability-matrix.md);这里保留过程,
> 因为**踩坑的过程比结论更值钱** —— 好几条"上集群才暴露"的问题都记在里面。
>
> 归档的原因:那份文件长到 681 行、其中绝大部分是已完成项,已经不再是一份
> "打开就知道要做什么"的清单了。见 CLAUDE.md「状态别写两遍」。

# 下次开机要验的清单

> **先跑这个,再手点剩下的:**
>
> ```bash
> ./scripts/46-verify-p15.sh
> ```
>
> 它把下面能自动化的部分跑一遍,一条一条报 ✅/❌,最后给汇总,日志在
> `logs/verify-p15.log`。**全部跳过会退出码 2,不会被当成通过** —— 一个
> "什么都没验"的运行报告成成功,是这个项目栽过四次的那个模式。
>
> 脚本**验不了**的:用两个真实账号验越权、组权限申请的批准按钮、作业详情页
> 的外观。
>
> **SQL Lab 那条原本以为要人点,后来发现不用** —— SQL Lab 用的就是
> `Database.get_sqla_engine()` 这条路,在 pod 里用 Superset 自己的
> `override_user` 把身份放进去走的是同一份代码。`./scripts/46-verify-p15.sh
> sqllab` 会验四条:current_user 是本人、有 grant 的能查、没 grant 的被拒、
> 脱敏生效。
>
> ⚠ **别用 `flask_login.login_user` 写这类测试** —— 2026-08-30 第一次就是
> 这么写的,`current_user` 返回 `superset_service`,差点当成"impersonation
> 坏了"报出去。Superset 读的不是 `login_user` 设的那个地方。

> 这份单独成文,是因为它在 [`current-work.md`](../project/current-work.md) 里已经涨到
> 60 多行,把"现在的主线是什么"挤到看不见了 —— 而那份文件的规则是超过
> ~150 行就说明又在写日记。
>
> **每条都写死了判据和"失败长什么样"。** 后者尤其重要:这一批改动里好几处
> 的失败是**静默的**(token 没对上首页只是空着、impersonation 没生效只是
> 悄悄用了服务账号权限),不写清楚下次就会被当成"功能没做"或者"看起来没
> 问题"。


**1. SQL Lab 的 impersonation** —— ADR-084 唯一没验的一环:

```
analyst001 登录 → SQL Lab → SELECT current_user
  期望:analyst001,不是 superset_service
analyst001 → 查一张他没有 grant 的表
  期望:被拒
```

**2. 门户的角色工作台**(需要先跑一次 `scripts/00-generate-secrets.sh`
把 token 复制到 platform-portal 命名空间):

```
alice 登录门户 → 首页应出现「我的表权限」,快到期的排最前、标黄
审批人登录     → 额外出现「待我审批」,显示已等多久
两块都空着     → 多半是 token 没对上(各生成了一份而不是复制)
                 而这个失败是静默的,不会报错
```

**3. `internal-packages` 的定时发布路径** —— 手工触发验过,CronJob 按点
触发从没观察到过。

**4. 审批体验那一批**(纯 UI/逻辑改动,本地 95 个测试全绿,但没在真实
浏览器里看过):

```
页面上时间显示成本地时区、3 天内是"N 小时前"  ← JS 没跑起来的话会退回
                                                显示原始 UTC 串,不会报错
拒绝一条申请不填原因 → 应该被挡住
2 级表不写理由提交   → 应该被挡住
催办按钮 → 企微收到一条;24 小时内再点 → 429
```

**5. 门户「SQL 工作台」那张卡的地址对不对** —— 端口后缀要在 path 前面
(`…superset.<域名>:32460/sqllab/`),拼反了在 local-lite 上测不出来。

**6. 建表工具的对账 CronJob**(要先跑 `scripts/00-generate-secrets.sh` 建
`table-registration-app-internal`):

```
故意让一条登记停在 openmetadata_status=failed
→ 等 30 分钟那轮 CronJob,或手工 POST /internal/reconcile-openmetadata
→ 该行变成 ok,GET /internal/reconcile-status 的 pending 归零
```

**7. 提权路径确认已堵**(这条最值得亲手走一遍):

```
用普通账号建一张表 → 看 OpenMetadata 里的 owner 是不是登录者本人
                     (而不是表单里能填的任何人)
再用同一个账号申请这张表的权限
→ 期望:审批链里没有他自己;如果剔完没有审批人了,申请应该被**拒绝**,
        note 里写着"自己不能批自己",而不是被放行
```

验过了才能把 capability-matrix 里对应那格改掉 ——
`scripts/check-capability-matrix.py` 会拦住"没验就标 ✅"。


**8. 作业详情页 + 取消/重跑**(这条要亲手点,mock 测不出 RBAC 对不对):

```
在门户「我的作业」点一个作业名 → 详情页打开
  失败的作业:最上面应该是失败原因,不是参数
  点开某一步 → 日志加载出来(按需拉,不是一次全拉)
点「重跑」→ 跳到一个新的作业名,原来那个不动
跑着的作业点「取消」→ 变成 Failed/Error,**记录和日志还在**(不是消失)

RBAC 不对的话表现是 403,页面上是"取消失败:ApiException" —— 需要
`kubectl apply` 过新的 rbac.yaml(patch/create/pods/log 是这次新加的)
```

**越权那条要单独验一次**(用两个账号):

```
用 A 的账号访问 B 提交的作业 /job/<B的作业名>
→ 期望 404「找不到这个作业,或者它不是你提交的」
→ 不能出现 B 的作业内容,也不能出现"存在但无权"这种能用来探测名字的措辞
```

---

## 这一批最要紧的一条:groups 到底有没有传过来

上面好几条都建立在同一件事上 —— 三个 Flask 应用能不能从 access token 里
读到 `groups`。**跑 `scripts/03-configure-keycloak.sh` 之后重新登录**,然后:

```
打开门户 / 权限门户 / 建表工具
→ 页面顶部**不应该**出现黄色的 ⚠ 提示
→ 出现了就说明 groups 还是没传过来,提示里写着该怎么办
```

这个提示本身就是这一轮的产物:在这之前,"配置没配对"和"你不在任何组"
在代码里长得一模一样(`groups == []`),分不开 —— 这个项目栽过三次
(ADR-078 的 Trino group provider、Superset 的 groups scope、
permission-request-app 的 `is_approver`)。

**9. 组权限申请这条流程**(此前对所有人 403,谁都没发现):

```
用 platform-team 的账号打开权限门户
→ 顶部应出现"你在 platform-team,可以审批组权限申请"
→ 「审计」和「权限交接」两个链接应该可见
→ 点批准一条组权限申请,应该成功而不是 403
```

**10. 门户按角色显示**:

```
data-analysts 账号 → 看不到 ArgoCD / Keycloak,看得到 SQL 工作台 / Superset
platform-team 账号 → 全部可见
顶部"N 个工具"的数字应该跟着可见的走,不是总数
```

**11. 代他人建表**:

```
platform-team 账号打开建表工具 → 负责人那个框可编辑
其他账号                       → 那个框是灰的(disabled)
非平台组的人直接 POST 一个别人的名字 → 落库的负责人仍然是他自己
```

**12. 建表工具的新表单**(全部只有单元测试,一次都没在集群上跑过):

```
带字段说明和分区提交一张表
→ Trino 里 SHOW CREATE TABLE 应该能看到 COMMENT 和 partitioning
→ OpenMetadata 里字段应该带 description
勾上质量断言
→ OpenMetadata 的 Data Quality 里应该真的出现对应的 testCase
点「预览要执行的 SQL」→ 显示的那段应该和实际执行的一字不差
用非平台组账号选 2 级 → 应该被挡住,记录里写着去权限申请门户
```

**13. 作业发布那一批**(同样只有单元测试):

```
多文件:jobs/daily-order-summary 现在是 job.py + jobkit.py
→ 手工提交一次,应该能 import jobkit 而不是 ModuleNotFoundError
补数:argo submit --from cronwf/daily-order-summary -n argo-workflows \
        -p run_date=2026-08-01
→ 表里应该出现 run_date=2026-08-01 那一批,而不是今天的
```

**14. 续期和到期提醒**:

```
造一条 7 天内到期的 grant → 跑一次 /internal/reclaim-expired
→ 企微(或 echo sink)应该收到「权限即将到期」
门户首页那条应该标黄、排最前、带「续期」链接
点续期 → 权限门户里出现一条 [续期] 开头的新申请,状态是等待审批
   (**不是直接延期**)
```

---

## 脚本验不了的那三条 —— 2026-08-30 全部补验通过

`scripts/46-verify-p15.sh` 结尾列的三条"必须人点一次",这次用 API 补验完了,
方法记在这里,下次不用重新摸索:

**1. 用两个账号验越权(A 打不开 B 的作业详情)** —— 通过。
本人打开 `/job/<name>` 是 200 且能看到步骤/参数/资源;换一个用户是 404,
措辞是「找不到这个作业,或者它不是你提交的」(刻意不区分"不存在"和
"不是你的",免得拿这个接口探测别人的作业名)。日志接口
`/job/<name>/logs/<pod>` 同样:本人 200、别人 404。

**2. 组权限申请的批准按钮** —— 通过。**这条必须要真 access token**,
`X-Forwarded-Groups` 不算数(页面自己会提示"这次请求里没有访问令牌,
按组判断的功能不会生效")。做法:

```bash
# 临时给 permission-request-app 这个 client 打开 direct access grants,
# 给 platform 域的 admin 设一个临时密码,用密码模式换 token,验完关回去
kubectl -n keycloak exec <keycloak-pod> -- /opt/keycloak/bin/kcadm.sh ...
kubectl -n keycloak port-forward svc/keycloak-keycloakx-http 18084:80
curl -s -X POST http://localhost:18084/auth/realms/platform/protocol/openid-connect/token   -d grant_type=password -d client_id=permission-request-app -d client_secret=...   -d username=admin -d password=... -d scope=openid
# 拿到的 token 里应该有 groups: ["platform-team"]
curl http://localhost:18081/ -H "X-Forwarded-User: admin" -H "X-Forwarded-Access-Token: $TOK"
```

结果:platform-team 看得到「待审批:组权限申请(1 条)」和「批准/拒绝」
按钮,POST `/requests/1/approve` 返回 302 且待审批清零;非 platform-team
POST 同一个接口返回 **403**。

**3. 门户「我的作业」详情页外观** —— 通过,顺带抓到一个真 bug:
**日志显示成 bytes 的 repr**(`b'\xe5\xa4\x84...'`),中文全是转义序列。
kubernetes 客户端的 `read_namespaced_pod_log()` 默认返回的是一个 str、
内容却是 bytes 的 repr。已改成 `_preload_content=False` + 自己 decode,
并加了一条测试盯死这个参数。

**还顺带抓到**:门户首页「流作业」那栏一直显示
「读不到流作业(ForbiddenException)」—— ServiceAccount 缺
`flinkdeployments` 的读权限。页面不报错、不空白,只显示一句看起来像
"暂时没有"的话,所以一直没人发现。补了 Role/RoleBinding 之后三条流作业
(device-events-stream / inference-log / trino-audit-sink)都正常显示。

---

## 开机后必验:OpenBao 起来并能自动解封(ADR-089,**没上过集群**)

**这一批一共三步,按顺序验**:①49 起来并解封(下面)、②50 配认证和策略、
③notebook 里 `platform_sdk.secret()` 真的读得到。

### ② 认证和策略(scripts/50)

```bash
./scripts/50-configure-openbao-auth.sh
# 期望:1/7 到 7/7 全部打印成功,最后一段是 4 个身份组

# 验按人隔离**真的由 OpenBao 强制**(这是 ADR-089 的核心):
# **注意 `kubectl exec` 没有 --env 参数**(那是 kubectl run 的)。
# 先把 token 放进 Pod 的 token 文件,后面的 bao 命令就不用带凭据了 ——
# scripts/50 里也是这么做的,原因见那个文件的注释。
TOKEN=$(kubectl -n openbao get secret openbao-unseal-keys -o jsonpath='{.data.root_token}' | base64 -d)
kubectl -n openbao exec -i openbao-0 -- sh -c 'cat > /home/openbao/.bao-token' <<< "$TOKEN"
kubectl -n openbao exec openbao-0 -- bao kv put secret/users/alice/demo value=alice的
kubectl -n openbao exec openbao-0 -- bao kv put secret/users/bob/demo value=bob的
# 验完记得清掉:kubectl -n openbao exec openbao-0 -- rm -f /home/openbao/.bao-token
# 然后用 alice 的身份登录(见下面 ③),读 bob 的那条 → **期望 403**
```

### ③ 门户的「我的凭据」页面

```
浏览器打开门户 → 首页应该有「我的凭据」一栏 → 点进去
→ 添加一个:名字 demo,值随便,存到「只有我自己」
→ 列表里出现 demo,并显示 platform_sdk.secret("demo") 这行提示
→ **页面上不应该出现值本身**(设计如此,见 ADR-089)
```

**这一页最要紧的验证点不是能不能存,是「以谁的身份存」** —— 它拿的是
oauth2-proxy 传下来的用户 access token 去换 OpenBao token。验法:
用 A 账号存一条,再用 B 账号登录,B 的列表里**不该**有 A 那条。
(如果有,说明门户在用自己的高权限身份读写,越权保护整个失效。)

**可能撞到的坑**:门户的 access token 是 `platform-portal` 那个 client 签的,
`auth/jwt/role/platform-user` 的 `bound_audiences` 里得能匹配上。现在写的是
`jupyterhub,openbao,account` —— Keycloak 的 access token 默认 `aud` 是
`account`,应该能对上;对不上的话页面会直接显示那句 audience 的提示,
按提示改 scripts/50 重跑。

### ④ notebook 里读得到(这是整件事的验收标准)

```python
# 在 JupyterHub 里新起一个 notebook(必须是新起的 —— 旧的没有注入 token)
import os
print(os.environ.get("PLATFORM_OIDC_TOKEN", "")[:20], os.environ.get("PLATFORM_GROUPS"))
# → 两个都应该有值。PLATFORM_OIDC_TOKEN 空 = auth_state 没生效,查 hub 日志

import platform_sdk
platform_sdk.list_secrets()      # 应该列出自己的
platform_sdk.secret("demo")      # 应该拿到值
```

**最容易撞的两个坑,症状和原因差很远**:

1. `invalid audience` —— notebook 里的 id_token 是 **jupyterhub** 那个
   client 签的,不是 openbao 的。`auth/jwt/role/platform-user` 的
   `bound_audiences` 里必须有 `jupyterhub`。**不要往策略上查。**
2. 读不到但也不报权限错,列出来就是空的 —— 多半是**策略模板里的 accessor
   不对**:同一个人从 UI 登录(oidc)和从 SDK 登录(jwt)是两条不同的
   alias,两个 accessor 的路径都得写。scripts/50 里写了,但如果 jwt 是后
   启用的、策略没重写,就会是这个症状。重跑一次 scripts/50。

---



**这是三件新东西里风险最高的一件** —— 它进了部署主线(`bootstrap-all.sh`
第 14 步),跑不通会让一键拉起在那儿停住。

```bash
# 1. 组件本身
kubectl -n openbao get pods
# → openbao-0 应该 Running,但 **READY 是 0/1** —— 封印状态下 readiness
#   探针本来就是 false。这不是故障,别急着查。

# 2. 初始化 + 解封(幂等,重复跑安全)
./scripts/49-init-unseal-openbao.sh
# → 第一次:打印「已初始化」+「已解封」,并建出 openbao/openbao-unseal-keys
# → 之后每次:打印「已经初始化过,跳过 init」+ 解封
kubectl -n openbao get pods       # 现在应该 1/1

# 3. **关键验证:关机重开之后,不人工干预,它自己能解封**
#    这条才是这套东西成不成立的判据 —— 云主机是竞价实例,经常关机重开。
#    做法:停机 → 开机 → 直接跑 ./scripts/bootstrap-all.sh(它会跑到第 14 步)
#    → 或者只跑 ./scripts/49-init-unseal-openbao.sh
#    期望:不需要任何人工输入,最后 openbao-0 是 1/1

# 4. UI(可选)
#    /etc/hosts 加 <云主机IP> openbao.local-lite.test
#    浏览器 http://openbao.local-lite.test:32460
#    root token:kubectl -n openbao get secret openbao-unseal-keys \
#                 -o jsonpath='{.data.root_token}' | base64 -d
```

**最危险的一种状态,脚本会停下来不自动处理**:`openbao-unseal-keys` 这个
Secret 在、但 OpenBao 说自己没初始化 —— 通常意味着数据卷被换掉/清空了,
而 Secret 里还是老密钥。这时候闷头再 init 一次会**覆盖老密钥,让原数据
(如果还找得回来)永远打不开**。脚本会明确报出来并让人决定。

---

## 开机后必验:MinIO 控制台 SSO(ADR-088,**没上过集群**)

```bash
# 0. Keycloak 侧要先跑一次(建 minio client + minio-policy scope)
./scripts/03-configure-keycloak.sh

# 1. 本机 /etc/hosts 加一条(和其它工具一样,test 档没有真实域名)
#    <云主机IP>  minio.local-lite.test
# 浏览器打开 http://minio.local-lite.test:32460
# → 应该出现「用 Keycloak 登录」按钮

# 2. 用 platform-team 的账号登录 → 应该能看到全部 4 个桶
#    (lakehouse / mlflow / spark-logs / backups)
# 3. 用一个非 platform-team 账号登录(比如 analyst001)
# → **期望:登录得进去,但看不到任何桶。** 这是有意的,不是 bug ——
#   MinIO 里是 Iceberg 的 parquet 原始文件,能读桶就绕过了整套 OPA 行列级
#   权限(ADR-088)。
# → 如果它反而能看到桶,说明 policy claim 没生效或者策略配错了,**这是
#   一个真实的数据泄露**,要立刻查。

# 4. 门户上「运维」分类里应该多一张「MinIO 控制台」卡片,状态点是绿的
#   (探的是 9000 的 health,不是控制台页面本身)
```

**最容易踩的一个坑**:claim 里如果带了斜杠(`/platform-team`),MinIO 按
字面匹配策略名会**永远匹配不上,而且不报错**,只表现为"登录成功但什么桶
都看不到" —— 和上面第 3 步的正确行为长得一模一样。分辨方法:用
platform-team 账号登录,如果**它也**看不到桶,那就是这个坑,去查
`minio-policy` mapper 的 `full.path` 是不是 `false`。

---

## 开机后必验:特征漂移作业(ADR-087,**没上过集群**)

```bash
# 0. 前置:先重新训一次,模型版本上才会有 feature_baseline tag
#    (2026-08-30 之前训的版本没有它,漂移作业会明确报"算不了")
kubectl -n argo-workflows submit --from workflowtemplate/train-demo-model 2>/dev/null \
  || argo submit -n argo-workflows --from workflowtemplate/train-demo-model
#    验:MLflow 上那个新版本有 feature_baseline tag

# 1. 造一点线上流量(不然窗口内没有推理请求,作业会正常退出但什么都算不出)
kubectl -n monitoring create job --from=cronjob/goldenpath-inference drift-traffic
#    多跑几次;等一个 Flink checkpoint(60s)让它们落进 iceberg.ml.inference_log

# 2. 先 dry-run,看它算出什么
argo submit -n argo-workflows --from cronwf/feature-drift -p dry_run=1
#    期望:打印每个模型版本的窗口样本数 + 超阈值的特征
#    **探针发的是固定向量([0.1]*20),所以 PSI 会非常高** —— 那不是 bug,
#    是真的漂移(线上分布是一个常数点)。这恰恰验证了空桶那条处理是对的。

# 3. 正式跑一次,确认写进了表
argo submit -n argo-workflows --from cronwf/feature-drift
# 用 platform-team 账号查(ml 是敏感 schema):
#   SELECT model, feature_index, psi, drifted FROM iceberg.ml.feature_drift
#   ORDER BY psi DESC LIMIT 10

# 4. 验 OPA 那条收窄真的生效:feature_drift_service 不该读得了审计表
#   用 feature-drift-credentials 里的账号查 iceberg.audit.query_events
#   期望:PERMISSION_DENIED
```

---

## ~~开机后必验:统一运行时镜像切到 ACR~~ **2026-08-30 已验完(SHA 已换,见下)**

**2026-08-30 晚补**:切 ACR 那次提交自己动了 `platform_sdk/config.py`
(只是注释),于是 `check-image-tag-freshness` 把 SHA 推到了 `9aeb810a`。
**这个新 tag 没验过在不在 ACR 上** —— 开机后第一件事仍然是下面那条
"只验镜像在不在"。

`environments/<env>/config.yaml` 新增 `platform_job_image`,cloud-full 指向
`.../platform-runtime:d805b030dd0427a40a24cc22212221e3372ec9bf`。
在这之前 notebook 和定时作业用的是 `local/platform-runtime:0.1.0` ——
一个**只存在于那台云主机上、靠手工 docker build 出来的**镜像。

**验完的结果**:镜像存在且能拉;定时作业自触发跑成功、`main` 容器就是
ACR 那份;用和 singleuser 相同的镜像/环境/SA 起的 pod 里,
`platform_sdk.query()` 通、`default_job_image()` 解析正确、`submit_job()`
提交的作业跑在同一个镜像上并查到真实数据。**没验到的**:没通过 JupyterHub
的 Web 界面真的 spawn 一个 notebook(要浏览器 OIDC)。

下面的步骤保留,换 SHA 之后按同一套再走一遍。

**风险点很集中**:那个 tag 如果 ACR 上不存在(或者名字拼错),
**集群上所有 notebook 和定时作业会同时 ImagePullBackOff**。所以第一步
先只验镜像在不在,再往下走。

```bash
# 1. 先确认这个 tag 真的存在(别的都不用做,几十秒)
kubectl -n argo-workflows run pulltest --restart=Never --command \
  --image=crpi-t6h2mzjka4hzoldo.cn-hangzhou.personal.cr.aliyuncs.com/bigdata-platform/platform-runtime:d805b030dd0427a40a24cc22212221e3372ec9bf \
  -- sleep 5
kubectl -n argo-workflows get pod pulltest -w
# ImagePullBackOff -> CI 没在那个 commit 上构建过 platform-runtime。
#   换成一个更近的、确实构建过的 commit SHA(工作流是全矩阵构建,
#   任何一次触发都会连它一起建),改 environments/{cloud-full,prod}/config.yaml
#   然后 render-environment-config + render-jobs 重新生成。
kubectl -n argo-workflows delete pod pulltest

# 2. 拉取凭据。scripts/45 是从仓库内容推导命名空间的,argo-workflows 和
#    jupyterhub 都在推导结果里,但它把 secret 挂到**当时存在的**每个
#    ServiceAccount 上 —— 新建的 SA 不会自动带上,所以重跑一次。
./scripts/45-configure-acr-pull.sh

# 3. 定时作业:手动触发一个,确认能拉起来并跑完
kubectl -n argo-workflows create job --from=cronjob/... 2>/dev/null || \
  kubectl -n argo-workflows get cronworkflows
#   (CronWorkflow 不能用 create job --from;克隆一份、把 schedules 改成
#    两分钟后,做法见本文件「定时路径」那条)

# 4. notebook:JupyterHub 起一个 singleuser,确认用的是新镜像
kubectl -n jupyterhub get pods -o jsonpath='{range .items[*]}{.metadata.name} {.spec.containers[0].image}{"\n"}{end}'
#   进 notebook 跑一次 platform_sdk.query("select 1"),再 submit_job() 一个
#   最小作业 —— 验的是"交互开发和调度执行环境一致"(ADR-058)这条能力
#   本身,不是镜像能不能拉。
```

---

## ~~开机后先做:清掉 2026-08-30 验证留下的一个临时凭据~~ **2026-08-30 已做**

(密码已换成随机值,`directAccessGrantsEnabled` 确认为 `false`。下面留着做法备查。)

### 原步骤

验组权限审批按钮时,给 Keycloak `platform` 域的 `admin` 设了一个临时密码
(密码模式换 token 用),`directAccessGrantsEnabled` 当时已经关回去了,但
**密码本身还在**。开发测试环境、那个账号本来也没有已知密码,影响为零 ——
但属于"验证留下的东西",该清掉:

```bash
KC=$(kubectl -n keycloak get pod -l app.kubernetes.io/instance=keycloak \
       -o jsonpath='{.items[0].metadata.name}')
kubectl -n keycloak exec $KC -- sh -c '
/opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8080/auth \
  --realm master --user admin --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null 2>&1
UID2=$(/opt/keycloak/bin/kcadm.sh get users -r platform -q username=admin --fields id \
       | grep -o "\"[a-f0-9-]\{36\}\"" | head -1 | tr -d "\"")
/opt/keycloak/bin/kcadm.sh set-password -r platform --userid $UID2 \
  --new-password "$(openssl rand -base64 24)"
'
```

顺带确认 `directAccessGrantsEnabled` 确实是关的:

```bash
kubectl -n keycloak exec $KC -- sh -c '
/opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8080/auth \
  --realm master --user admin --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null 2>&1
CID=$(/opt/keycloak/bin/kcadm.sh get clients -r platform -q clientId=permission-request-app \
      --fields id | grep -o "\"[a-f0-9-]\{36\}\"" | head -1 | tr -d "\"")
/opt/keycloak/bin/kcadm.sh get clients/$CID -r platform --fields directAccessGrantsEnabled
'
# 期望:false
```

---

## 跑完之后

把验过的项在 [`capability-matrix.md`](../project/capability-matrix.md) 里从「未验证」
改掉,并写上日期和证据。`scripts/check-capability-matrix.py` 会拦住"没验
就标 ✅"。

**15. 新加的 audit 黄金链路探针**(**2026-08-30 实机验证通过**:
「链路 [audit] 通(0.3s):最新审计记录 1 分钟前」,门户黄金链路 7 条齐):

**上集群跑出两个真问题**,都已修:

1. 探针查了一个**不存在的列** —— 写的 `event_time`,`audit.query_events`
   的时间列叫 `event_ts`(`event_time` 是隔壁 `demo.device_events_stream`
   的列名,照抄时没核对表结构)。
2. 改完列名之后被**自己的 OPA 策略**挡住:`Cannot select from columns
   [event_ts]`。没有像 openmetadata_service / iceberg_maintenance_service
   那样放开整个 audit schema —— 按**列**开了一条窄口子(只有
   `query_events` 的 `event_ts`),多选一列立刻被拒,6 条测试守着。

原验证步骤:

```
kubectl -n monitoring get cronjob goldenpath-audit        # 应该存在
手工触发一次:kubectl -n monitoring create job --from=cronjob/goldenpath-audit t1
→ 期望输出「链路 [audit] 通(Xs):最新审计记录 N 分钟前」
→ 如果报表不存在,说明 iceberg.audit.query_events 还没建 —— 那本身就是
  审计链路没跑起来,是真阳性不是探针的问题
门户首页「黄金链路」那栏应该变成 7 条,并出现「查询留痕」
```

**16. 从查询历史自动推的血缘**(**2026-08-30 实机验证通过**:血缘接口
查到 `trino.iceberg.demo.orders -> lineage_probe_1788096845`,从查询历史
自动推出来的,没有人工声明):

**上集群跑出一个真问题**:`trino` 这个 DatabaseService 上的
`username`/`authType` 不知在哪一步丢了(scripts/29 是唯一写它们的地方,
重跑一次就补回来)。而缺了 username 的报错**极难认**:采集 Job 会吐出
**483 条 pydantic 校验错误**(绝大多数是 BigQuery/BigTable 的噪音),
真正那条 `[TrinoConnection].username Field required` 夹在中间,最后以一句
毫不相干的 `AttributeError: 'NoneType' object has no attribute 'root'`
收尾。**scripts/47 现在会先检查这两个字段,缺了就直接告诉你去跑 29。**

原验证步骤:

```bash
./scripts/47-configure-openmetadata-trino-lineage.sh   # 配采集
./scripts/48-verify-trino-lineage.sh                   # 验证
```

48 会用一条真实的 `CREATE TABLE ... AS SELECT` 制造一条**确定存在**的血缘,
跑完采集之后查血缘接口确认那条边在,最后清理临时表。

**失败长什么样**:如果报"没有从 demo.orders 指过来的边",最可能的原因是
Trino 的 `system.runtime.queries` 里已经没有那条 CTAS 了 —— 它是内存里的,
受 `query.max-history` 限制,coordinator 一重启就清空。**这是已知局限,
不是配置错了**,脚本的输出里会直接这么说。

**17. 推理留痕**(**2026-08-30 端到端实机验证通过**:接收端 202、
`iceberg.ml.inference_log` 里 request/response 成对落库、
`inference_service = demo-rf-classifier`、非 platform-team 账号
`PERMISSION_DENIED`):

**这一条上集群跑出四个真问题**,是这批里最多的:

1. 镜像还钉在 `7d42658`(改 kafka-python 之前的 commit),`/readyz` 永远
   503。修 kafka-python 那次没把 tag 跟着改。
2. `KAFKA_BOOTSTRAP_SERVERS` 写的是 `kafka-cluster-kafka-bootstrap`,
   这个集群的 Kafka 叫 `platform-kafka`。三处都错(接收端 env、接收端
   源码默认值、Flink sink 默认值)。
3. Flink sink 提交即失败:**`model` 是 Calcite 保留字**。这个文件顶部
   写着"字段名避开保留字,踩过两次,这次提前避开",还是漏了 —— 现在
   改成所有字段名一律加反引号。
4. **`kserve/agent` 这个镜像不在镜像清单里。** 它只有配了 logger 才被
   KServe 注入,躺在 `inferenceservice-config` ConfigMap 的 JSON 字符串
   里,`scripts/list-project-images.py` 扫 `image:` 行永远看不到它 ——
   于是在这台连不上 Docker Hub 的机器上直接 ImagePullBackOff,而
   "打开留痕"这个动作看起来跟镜像毫无关系。已补
   `apps/kserve-runtimes/inferenceservice-config-images.json`。

**外加一个"跑通了但没用"的问题**:留痕存下来了,但
`inference_service` 是 `http://localhost:9081/`(agent 自己的地址)、
`model` 是空 —— 查不出这次调的是哪个模型,而 ADR-085 记这份数据就是为了
特征漂移。临时起了个只打印 `ce-*` 头的接收端抓到真实头,改成
`ce-inferenceservicename`。单测没抓到是因为**夹具里的假数据比真数据
"好看"**。

原验证步骤:

```
ENABLE_PAYLOAD_LOG=1 ./scripts/11-deploy-demo-inference-service.sh
发一次推理请求
→ kubectl -n inference-log-sink logs deploy/inference-log-sink  应该看到 202
→ 等一个 checkpoint(60s)后,Trino 里:
    SELECT count(*) FROM iceberg.ml.inference_log
  应该 >= 2(一次推理产生 request + response 两条)
→ 用非 platform-team 账号查同一张表,应该被 PERMISSION_DENIED 拒
  (ml 和 audit 受同一套 OPA 保护)
```

**失败长什么样**:接收端返回 503 说明写 Kafka 失败 —— **那是有意设计的**,
返回 200 会把"留痕断了"这个唯一的信号也吞掉。

**18. 从数据目录跳去申请 + 给 OA 的治理接口**(**2026-08-30 实机验证通过**:
`/api/table-governance` 两种表名写法都返回 security_level / table_owner /
required_approval;OpenMetadata 表详情页上的 `accessRequest` 是可点的
markdown 链接;没走建表工具的表返回 404 + 说人话的原因):

**这一条上集群跑出三个真问题**:

1. **三个自建应用的镜像 tag 全都落后于源码**,`/api/table-governance`
   直接 404 —— 集群上跑的是旧代码。已加
   `scripts/check-image-tag-freshness.py` 进 CI。
2. **接口的参数格式和它自己说的不一样**:底层要 OM 的完整 FQN
   (`trino.iceberg.demo.orders`),而参数说明和 400 报错给的例子是
   `iceberg.demo.orders`。外部系统按接口自己说的调,永远只拿到 404,
   而那个 404 说的是"数据目录里查不到这张表" —— **它不报错,它撒谎**。
   改成两种都认。测不出来是因为所有相关测试都把
   `lookup_table_governance` 整个 mock 掉了,而错就藏在被 mock 的那层。
3. **「从数据目录点一下去申请」部署了但从来没工作过**:
   `PERMISSION_APP_PUBLIC_URL` 读一个 `optional: true` 的 secret key,
   而那个 key 从来没人建过 → `accessRequest` 一直是空串(比没有更糟:
   OM 上会显示一个空白字段,看起来像"这张表没法申请")。改成按环境
   渲染,`apps/table-registration-app/manifests/` 进 templates/。

原验证步骤:

```
建一张表(走建表注册工具)
→ OpenMetadata 的表详情页上应该出现「申请访问这张表」这个可点链接
  (自定义属性 accessRequest,markdown 类型,**没有二开 OM**)
→ 没出现的话先看 PERMISSION_APP_PUBLIC_URL 配了没(没配就不写链接)

curl 平台的治理接口(不需要 token):
  curl 'http://permission-request.<域名>/api/table-governance?table=iceberg.demo.orders'
→ 应该返回 security_level / table_owner / required_approval
→ 对一张没走过建表工具的表(比如 scripts/08 直接建的),应该返回 404 +
  「数据目录里查不到这张表的安全等级」,而不是 500
```

**顺带验一个隐患修复**:自定义属性的 propertyType 之前是写死的 UUID,
现在改成运行时按名字查。如果建表报 `Cannot invoke "Object.hashCode()"`
这种莫名其妙的 NPE,就是这条没生效。

**19. Iceberg 表维护作业**(**没上过集群**):

```
argo submit --from cronwf/iceberg-maintenance -n argo-workflows -p dry_run=1
→ 先用 dry-run 看它打算动哪些表(应该只有 audit / ml / demo 三个 schema,
  不该出现 tpch / tpcds)
再不带 dry_run 跑一次
→ 每张表应该报 "3/3 成功";个别失败是正常的(表正在被写),会列出来
→ 跑完在 Trino 里:SELECT count(*) FROM iceberg.audit."query_events$snapshots"
  快照数应该比跑之前少(超过 7 天的被清了)
```

**失败长什么样**:如果报 "一张表都没处理到",是 iceberg catalog 连不上,
**那是真问题**;个别动作失败(比如 optimize 报 table is being written)
不算,作业不会变红。

**20. Iceberg 备份**(**2026-08-30 实机验证通过**):

```
kubectl -n data create job --from=cronjob/iceberg-backup bk1
kubectl -n data logs job/bk1 -f
→ 实测结果:audit 镜像成功(33.43 MiB / 1239 个对象),ml 打印
  「schema ml 还不存在,跳过」并正常退出(推理留痕链路还没产出数据)
```

**这一条上集群跑出了三个只有实机才会暴露的问题**,都已修:

1. 每次都在第一条 `mc alias set` 上 **Connection refused** —— 新建 pod
   头几秒连集群内服务有概率被拒(和 postgres-backup 注释里记的是同一个
   现象),已加重试。
2. 备份路径写死错了:实际是 `lakehouse/warehouse/audit.db/`,目录名带
   `.db` 后缀。而且仓库里两条链路的 warehouse 前缀本来就不一致(审计是
   `s3a://lakehouse/warehouse`,推理留痕默认 `s3a://lakehouse/`),所以
   改成两个候选位置都找。**这个不一致本身待收敛,见 roadmap backlog。**
3. 「schema 不存在就跳过」的守卫形同虚设 —— `mc ls` 对不存在的前缀
   **退出码是 0**,得判断输出是否为空。

**记住这一档的局限**:cloud-full 的备份目的地就是同一个 MinIO ——
**验的是"备份任务能跑通",不是"数据安全了"**。
