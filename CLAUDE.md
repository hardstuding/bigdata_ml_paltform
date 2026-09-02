# 本项目的 AI 协作规则

这份文件是仓库里的、对任何 AI(Claude/Codex/其他)和人类维护者都可见的
规则——和 Claude 自己的私有 memory(`~/.claude/projects/.../memory/`)
不是一回事:私有 memory 别人看不到,换机器/换工具会丢,这份文件不会。
私有 memory 应该只存个人偏好和"去哪找权威内容"的索引,不该是唯一事实
来源。这条区分本身也是 2026-08-15 一轮外部(Codex)review 指出的问题,
背景见 [ADR-055](docs/decisions/055-external-review-response-2026-08-15.md)。

## 每次开始工作,先按顺序读

1. 这份 `CLAUDE.md`
2. [`docs/project/current-work.md`](docs/project/current-work.md) —— 现在唯一的主线
   任务是什么、下一步做什么、有没有还在跑的后台任务
3. [`docs/project/capability-matrix.md`](docs/project/capability-matrix.md) —— 五个角色今天各自真的能做什么。
   **这是"我们做到哪了"的唯一权威入口**,衡量标准是"某个岗位能不能
   独立完成一件真实工作",不是"部署了哪些组件"。判断某件事该不该做、
   该排多前,依据是它解锁哪个角色的哪条能力(见 ADR-057)。
   **看「验证级别」那一栏,不要只看状态**——它回答的是"凭什么说它可用",
   `demo`/`未验证` 和 `集成验证` 之间差着这个项目踩过的大部分坑
4. 当前主线涉及的环境状态文档(比如
   [`environments/cloud-full/STATUS.md`](environments/cloud-full/STATUS.md))
5. 主线直接引用的 ADR(索引:[`docs/decisions/README.md`](docs/decisions/README.md))

不要只信聊天记录/会话摘要——`git status`、活的集群资源状态、这几份文档,
比任何"上一轮说了什么"的回忆更权威。怀疑某段内容"是不是说过又丢了",
翻 `~/.claude/projects/<项目>/*.jsonl` 原始记录,不要凭印象回答。

## 核心要求(这三条是用户明确要求过的底线,不是建议)

- 文档和验证证据要做到"新的 AI/人类不依赖这次对话记忆也能接手"。
- 要有从空环境可恢复、可重复的一键部署路径(现状:还没有真正做到,见
  下面"已知差距")。
- local-lite/cloud-full/prod 未来要能通过改配置切换,不是维护三套手动
  漂移的副本(现状:还没有做到,见下面"已知差距")。

## cloud-full 那台云主机不是我们独占的(2026-08-22 补,踩过才写进来)

Codex 那个并行项目(`bigdata_ai_platform_v2`)**和这个平台跑在同一台云
主机的同一个 k3s 集群里**,占 `data-ai-platform-v2` 这个命名空间。这不是
"两台机器偶尔抢账号额度",是同一个 Kubernetes 控制面、同一份
`/data/k3s`。

所以下面这些动作**必须先停下来问用户**,不管看起来多像"清理我自己的
东西":

- 任何会让 k3s 本身停掉或重装的操作(`k3s-uninstall.sh`、
  `k3s-killall.sh`、重装 k3s、`rm -rf /data/k3s`)
- 云主机停机/释放
- 集群级资源的增删改(CRD、ClusterRole、ingress controller、
  cert-manager 这类跨 namespace 生效的东西)

命名空间级别的东西(自己项目自己 namespace 里的 Deployment/Service)
正常做,不用问。

**这条为什么现在才写进 CLAUDE.md**:这个事实 2026-08-16 就发现了,但只
记在 `docs/journal/2026-08.md` 和 Claude 的私有 memory 里。2026-08-22 做
"推倒重建验证"时没被想起来,跑了一次 `k3s-uninstall.sh`,把 Codex 那边
一起弄停了(数据没丢,已完整恢复,见
[ADR-039](docs/decisions/039-teardown-rebuild-test.md) 末尾)。跨项目的
硬约束躺在日记里等于不存在。

