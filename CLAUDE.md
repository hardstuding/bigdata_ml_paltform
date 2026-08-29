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
   该排多前,依据是它解锁哪个角色的哪条能力(见 ADR-057)
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
和 [`docs/project/roadmap.md`](docs/project/roadmap.md) 的完整清单,这里只点名最重要
的几条:一键部署目前仍是多个手动脚本按顺序跑;三个自建 Flask 工具没有
自动化测试、源码和 ConfigMap 靠人工同步;环境切换靠手动 `git mv` 加手调
资源,不是声明式的。
