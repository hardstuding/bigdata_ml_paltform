# 001. 用 Kubernetes 做统一调度层,本地用 OrbStack

- 状态: 已采纳(2026-08-08)

## 背景

没有服务器,先在本地 Mac(M2 / 8核 / 16GB / arm64)搭建,未来会接入云服务器甚至替换现有生产大数据平台。需要一个从本地到云端都一致的调度/部署方式。

## 决策

用 Kubernetes 作为所有组件的统一调度层。本地通过 OrbStack 起 k8s(而不是 Multipass+k3s 或 colima+k3s)。

## 理由

- 16GB 内存下,资源开销是本地方案的第一约束。OrbStack 是 arm64 原生、启动快、后台资源占用明显低于 Docker Desktop。
- 代价是不是字面意义上的"独立虚拟机",但只要 k8s API 行为一致,上层的 Helm/GitOps 配置不关心底下是 OrbStack 的轻量虚拟化还是 Multipass 里的完整 Ubuntu VM。
- 云端/生产环境会用标准 k8s 发行版(如托管 k8s 或 kubeadm),本地方案只是"哪个虚拟化层最省资源",不影响上层可移植性。

## 后果

- 换云服务器时,只需要有一个能跑标准 k8s 的地方(托管 k8s、kubeadm、k3s 都可以),`platform/`、`apps/` 下的 Helm 配置不用改,只切 `environments/` 的 values。
- 如果未来发现 OrbStack 某些行为和标准 k8s 有差异(概率低),需要单独记录,不影响本决策的可逆性——换回 Multipass/colima 只是重新跑一遍 `infra/` 里的自举脚本。
