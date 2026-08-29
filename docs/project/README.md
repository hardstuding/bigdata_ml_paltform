# 项目过程记录

这个目录**不是使用文档**。它回答的是"我们做到哪了、接下来做什么、当初为什么
那样决定",而不是"这个平台怎么用"。找使用说明请回
[`../README.md`](../README.md)。

之所以单独放一层,是因为这两类内容混在一起会互相拖累:使用文档要现在时、
要短、要能直接照着敲;过程记录要带日期、要保留当时的判断和后来的推翻。

| 文件 | 回答什么 |
|---|---|
| [capability-matrix.md](capability-matrix.md) | **五个角色今天各自真的能做什么** —— 衡量进度的权威入口。看「验证级别」那一栏,不要只看状态 |
| [current-work.md](current-work.md) | 现在唯一的主线是什么、下一步做什么 |
| [next-boot-checklist.md](next-boot-checklist.md) | 云主机下次开机要验的事,每条带判据和"失败长什么样" |
| [roadmap.md](roadmap.md) | 待办、已知差距、刻意不做的事 |
| [production-readiness-gaps.md](production-readiness-gaps.md) | 距离生产可用还差什么 |
| [phase-history.md](phase-history.md) | Phase 0-4 的组件部署与验证记录(历史) |
| [open-questions-log.md](open-questions-log.md) | 架构未决问题的完整流水,含已解决的那些当时是怎么决的 |
| [reviews/](reviews/) | 外部评审记录 |

相邻的两处:

- [`../decisions/`](../decisions/) —— ADR。**决策本身**记在那里,这里只记
  进度和过程。
- [`../journal/`](../journal/) —— 按月的排障叙事归档。
