---
name: Bug report / 问题反馈
about: 部署/使用过程中遇到的具体问题
title: ""
labels: bug
assignees: ""
---

**先查过 `docs/operations/troubleshooting.md` 了吗?**
(是/否——这个文档专门收集真实踩过的坑,很多"看起来是新问题"的报错
已经有记录和处理方式)

**环境画像**
local-lite / cloud-full / prod(见 `docs/architecture.md` 的环境画像
定义)

**涉及组件**
比如:Trino、Keycloak、ArgoCD Application 名字等——越具体越好

**现象**
实际发生了什么,贴出具体报错信息(`kubectl describe`/日志片段,不是
"不工作了"这种描述)

**期望行为**
你觉得应该发生什么

**复现步骤**
1.
2.
3.

**其他上下文**
比如:是不是刚做过 `colima delete` 重建、是不是刚从 `pending-
definitions/` un-park 某个组件、相关的 ADR 编号(如果知道)
