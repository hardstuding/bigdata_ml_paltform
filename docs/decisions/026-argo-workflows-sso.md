# 026. Argo Workflows 接 Keycloak SSO,以及 CRD 安装 Job 的网络坑

- 状态: 已采纳(2026-08-11,已验证:8 个 CRD 装上,OAuth2 授权跳转确认可用)

## 决策

官方 argo-helm 仓库(`https://argoproj.github.io/argo-helm`,和 ArgoCD 自己
用的是同一个来源)。SSO 用 `server.sso`,机制和 ArgoCD 内置 OIDC 是同一种
"单一 issuer,服务端和浏览器共用"的设计(不像 Grafana/JupyterHub 能把两个
用途的 URL 分开配,见 ADR-025),复用已有的 CoreDNS 自定义 zone
(`keycloak.local-lite.test` 已经在里面,当初是给 ArgoCD/Trino 配的,见
ADR-016/017),不需要新增解析配置。

## 踩的坑:CRD 安装 Job 直连 GitHub 超时

这个 chart 的 CRD 不是打包在 chart 里的(说是"太大",用完整版 OpenAPI
schema),是靠一个 pre-install hook Job 执行 `kubectl apply --server-side
-f https://raw.githubusercontent.com/...` 直接从 GitHub 下载再 apply。这台
机器上的 pod 直连 `raw.githubusercontent.com` 会卡住/超时(实测从 colima
虚拟机内部直接 `curl` 同一个地址,5 秒内传了 212KB/2.6MB 就超时了)——和
ArgoCD 自己的 repo-server 当初需要 `NEEDS_LOCAL_PROXY` 是同一个网络限制。

这个 chart 很贴心地在自己的 `values.yaml` 里就写了
`crds.upgradeJob.extraEnv` 支持配 `HTTP_PROXY`/`HTTPS_PROXY`
的注释示例,不需要我们自己发明绕过办法或者把 CRD 安装挪到手动脚本里
(和 kube-prometheus-stack CRD 走 `scripts/04-install-kube-prometheus-crds.sh`
单独手动安装是不同的解法——那边是 ArgoCD 本身应付不了 CRD 体积,这边是
纯粹的网络连通性问题,加个代理就好,不需要绕开 ArgoCD 管理)。

## 后果

- 代理地址(`192.168.5.2:1087`)是从 `colima ssh -- env | grep -i proxy`
  查出来的,换了网络或者重启 colima 后如果这个 Job 卡住,先重新确认地址
  没变。
- 这类"chart 自己的 hook Job 需要直连外部网络"的模式,以后装新组件时要
  留意——不是所有 chart 都像这个一样贴心地留好 proxy 配置项,遇到类似情况
  先看 chart 的 `values.yaml` 里有没有现成的 proxy/env 覆盖点,没有的话
  才考虑手动脚本安装这条路。