## 执行纪律

- 任何时候只有一个 `CURRENT` 主线(见 `docs/project/current-work.md`)。做当前
  主线时冒出来的新想法,默认记进 backlog,不自动切换过去做——除非它是
  会阻断当前主线的真实 P0(数据风险、持续计费、安全问题)。
- 顺手修一下如果会跨组件/改变架构/超过一小段时间,单独立项,不要塞进
  当前任务里"顺便"做了。
- 高风险/计费/不可逆操作,必须走对应的 guard/preflight
  (`scripts/confirm-destructive-kubectl.sh`、
  `scripts/cloud-full-preflight.sh`),不能图快跳过。
- 不用删除 namespace、强杀容器作为日常的组件启停手段——本地要 park 组件,
  用 `scripts/local-lite-toggle-heavy.sh` 这类 GitOps 开关,不是
  `kubectl delete namespace`。

## 多 agent 分工的边界(2026-08-21 确立)

用便宜模型执行、贵模型统筹时,**分工的切法是"谁动手"和"谁为最终结果
负责"分开**,不是"执行方什么都不许决定"。

**执行方(便宜模型)有权自己修**:遇到失败先自己诊断、自己改、自己重试,
不用每一步都回来请示。改 YAML/脚本参数、补缺失依赖、按报错调配置、重跑
——这些都自己做完再报。**硬性要求是报告里必须列出"我改了哪些文件、
分别改了什么"**,这是让放权成立的前提:判断方要能 review 之后再决定
提不提交。

**回报颗粒度**:默认精简结论 + 支撑结论的关键证据(状态值/数字)。失败
时**提取核心报错**(真正的异常 + 必要上下文)就够了,不用贴整段
日志——但不能只写"失败了"或者自己转述成"大概是网络问题",要有能让
判断方独立复核的原文片段。

**这条约定有个真实的坑,2026-08-21 实测撞到**:"执行方改仓库文件 +
`kubectl apply` 立即验证,统筹方回头再 commit"——**这套流程对 ArgoCD 管着
而且 selfHeal 生效中的资源根本走不通**。实测:改完 apply 上去,1-2 分钟内
就被 self-heal 打回原值;而且 app-of-apps 结构下,连"临时把某个 Application
的 selfHeal 关掉"这个动作本身也会被上层 `apps-root` 拉回 `true`。那次两个
真实 bug 修复(Airflow DAG 的 `default_var`、spark-operator 的 quota)都卡在
这里,执行方没有任何办法自己验证到底。

**正确的做法**:执行方改完这类资源就**停下来报告**,由统筹方立刻 commit +
push + 触发同步,再用 `SendMessage` 让同一个 agent 接着验证(它还带着上下文,
不用重开)。不要让执行方在那里反复 apply 和 self-heal 打架。判断一个资源
是不是这类:`kubectl -n argocd get applications` 里能找到管它的那个
Application,且 `syncPolicy.automated.selfHeal: true`。

**执行方不能碰的**:
- git commit / push(提交历史由统筹方统一维护)
- 计费资源开关机
- 超出自己测试产物范围的删除操作
- **架构级决策**——加新组件、加新命名空间、改 ADR 定过的取舍。撞到这类
  边界要停下来说明,不要自己拍板绕过去

**统筹方(贵模型)的核心职责不是"自己动手做完"**,是:开工前把验收标准
说清楚、事后自己核实最终状态(不轻信"跑通了")、守住上面那几条边界、
保证一键部署能力不被这次改动破坏。**判断"证据够不够"这件事不下放**
——这个项目被"看起来成功了"坑过太多次(ArgoCD Synced 不等于生效、
Pod Running 不等于健康、Job Complete 不等于业务逻辑跑对)。

