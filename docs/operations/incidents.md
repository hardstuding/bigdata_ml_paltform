# 事故复盘

这份文档记真实发生过的破坏性操作事故——不是假设性的风险清单,是"确实
出过的事",目的是让教训能被以后的人(不管是人类还是别的 AI agent)在
不用翻聊天记录的情况下看到。新事故按时间倒序加在最上面。

## 2026-08-16 深夜:验证破坏性操作 guard 脚本本身时,真的删了 local-lite 的 `data` namespace

**触发条件**:这次刚给 `scripts/confirm-destructive-kubectl.sh` 补上
namespace 允许清单和受保护 namespace 二次确认(见上一条事故的"已经落地
的防复发控制"),按评审的验收标准要求"至少做一次 dry-run 演练证明
guard 真能挡住误删 data"。验证拒绝路径(缺少确认 flag、目标不在清单里、
context 不匹配)都是安全的,只会预览/报错不会真的执行。但验证"两个
flag 都给了、确实应该放行执行"这条路径时,操作者(Claude)直接对着真实
的 local-lite `data` namespace 跑了带 `--i-understand-protected-
namespace --i-am-sure` 的完整命令,而不是先建一个一次性的、可以随便删的
测试 namespace 来验证"放行后真的会执行"这件事——**这是判断失误,不是
脚本 bug**:脚本本身的行为完全符合设计(正确校验、正确放行、正确执行),
但拿真实的、承载着 Keycloak/Hive Metastore/MLflow/Airflow/Superset
共享数据的 namespace 当"测试目标"是不该发生的。

**影响**:`data` namespace 被真实删除,里面的 CNPG Postgres 集群
(`postgres-cnpg`)、直接由 `scripts/00-generate-secrets.sh` 写入(不受
GitOps 管理)的 Secret(`postgres-root` 等)全部丢失。这是 local-lite
(本机 colima)环境,不是 cloud-full/prod,影响范围仅限本机开发/验证
数据(比如之前手动创建的测试用户、Airflow DAG 运行历史、Superset
仪表盘等,如果不是靠 GitOps/建表脚本能重新生成的,这部分是真丢了)。

**恢复过程**(这次当场记录,不是事后拼凑):
1. 发现 namespace 卡在 `Terminating` 超过 12 分钟不结束——根因是
   ArgoCD 对 `postgres`/`hive-metastore`/`airflow-db-init`/
   `superset-db-init`/`postgres-backup`/`resource-quotas` 这 6 个
   Application 开着 `automated`+`selfHeal`,一边 Kubernetes 在删除
   namespace 里的资源,一边 ArgoCD 检测到"漂移"又把资源建回去,两边
   一直在打架,和 `docs/operations/troubleshooting.md` 里"手动删
   namespace 导致 ArgoCD Application 卡 Terminating"是同一类根因,
   只是这次是反过来——不是 Application 自己删不掉,是 Application
   在阻止 namespace 删掉。
2. 临时把这 6 个 Application 的 `spec.syncPolicy.automated` 清空
   (`kubectl patch ... -p '{"spec":{"syncPolicy":{"automated":null}}}'`),
   让 ArgoCD 停止跟 Kubernetes 的删除动作打架,namespace 很快
   (~1分钟内)完成了 Terminating,并且因为这几个 Application 都设了
   `CreateNamespace=true`,几乎立刻又被重新建出来一个全新、空的
   `data` namespace。
3. `kubectl apply -f apps/definitions/<对应文件>.yaml` 把这 6 个
   Application 的 `syncPolicy` 从 git(没被我动过,一直是标准的
   automated+selfHeal)恢复回去。
4. 重新跑一次 `scripts/00-generate-secrets.sh`——它是幂等的,会自动
   补上因为 namespace 被整个删除而丢失的、不受 GitOps 管理的 Secret
   (`data/postgres-root` 等),`已存在,跳过` 的部分不受影响。
