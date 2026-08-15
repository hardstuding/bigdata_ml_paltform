# Backlog

新想法/顺手发现的问题,默认进这里,不自动打断 `docs/CURRENT_WORK.md`
里的当前主线。这份文件只做索引和优先级排序,不重复描述已经在别处写清楚
的内容——每一项都指向权威来源(ADR/architecture.md),不在这里复制一遍。

## P0(会阻断当前主线的,才有资格排这里)

当前没有。如果出现真实的数据风险/持续计费异常/安全问题,加在这里,
并在 `docs/CURRENT_WORK.md` 里注明"CURRENT 被 P0 阻断,原因是……"。

## P1(cloud-full 部署收尾之后,下一段专门时间做)

排序和理由见
[ADR-055](decisions/055-external-review-response-2026-08-15.md#后续明确排期不是无限期搁置)
"后续"一节,这里只列条目:

1. 破坏性操作防护补全(目前只有轻量版
   `scripts/confirm-destructive-kubectl.sh`,评审建议的完整统一 guard
   框架还没做)
2. 三个自建 Flask 工具补测试/锁依赖/单一源码(`src/` vs ConfigMap 人工
   同步的问题)
3. 环境 overlay 重构(local-lite/cloud-full/prod 真正做到改配置切换,
   不是手动 `git mv`)
4. 扩大 CI(见 ADR-055 引用的原评审 P1-3 完整清单)
5. Trino OPA 真正切换生效(需要用户在场,不是延后到"不重要",是延后到
   "需要人决策的窗口",见 ADR-051)

## P2(5 条产品主线——分析师/开发/算法/运维/管理岗的完整体验)

完整方案见 `docs/architecture.md`"Phase 4 之后"一节和原始评审
`docs/claude-improvement-recommendations-2026-08-15.md`。排序:
可靠底座(即上面 P1)→ 统一项目模型 → 分析师黄金路径 → 大数据开发
黄金路径 → 算法黄金路径 → 运维控制面+管理驾驶舱 → 新引擎评估
(ClickHouse 等)。**这五条现在都还没开始**,不要误以为在做。

## 曾经提出、明确决定不做/暂缓的

- **需求追踪矩阵**(`docs/requirements.md`,给每条用户需求分配 ID 逐条
  验收):评审建议里的一项,认可其价值,但补建这个矩阵需要回溯梳理
  ~50 篇 ADR 的历史内容,工作量本身就是一个独立任务,不现在做。如果
  以后真的再发生一次"某条需求被忘了"的事故,优先级要重新评估。
- **自建 `scripts/task-runner.sh`**(start/status/logs/stop/resume 的
  长任务管理框架):评估后判断这个项目目前是单 Claude 会话操作,Bash
  工具自带的后台任务追踪+完成通知机制已经够用,重新建一套平行机制是
  多余的封装,不做。
- **正式的多工作流并行调度表**(workstream ID/资源预算/依赖表):当前
  实际工作模式主要是单线操作,没有真的同时跑多个独立 sub-agent,先不
  建这套机制,等真的出现"经常需要精细协调 3+ 个并行工作流"的场景再说。
