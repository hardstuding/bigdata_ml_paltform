# ADR-061:helm 有一个改不掉的 120 秒超时,alloy/loki 的 chart 只能 vendor 进仓库

日期:2026-08-22
状态:已实现并在 cloud-full 验证

## 背景:一个被误判为"状态显示问题"的部署阻断

`alloy` / `loki` / `kube-prometheus-stack` 三个 Application 长期
`Sync Status = Unknown`。因为底层 Pod 一直 Healthy,这件事被当成"只是
状态显示不准",挂在 BACKLOG 里很久没人动。

2026-08-22 量化之后发现判断错了:

- 传统 Helm 仓库每次同步都要先拉整个 `index.yaml`。
  `grafana.github.io/helm-charts` 那份 **4.0MB**,
  `prometheus-community.github.io/helm-charts` 也在同一量级。
- 从 cloud-full 这台境内云主机实测下载速度约 **12KB/s**(同一时刻,同一台
  机器上拉具体的 chart tgz 只要 4.4 秒——慢的是那个巨型 index,不是整条
  出口链路都不通)。
- repo-server 日志里 `time_ms=120030`,**每次都精确卡在 120 秒**。而
  `ARGOCD_EXEC_TIMEOUT` 明明配的是 180s(实测 `kubectl get deploy
  argocd-repo-server` 确认过)。也就是说这 120 秒是 **helm 自己的 HTTP
  超时,ArgoCD 调不到**——这个问题**没法靠调大 ArgoCD 的任何超时解决**。

**真正的后果不是状态难看**:在一个全新集群上,这几个 Application 根本
装不起来。一键部署会直接断在这里。而这件事此前从没暴露,是因为现有集群
是增量长出来的——chart 早就拉下来过了,Pod 一直活着,没人注意到"再也
拉不到新的了"。这正是这个项目反复踩的那类坑:**当前状态健康,不等于
从零重建得出来**。

## 决策

分两种情况处理,不搞一刀切:

### 1. 上游有官方 OCI 仓库的:换 OCI

`kube-prometheus-stack` → `oci://ghcr.io/prometheus-community/charts/kube-prometheus-stack`。

OCI 不需要 index,按名字+版本直接取 manifest。**这不是换了 chart 来源
方**——同一个组织发布的同一个 chart 的另一种分发形式,digest 可核对。
实测:894KB 的 chart 几秒拉完,`helm template` 184ms,Application 从
`Unknown` 变成正常比较。

### 2. 上游没有 OCI 的:vendor 进仓库

Grafana 目前没把 chart 发到 OCI —— `ghcr.io/grafana/helm-charts/alloy`、
`ghcr.io/grafana/charts/alloy`、`ghcr.io/grafana/alloy` 三个候选路径实测
全是 403 / not found。所以 `alloy` 和 `loki` 用
`scripts/28-vendor-helm-chart.sh` 原样解包进 `platform/alloy-chart/` 和
`platform/loki-chart/`,Application 改成从这个 git 仓库的 `path:` 读。

体积可以接受:两个 chart 加起来 379 个文件、约 200KB 压缩。

**为什么不选另外两条路**:

- *镜像到我们自己的 GHCR OCI 仓库*(GitHub Actions 境外 runner 上
  `helm pull` + `helm push`):技术上直接,但 GHCR package 默认私有,要么
  需要人去 UI 上手动改成 public,要么得给 ArgoCD 配拉取凭据——多一个
  "换台机器/换个账号就要重做一遍"的手工步骤,和这个仓库"一键部署"的目标
  相反。
- *只调大超时忍着*:**这条根本不成立**,前面说了 120 秒是 helm 自己的,
  ArgoCD 调不到。这一点值得单独强调,因为它是最容易想到、也最容易被
  当成"已经想过了"而写进 backlog 的方案。

## 后果

- **顺带解决了一条一直挂着的顾虑**:vendor 进来的 chart 不需要任何外网
  访问就能部署,对"生产环境可能没有外网"这个场景是实质性的改善。
- **升级 chart 变成显式动作**:改 `scripts/28-vendor-helm-chart.sh` 的
  调用参数重跑,然后 review diff。比原来"改一个 targetRevision 数字"多
  一步,但 diff 里能真的看到 chart 内容变了什么——对第三方依赖来说这是
  好事不是坏事。chart 版本记在各自的 `VENDORED.md` 里,
  `scripts/list-component-versions.py` 仍然能扫到。
- **CI 覆盖没有变弱**:`scripts/validate-charts.py` 加了一条分支,
  `path` 指向的目录里有 `Chart.yaml` 就真的跑一遍 `helm template`
  (带我们自己的 valuesObject),不当成"纯 git manifest"只做语法检查。
  这一点特意做了——vendor 之后 CI 反而变弱的话,丢掉的正好是"我们的
  values 和这个 chart 版本对不对得上"这一层。
- **`targetRevision` 的含义变了**:对这两个 Application 来说它现在指的是
  这个 git 仓库的分支(`main`),不是 chart 版本。两个 Application 文件
  里都写了提醒。