## 状态别写两遍 —— 一天之内因为这个改了 7 份文档

2026-08-30 一次系统排查,在 **7 处**发现"文档写的状态和现实不符",而且
**全部是同一个成因:同一份状态被维护在两个地方**。

| 文档 | 写的 | 实际 |
|---|---|---|
| `README.md` | OpenMetadata/JupyterHub/MLflow "未部署/未启用" | 早就部署并验证过 |
| `docs/architecture.md` | "**Trino 现在零访问控制**" | OPA 从 08-16 起就生效了 |
| `docs/decisions/README.md` | 10 条"未部署验证" | 分别在 08-25~08-30 验过 |
| `production-readiness-gaps.md` | 3 条"未部署" | 落后现实四五天 |
| `docs/operations/tuning.md` | 会话超时"部署前必须重新评估" | 早就按环境分档、prod 已收紧 |
| `roadmap.md` P2/P4 | 服务目录/推理可观测/CI-CD/Flink/Spark4 "没做" | 都做完了 |
| `CLAUDE.md` 自己 | "一键部署仍是多个手动脚本" | bootstrap-all.sh 27 步 |

**其中两条会直接导致错误判断**:说"Trino 零访问控制"的架构文档,和说
三个核心组件"未部署"的 README —— 后者是任何人和 AI 第一眼看到的文件。

### 判据:发现状态过期时,先问"这份状态该不该在这里"

**"更新一遍"几乎总是错的答案** —— 它几天后会再次过期。按这个顺序选:

1. **能删掉重复的就删掉。** `architecture.md` 里"哪个环境启用了什么"那三
   列直接删了(权威在 `enabled_components`);`production-readiness-gaps.md`
   不再自己记状态(权威在 `capability-matrix.md`)。
2. **删不掉就生成。** ADR 索引的状态列改成从每份 ADR 自己的 `状态:` 行
   生成(`scripts/sync-adr-index.py`),`--check` 进 CI。
3. **两者都不行,才手写 + 加检查。** 能力表就是这一类
   (`check-capability-matrix.py` 拦住"✅ 但未验证")。

**一个需要读者"记得不要相信"的表格,不该继续存在。** `architecture.md`
那张组件表烂过两次,第一次的处理是在表上方加一段警告 —— 然后它又烂了。

### 现在有哪些检查在拦这一类

`check-capability-matrix` / `sync-adr-index --check` /
`check-bootstrap-coverage` / `check-doc-commands` /
`check-docs-dont-teach-editing-generated` / `list-manual-credentials --check`
—— 每一个都是在某次真实翻车之后加的,加的时候都当场抓到了东西。

## 非必须功能 vs 升级便利性,是要做取舍的

zhenghe 2026-08-30,谈到"在 OpenMetadata 里显示当前用户有哪些表权限"这个
需求时:

> 「这个其实暂时没有那么重要,如果涉及二开,可以不做,免得以后升级麻烦。
> **有些非必须的功能和版本升级的方便性,还是要做抉择。**」

**这不是"这一个需求不做",是一条判断规则。** 遇到"要改上游组件源码才能
做到"的需求时:

1. **先找上游留的扩展点。** OpenMetadata 的自定义属性、Superset 的
   SecurityManager、KServe 的 logger —— 这些都是官方留给使用方的口子,
   用它们等于零升级成本。这个平台已经靠这条解决过好几次
   ([ADR-086](docs/decisions/086-approval-belongs-to-oa.md) 的"申请访问"
   链接、[ADR-085](docs/decisions/085-inference-payload-logging.md) 的推理
   留痕、Superset 的角色映射)。
2. **扩展点做不到的,先问这个功能有多必要。** 不是"能不能做",是"值不值得
   为它付出以后每次升级都要重新合改动的代价"。
3. **能在别处用低得多的成本达到相近效果的,就去别处做。** 那个需求最后的
   处理是:门户首页已经有「我的表权限」,在两个地方看到同一份东西的话,
   门户这边成本低得多。

