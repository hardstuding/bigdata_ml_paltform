# 贡献指南

这个项目对人类和 AI Agent 是同一套贡献方式——GitOps 即操作接口(见
[ADR-005](docs/decisions/005-argocd-gitops.md)):改 YAML、提交 PR,
不存在"只有维护者能改的隐藏配置"。这份指南同样适用于两者。

## 开始之前

1. 通读 [README.md](README.md),尤其是"这个项目提供什么"和"仓库结构"
   两节,搞清楚这不是 CDH 的复刻品、也不是一次性 demo。
2. 看一眼 [`docs/architecture.md`](docs/architecture.md) 的路线图,
   确认你想做的事情属于哪个 Phase、有没有已经记录在"还没定的事"里的
   已知考量。
3. 遇到具体报错先查 [`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md)
   ——这个仓库的风格是把真实踩过的坑都记下来,很多"看起来是新问题"的
   报错其实已经有记录。

## 这个项目的几条硬性原则(提 PR 前请对照检查)

见 [`docs/architecture.md`](docs/architecture.md) 的"设计原则"一节,
这里挑几条最容易在评审时被卡住的:

- **只用官方支持的部署方式**(见 [ADR-008](docs/decisions/008-avoid-bitnami.md)):
  不引入 Bitnami 或来源不明的社区 chart。组件优先用官方 Helm chart;
  没有官方 chart 的,写裸 manifest(参考 `apps/hive-metastore/`、
  `platform/coredns-custom/` 这类现有例子的组织方式),不要为了图方便
  引入一个维护状况不明的第三方 chart。
- **每个非显而易见的技术选择都要有 ADR**:新增一个组件、做一个有取舍
  的技术决策,在 `docs/decisions/` 下加一篇编号递增的 ADR,写清楚为什么
  这么做、考虑过什么替代方案、有什么后果——不是"我们就是这么做的",是
  "为什么这么做"。看现有任意一篇 ADR 的结构照着写。
- **不做没法验证的东西**:声明式配置写完不算完成,要有真实验证记录
  (真实调用过 API、真实查过数据、真实触发过失败场景)。纯理论上"应该
  能跑"但没有实测过的改动,PR 描述里要明确说清楚"这部分没有条件验证"
  ,不要含糊带过。
- **组件独立可升级**:每个组件是 ArgoCD 里独立的 Application,禁止用
  一个大 umbrella chart 把多个组件焊在一起。
- **环境画像意识**:改动如果涉及资源配置,想清楚这个改动在
  `local-lite`/`cloud-full`/`prod` 三个画像下分别应该是什么状态,见
  [ADR-004](docs/decisions/004-environment-profiles.md)。

## 提交前自查

```bash
python3 scripts/validate-charts.py   # helm template 渲染校验,CI 会自动跑这个
```

这只验证"配置能渲染出来",不验证"部署到真实集群里跑得起来"——如果你
的改动动了正在跑的组件,尽量在自己的 local-lite 环境里实际验证过再提
PR,并在 PR 描述里说明验证方式(不只是"我改了配置",而是"我做了 X,
确认了 Y 这个真实结果")。

## Commit 和 PR

- Commit message 说清楚"为什么"而不只是"改了什么"——这个仓库的历史
  记录本身也是一种文档,`git log` 应该能读出决策脉络,不只是一堆
  "update xxx.yaml"。
- 一个 PR 聚焦一件事,不要把不相关的改动混在一起,方便评审和以后
  `git blame` 追溯。
- 如果 PR 引入了新的已知限制/待办,加进 `docs/architecture.md` 的
  "还没定的事",或者对应组件的 ADR 里,不要只留在 PR 描述里等着被
  遗忘——这个仓库吃过一次真实的亏:一次会话里讨论过的需求,当时没有
  写进任何持久化文档,后来对话历史被压缩,内容完全丢失,靠翻找原始
  会话记录才找回来(见 [ADR-040](docs/decisions/040-enterprise-governance-roadmap.md))。

## 报告问题

用 GitHub Issues,附上:你在哪个环境画像下遇到的(local-lite/
cloud-full/prod)、具体报错信息、`docs/operations/troubleshooting.md`
里有没有类似的已知问题。安全相关的问题请看
[SECURITY.md](SECURITY.md),不要开公开 Issue。