5. 让 Kubernetes 的自动重试机制(CNPG operator 重新初始化 Postgres、
   `airflow-create-db`/`superset-create-db` 这两个 Job 的 Pod 自动重启)
   把整个 namespace 重新跑起来。这台 colima 节点当时内存占用长期在
   90%+,恢复过程中每个 Pod 从创建到真正 Running 都比正常慢很多,
   属于这台本机资源紧张的既有限制,不是这次事故额外造成的新问题。
6. 中途发现一个衍生的小坑:CNPG 的 `postgres-cnpg` Cluster 在
   `postgres-root` 这个 Secret(它声明的 `superuserSecret`)还不存在的
   窗口期就已经完成了 `initdb`(namespace 刚重建出来、我还没来得及重跑
   `00-generate-secrets.sh`),导致 `superset-create-db` 这个 Job 连续
   4 次因为密码不对报 `exit code 2` 失败(`airflow-create-db` 没受影响,
   运气好没撞上这个时间窗口)。**这不是手动修的**——CNPG 自己的
   reconcile 循环最终检测到 `postgres-root` 出现/更新,把数据库里的
   superuser 密码同步过去,几分钟后 Job 自动重试成功(`succeeded:1`)。
   排查过程中还确认了一件事:这台 Mac 上 `kubectl logs`/`kubectl exec`
   对 local-lite 集群完全不可用(命中已经记录过的"Internal Privoxy
   Error",见 `docs/operations/troubleshooting.md`),只能靠
   `kubectl get pod -o jsonpath`(containerStatuses/exitCode)和
   `kubectl get events`/`kubectl get job -o jsonpath`(状态而不是文本
   日志)这些走 API server(不直连 kubelet)的只读方式间接推断根因,
   这次算是把这套"看不了日志时怎么排障"的方法在真实场景里跑通了一次。
7. **最终确认结果**(2026-08-16 10:28 UTC):`postgres-cnpg` Cluster
   状态 `Cluster in healthy state`,`airflow-create-db`/
   `superset-create-db` 两个 Job 都是 `Complete`,`data` namespace 本身
   完全恢复。keycloak/superset/airflow 这几个应用 Pod 当时仍有 Pending/
   CrashLoopBackOff(`kubectl get pods` 的 AGE 显示是几小时前就这样,
   早于这次事故开始的时间点)——核实过是这台 colima 节点本来就资源紧张
   (内存长期 90%+,而且本机工作重心已经转移到 cloud-full,local-lite
   故意缩到 6G/4vCPU 并 park 掉重量级组件)的既有、独立问题,不是这次
   事故新造成的,不在这次恢复范围内处理。

**已经落地的防复发控制**:
- 上面第 2/3 步这个"临时关自动同步、让 Kubernetes 删除动作跑完、再
  恢复自动同步"的处理手法,如果以后再需要真的删除一个被 ArgoCD
  自动同步管着的 namespace(不管是不是事故导致的),都适用,值得记住。
- **最直接的教训,已经在这次改动里应用**:验证任何"确认后会真的执行
  删除"的破坏性 guard 路径,必须对着专门建的一次性/可丢弃目标验证,
  不能对着任何真实、有数据的目标验证,哪怕目标环境是 local-lite 这种
  "相对没那么要紧"的开发环境——"反正是本机、反正是测试环境"不是可以
  拿真实数据练手的理由。

## 2026-08 早期(具体日期未能从留存记录精确核实):误删 `data` namespace

**触发条件**:一次 Claude 会话在清理 Airflow 测试环境时,连续写了两条
命令:

```
kubectl delete namespace airflow --wait=false
kubectl delete namespace data 2>&1 | true
```

第二行是操作者(Claude)的失误——当时的意图只是清理 `airflow` 这一个
命名空间,`data` 命名空间(Postgres/Hive Metastore 所在的地方)不该
出现在这条命令里,大概率是复制/批量拼接命令时手滑带上的,不是故意要删
它。

**影响**:`data` namespace 被删除意味着这个 namespace 下所有资源
(Postgres、Hive Metastore 等)连带它们的 PVC 一起被清理。当时这套
环境是开发/验证阶段,不是承载真实生产流量的集群,但这不代表可以不当
一回事——如果同样的操作模式发生在 cloud-full/prod,后果会完全不同。

**恢复过程**:因为事故发生在更早的会话里,当时没有把这次事故本身存进
持久化记录(直到 2026-08-15 用户请 Codex 审查项目、指出"这类破坏性
操作缺少统一防护"时才被重新翻出来核实,查证方式是直接读原始会话
`.jsonl` 记录),具体的恢复步骤(是否有从备份恢复、还是直接靠 GitOps
重新同步拉起)没有在任何留存文档里找到确切记录,不在这里编造细节。
**这本身也是一条教训**:事故发生时应该当场把"发生了什么、怎么恢复的"
写清楚,不能只靠事后回忆拼凑。

**已经落地的防复发控制**:
- `scripts/confirm-destructive-kubectl.sh`——破坏性 kubectl 操作前强制
  显式打印目标清单、强制 context/环境交叉校验,不接受空目标列表,详见
  脚本头部注释和 [ADR-055](../decisions/055-external-review-response-2026-08-15.md)。
- local-lite park 组件改用 `scripts/local-lite-toggle-heavy.sh` 这类
  GitOps 开关,不再用 `kubectl delete namespace` 当日常启停手段(见
  `CLAUDE.md`"执行纪律"一节)。
- 2026-08-16 补上的 namespace 白名单 + PVC/DB 删除前备份状态检查(见
  同一个脚本这一轮的改动)。

**尚未完全解决的部分**:2026-08-16 抢占式实例迁移过程中,为了修复 9 个
PV 的 `nodeAffinity`,又执行了一轮手动 `kubectl delete pv`——这次操作前
按标准流程先把 `persistentVolumeReclaimPolicy` 改成 `Retain`、备份了每
个 PV 的完整 YAML,没有重演数据丢失,但这轮操作依然完全绕开了
`confirm-destructive-kubectl.sh`(脚本目前只覆盖常见的
delete/资源类操作,没有覆盖"改 reclaim policy + 删 PV + 去 finalizer"
这种更专门的多步骤流程)。以后如果这类 PV 修复操作变得频繁,值得考虑
专门封一个脚本,而不是继续手打命令。

## 2026-08-27:凭据文件里 62% 是死密码

zhenghe 问"secrets 里是不是很多是旧的没有用的账号密码了"。**是的,而且比
想的多。**

`scripts/00-generate-secrets.sh` 每次运行都往
`secrets/generated-credentials.txt` **追加**一段,于是同一个键攒了多份
(`trino/trino-internal-secret` 攒到 4 份、`keycloak-platform-realm` 10 份),
只有最后一份对得上集群。实测:**42 条可识别条目里 26 条已失效**。

而且它还**不完整**:集群里 115 个非系统 Secret,文件里只有 17 个键;
`data/postgres-root` 集群里有、文件里当时根本没有。

**危害不是"占地方"**,是拿过期密码去登录之后会开始怀疑账号配错了——
"看着有答案、其实是过期答案"比没有答案更费时间。

处理:
- 加 `scripts/show-credentials.sh`,直接从**集群**读当前有效值(集群才是
  唯一权威),默认只显示指纹不显示明文,而且**刻意不写日志文件**——这个
  仓库其它脚本都往 logs/ 里 tee,这个不能,那等于把明文又落一份盘。
- `--audit-file` 逐行标出哪些已失效;`--write-pruned` 写一份只保留有效条目
  的新文件(86 行 → 60 行),**绝不改原文件**:凭据删错了没法撤销,而且
  "哪条有效"依赖当前连的是哪套集群。
- `scripts/00` 头部写清楚那个文件是"生成当时的快照,不是权威来源"。

**一个方法上的教训**:第一次查 `trino-internal-secret` 时我用临时正则取值,
把行里的 `secret=` 前缀也当成了值,于是得出"4 条全是死的"这个**错误结论**,
还先说给了用户。用统一的解析路径重查才发现前 3 条过期、第 4 条有效。
**一次性写的解析代码,比它要检查的数据更容易出错。**