**参照物**:我们 2026-08-26 把 OpenMetadata 从 1.13.3 升到 2.0.0,是逐条
核对 breaking changes 做的。如果那时候它是个分叉版本,这次升级的代价会
高一个量级 —— 而且很可能就此不升了,然后一直停在旧版本上。

## 改文件之前,先看它是不是生成物

这个坑撞了 **4 次**(改 `platform/apps/keycloak.yaml`、
`platform/bootstrap/argocd-values.yaml`、`apps/definitions/*`、
`scripts/03-configure-keycloak.sh`),每次都白改一遍。

**症状特别隐蔽**:改完当场是对的(文件里确实是你写的内容),下一次
`render-environment-config.py` 一跑,改动被静默覆盖 —— 没有冲突、没有报错、
没有任何提示。如果中间还提交过一次,git 历史里甚至能看到"改了又没了"。

**动手之前先看文件头**:

```bash
head -3 <文件> | grep -q "自动生成\|这个文件是生成的" && echo "!! 这是生成物,去改 templates/"
```

生成物 → 源的对应关系在 `scripts/render-environment-config.py` 的 `DIR_MAP`
里。常见的几组:

| 生成物 | 源 |
|---|---|
| `apps/definitions/*.yaml` | `apps/components/*.yaml` |
| `platform/apps/*.yaml` | `templates/platform-apps/*.yaml` |
| `apps/table-registration-app/manifests/*` | `templates/apps-table-registration-app-manifests/*` |
| `platform/bootstrap/*` | `templates/platform-bootstrap/*` |
| `scripts/03-configure-keycloak.sh` 等 | `templates/scripts/*` |
| `apps/platform-jobs/manifests/*` | `jobs/`(经 `render-jobs.py`) |
| `apps/platform-streams/manifests/*` | `streams/`(经 `render-streams.py`) |
| `docs/reference/service-catalog.md` | `platform/service-catalog.yaml` |

`render-environment-config.py --check` 只在"改了源没重新渲染"时报错;
"改了生成物"它是**发现不了**的 —— 因为下一次渲染会把生成物改回去,
两边就一致了。所以只能靠动手前看一眼。

## 同步内嵌文件用 `check-embedded-scripts.py --fix`,别自己 yaml 往返

2026-08-30 撞到:给 OPA 策略加了一条规则之后,我自己写了一段
`yaml.safe_load` → 改 data → `yaml.safe_dump` 去同步 ConfigMap。结果
**389 行的块字符串被压成了一行**转义字符串 —— 内容是对的、YAML 也合法、
防漂移检查也说"一致",但一份安全策略文件从此没法读了(diff 是
`1 insertion, 379 deletions`)。

根因:rego 用 **tab 缩进**,而 YAML 块标量不允许 tab 做缩进,PyYAML 只能
退化成引号字符串。**这不是配置问题,是那种格式本来就转不过去。**

仓库里已经有正确的工具:

```bash
python3 scripts/check-embedded-scripts.py --fix
```

它是按文本行处理的,不走 YAML 往返,块字符串原样保留。**任何"把一份文件
同步进 ConfigMap"的需求都用它**,不要自己写往返代码。

## 不要用 `git checkout <文件>` 撤销临时实验

今天(2026-08-29)撞了 **2 次**,同一个形态:

```bash
# 改一下,看看检查脚本会不会报错(反证)
sed -i '' 's/.../.../' 某文件
python3 scripts/check-xxx.py     # 确认会红,好
git checkout 某文件               # ← 就是这里
```

`git checkout <文件>` 恢复的是 **HEAD 的版本**,不是"我刚才那次实验之前的
版本"。那个文件上如果还有**没提交**的正经改动(几乎总是有,因为正在写),
一并没了,而且没有任何提示。

**改成**:实验前先备份到 scratchpad,实验后拷回来。

