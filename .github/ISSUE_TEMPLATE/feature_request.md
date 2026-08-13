---
name: Feature / 新组件或新能力建议
about: 建议接入一个新组件,或者给现有组件加能力
title: ""
labels: enhancement
assignees: ""
---

**想解决什么问题**
不要直接跳到"接入 XX 工具",先说清楚背后的真实需求是什么——这个仓库
的惯例是先想清楚"为什么需要"再决定"用什么工具",见任意一篇现有 ADR
的写法。

**有没有调研过现成方案**
是否已经有官方维护的开源组件能做这件事(这个项目的原则是优先用官方
支持的部署方式,见 `docs/decisions/008-avoid-bitnami.md`),还是确实
需要自建。

**这个改动影响哪个/哪些 Phase**
参考 `docs/architecture.md` 的路线图,新组件属于 Phase 0-4 里的哪个,
在 `local-lite`/`cloud-full`/`prod` 三个画像下分别应该是什么状态
(开启/关闭/降配)。

**是否已经在 `docs/architecture.md` 的"还没定的事"或者某篇 ADR 里
提到过**
这个仓库吃过一次真实的亏(需求讨论完没有及时归档,后来对话历史被
压缩,内容完全丢失,见 [ADR-040](../../docs/decisions/040-enterprise-governance-roadmap.md)),
提 issue 前如果发现这是一个新的、还没被记录的方向,请说明。
