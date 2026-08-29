# 文档索引

这个目录分成四类,**按你现在想干什么找**:

| 你想…… | 看这里 |
|---|---|
| 第一次接触,想先跑通一条完整链路 | [`getting-started.md`](getting-started.md) |
| 用这个平台干活(查数、建表、跑作业、上线模型) | [`usage-guide.md`](usage-guide.md) |
| 运维它(部署、排障、备份、升级) | [`operations/`](operations/) |
| 搞清楚它为什么长这样 | [`architecture.md`](architecture.md) 和 [`decisions/`](decisions/) |

---

## 一、上手

- **[getting-started.md](getting-started.md)** —— 半小时亲手跑通
  "数据进来 → 能查 → 能看 → 能训练 → 能上线"。先有整体感觉再看细节。
- **[../examples/](../examples/)** —— 四个能直接跑的作业模板。

## 二、使用

- **[usage-guide.md](usage-guide.md)** —— 按角色组织:分析师查数、开发跑
  批/流作业、算法训模型上线、治理建表和血缘。
- **[reference/service-catalog.md](reference/service-catalog.md)** ——
  每个服务是干什么的、谁负责、坏了影响谁。**出事时先看这里**,再去
  Runbook 找处置办法。

## 三、运维

- **[operations/troubleshooting.md](operations/troubleshooting.md)** ——
  Runbook。顶部是**症状索引**(按人实际观察到的现象组织),下面按层分节,
  每条统一成"症状 → 定位 → 处置"。
- **[operations/backup.md](operations/backup.md)** —— 备份与恢复。
- **[operations/upgrade.md](operations/upgrade.md)** —— 升级流程。
- **[operations/tuning.md](operations/tuning.md)** —— 调优。
- **[operations/onboarding-offboarding.md](operations/onboarding-offboarding.md)**
  —— 人员入职/离职时权限怎么开、怎么收。
- **[operations/incidents.md](operations/incidents.md)** —— 真实发生过的
  故障记录(不是假设的演练)。
- **[operations/image-registry.md](operations/image-registry.md)** ——
  镜像仓库,以及境内环境拉镜像的已知问题。
- **[operations/cpu-architecture.md](operations/cpu-architecture.md)** ——
  CPU 架构统一的约定。
- **[operations/multi-node-rehearsal.md](operations/multi-node-rehearsal.md)**
  —— 多节点演练。

## 四、设计与取舍

- **[architecture.md](architecture.md)** —— 整体架构:分层、组件选型原则、
  三档环境。
- **[decisions/](decisions/)** —— **ADR(架构决策记录)**。这里是这个项目
  最有价值的部分:每条记的不只是"我们选了什么",还有**为什么不选另一个、
  代价是什么、后来有没有被推翻**。找具体某条看
  [decisions/README.md](decisions/README.md) 的索引。

## 五、项目进展(不是使用文档)

[`project/`](project/) 下面是**这个项目自身的过程记录**,和上面四类分开放
——它们回答的是"我们做到哪了、接下来做什么",不是"这个平台怎么用"。

- [project/capability-matrix.md](project/capability-matrix.md) ——
  **五个角色今天各自真的能做什么**。衡量标准是"某个岗位能不能独立完成一件
  真实工作",不是"部署了哪些组件"。
- [project/current-work.md](project/current-work.md) —— 当前主线。
- [project/roadmap.md](project/roadmap.md) —— 待办与已知差距。
- [project/production-readiness-gaps.md](project/production-readiness-gaps.md)
  —— 距离生产可用还差什么。
- [project/reviews/](project/reviews/) —— 外部评审记录。
- [journal/](journal/) —— 按月的排障叙事归档。

---

## 写文档的约定

1. **使用文档用现在时,不写"某天我们踩到了什么"。** 排障过程和决策变更
   属于 ADR 和 `journal/`,不属于使用说明——混在一起会让读者要先读完一段
   历史才能找到"我现在该敲什么命令"。
2. **每条断言要能被验证。** 写"已验证"就要说清楚验证方式和当时看到的
   输出;做不到就写"未验证",不要含糊。
3. **文档之间的链接用相对路径**,`scripts/check-doc-links.py` 会在 CI 里
   检查死链。
4. **生成的文档不要手改**(顶部会写明它的源在哪),改源码再重新生成,
   CI 会校验两者不漂移。