```bash
cp 某文件 "$SCRATCH/bak"
# ...实验...
cp "$SCRATCH/bak" 某文件
```

## 程序化验证通过 ≠ 人能用

2026-09-02 发现 Superset 的 Keycloak 登录**从 08-29 起就是坏的**,而能力表
上那一格一直写着「集成验证」。用户点登录按钮 → 跳到 Keycloak → 输密码 →
认证成功 → 跳回 Superset → **又弹回登录页**。

根因是 `userinfo` 的相对路径少了一段(`get("userinfo")` 应该是
`get("openid-connect/userinfo")`),而 FAB 把这个异常吞掉,日志里一个 ERROR
都没有。

**为什么能藏一周**:此前所有"验证"都是程序化的 ——

| 验的东西 | 用的方法 | 绕过了什么 |
|---|---|---|
| Trino impersonation | `override_user` 直接构造上下文 | 整个登录链路 |
| 门户按角色显示 | 伪造 `X-Forwarded-*` 请求头 | oauth2-proxy 和 Keycloak |
| SQL Lab | permalink 接口 | 登录 |

**一次都没有真的走完登录**,而那恰恰是每个用户每天要走的第一步。

**判据**:一条能力如果"人要用它就得先登录",那么验证里**必须有一次真的
带账号密码走完登录**。`scripts/52-verify-sso-login.sh` 就是干这个的 ——
它的判据刻意不是 HTTP 200(**坏的时候也返回 200,只是又给你一张登录页**,
这正是它能藏一周的原因),而是"最终落在已登录页面、且页面里不含登录表单"。

**这条比"多写几个测试"更重要**:程序化验证是在测你写的那条路,而用户走的
是另一条。两条都要有人走过。

## 不要编辑正在运行的 shell 脚本

2026-09-01 撞到:迁可用区的脚本在后台跑着(卡在等镜像那几十分钟),我趁
这段时间去给同一个文件加功能。

**bash 是边读边执行的** —— 它记录的是文件的**字节偏移**,不是行号。在
前面插入几行,它执行完当前这段之后会从偏移量继续读,而那个位置已经指向
别的内容了。轻则语法错,重则执行出一段谁也没写过的命令。

这次没造成损坏(发现后立刻停了脚本,而正在建的镜像是云端异步操作,不受
影响),但它是纯运气 —— 当时脚本正好在一个已经被完整解析的 `for` 循环里。

**改成**:

- 要改就先停。长任务如果是云端异步的(建镜像、建实例、快照),停掉本地
  脚本不影响它继续。
- 给这类脚本留**从中间接着跑**的入口(比如 `MIGRATE_IMAGE_ID=m-xxx`),
  这样中断的代价是零。没有这个入口的话,人会因为"不想重来一遍"而选择
  在运行中改文件 —— 那正是要避免的行为。
- 真要边跑边改,改副本:`cp 脚本 /tmp/x.sh && 编辑 /tmp/x.sh`。

## 基础设施标识(实例 ID、磁盘 ID、私网 IP)只写一处

同一天抓到三处同形态的问题,都是"同一个值在多个地方各写一次":

| 值 | 写在哪 | 漏改一处的后果 |
|---|---|---|
| 实例 ID | 开机脚本 + 停机脚本 | **停机脚本停一台不存在的机器、静默成功,而真机器一直烧钱** |
| 节点私网 IP | JupyterHub 的 NetworkPolicy | notebook 起得来、查数正常,只有 `submit_job()` 超时 —— 人会去查 RBAC |
| 磁盘 ID | 迁移脚本的收尾提示 | 那行是给人复制去**执行删除**的,换台机器跑就是别人的盘 |

**判据和「状态别写两遍」那节一样**:能收敛成一处就收敛
(`environments/cloud-full/vm.env`)、能运行时查就查(`DescribeDisks`)、
能渲染就渲染(`node_private_ip` → `{{NODE_PRIVATE_IP}}`)。

