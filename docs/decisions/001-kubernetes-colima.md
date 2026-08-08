# 001. 用 Kubernetes 做统一调度层,本地用 colima + k3s

- 状态: 已采纳(2026-08-08,当天修订:虚拟化工具从 OrbStack 改为 colima)

## 背景

没有服务器,先在本地 Mac(M2 / 8核 / 16GB / arm64)搭建,未来会接入云服务器或公司 IDC,甚至替换现有生产大数据平台。需要一个从本地到云端都一致的调度/部署方式。

## 决策

用 Kubernetes 作为所有组件的统一调度层。本地通过 **colima + k3s** 起 k8s。

## 决策过程(含一次修订)

最初选的是 OrbStack(arm64 原生、GUI 友好、资源占用低)。装完并启用 k8s 之后发现:**OrbStack 免费版仅限个人非商业用途**,而这个项目的终点是替换公司生产环境的大数据平台,严格算商业用途,继续用需要 Pro 订阅($8/月)。考虑到项目的初衷就是"避免像 CDH 那样被商业授权绑住,自己搭免费开源的替代方案",在切换成本还很低的时候(只开了一个空集群,还没部署任何组件)改用完全开源的 colima + k3s。

## 理由

- colima 底层同样是 macOS Virtualization.Framework,资源开销和 OrbStack 接近;唯一区别是没有 GUI,要多敲几条命令。
- colima 和 k3s 都是 Apache 2.0 开源协议,不存在个人/商业用途的授权区分。
- 云端/生产环境会用标准 k8s 发行版(托管 k8s 或公司 IDC 里的 kubeadm/k3s),本地方案只是"哪个虚拟化层最省资源、授权最干净",不影响上层可移植性 —— `platform/`、`apps/` 下的 Helm 配置不关心底下是 colima 还是别的。

## 后果

- 本地没有 GUI 面板,查看容器/集群状态靠 `colima status`、`kubectl`、`docker` 命令行,或者按需装 k9s 这类 TUI 工具。
- 如果未来发现 colima/k3s 某些行为和标准 k8s 有差异(k3s 默认精简了一些组件,如默认用 containerd、内置 servicelb 等),需要单独记录在 [troubleshooting.md](../operations/troubleshooting.md)。