**兜底值要特别小心。** 三个脚本原来都写着
`${CLOUD_VM_INSTANCE_ID:-i-0jlbped...}` —— 看着是"读不到就用默认",实际是
"配置一旦缺失,就静默地对另一台机器动手"。改成读不到直接报错退出。
**一个会安静地作用在错误目标上的兜底,比没有兜底危险。**

## 等后台任务:不要用 `pgrep -f` 判断"跑完没有"

这个坑在 2026-08 撞了 **5 次**,每次都浪费几十分钟到几小时:

```bash
until ! pgrep -f "docker build" >/dev/null; do sleep 15; done   # 永远不退出
```

`pgrep -f` 匹配的是**完整命令行**,而这条 `until` 循环自己的命令行里就含有
`docker build` 这个字符串 —— 它匹配到自己,循环永不结束。真实后果:
构建早就完成了,而等待循环还在那儿转,一转几小时;更糟的是它会让人以为
"任务还在跑",从而不去查真实状态。

**改成判断产物,不是判断进程**:

```bash
until docker images | grep -q "local/platform-runtime"; do sleep 15; done
until [ -f /path/to/output.done ]; do sleep 10; done
kubectl wait --for=condition=Ready pod/xxx --timeout=600s
```

产物判据还有一个额外好处:进程没了但**失败了**的情况,进程判据会误判成
"完成",产物判据不会。

如果实在只能看进程,排除自己:`pgrep -f "docker build" | grep -v $$`,
或者用一个不会出现在自己命令行里的模式。

**另外**:SSH 到会被关机的机器上跑等待循环,机器一停这个 SSH 会挂住不返回,
本地就留下一个永远不结束的后台任务。等云主机上的长任务,用产物判据 +
本地超时,不要把等待放在对端。

## 不能没头没尾地停

结束一段工作前,过一遍 `docs/project/current-work.md` 底部那份检查清单。达到
下面任一条件才可以停:完成(验收标准满足,状态文档更新过)、需要用户
才能做的决策(给出选项和推荐,不是把普通技术判断也推给用户)、卡在
只有用户能解决的权限/凭据上、继续做下去会有不可接受的风险。"已经做了
不少""发现了别的有意思的东西"不算合法的停止理由。

## 已知差距(如实记录,不是没人管的隐藏债务)

见 [ADR-055](docs/decisions/055-external-review-response-2026-08-15.md)
和 [`docs/project/roadmap.md`](docs/project/roadmap.md) 的完整清单。

**这一段 2026-08-30 核对过一次**,原来点名的三条里有两条已经不成立了 ——
如实更新,不留假的债务(留着的后果是有人去做一件已经做完的事):

- ~~一键部署仍是多个手动脚本按顺序跑~~ → `scripts/bootstrap-all.sh` 串起
  27 步,幂等、失败非零退出、出部署报告;
  `scripts/check-bootstrap-coverage.py` 保证它和文档的部署主线表两边一致。
  从零拉起的完整说明:
  [`docs/operations/deploy-from-scratch.md`](docs/operations/deploy-from-scratch.md)。
- ~~三个自建 Flask 工具没有自动化测试~~ → 门户 105 / 权限门户 114 /
  建表 75 条测试,全部在 CI 里。
- **源码和 ConfigMap 靠人工同步** —— 这条仍然成立,但已经有两层网:
  `check-embedded-scripts.py`(ConfigMap 里的副本)和
  `check-duplicated-sources.py`(应用 `src/` 里的副本)。
- **环境切换**已经是声明式的(`enabled_components` + `resource-profiles.yaml`),
  但**三档环境只有 cloud-full 真的跑过**,prod 那档从没部署过。

**真正还剩的大缺口**:没上过生产(能力表里零个"生产验证"),门禁条件见
[`docs/project/production-readiness-gaps.md`](docs/project/production-readiness-gaps.md)。
