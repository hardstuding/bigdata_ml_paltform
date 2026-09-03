# 从零把这套平台拉起来

**这份文档假设你(人或 AI)对这个项目一无所知。** 照着从上到下做,不用先
读别的。看不懂某一步为什么这么做,那一步会给一个链接。

> 这份是**部署**文档。想知道"平台能用来干什么"看
> [`../usage-guide.md`](../usage-guide.md);想知道"现在做到哪了"看
> [`../project/capability-matrix.md`](../project/capability-matrix.md)。

---

## 0. 先确认你有什么、缺什么

```bash
python3 scripts/list-manual-credentials.py
```

它会列出**脚本生不出来、必须由人提供**的东西。**不需要连集群**——这一步
的意义就在于让你在还没有集群的时候就知道要准备什么。

现在的答案很短:

| 要准备的 | 必需? | 不给会怎样 |
|---|---|---|
| 一个能用的 Kubernetes 集群 + `kubectl`/`helm` | **是** | 无从开始 |
| `permission-request-app-git`(GitHub PAT) | **是**(想让权限审批真正生效) | 审批走得完,但授权不会真正生效,状态停在 `approved_pending_apply` |
| 阿里云 ACR 的 4 个 GitHub 仓库 secret | **境内部署是** | CI 不报错,但**境内集群拉自建镜像会超时** |
| 企微 webhook | 否 | 不推送通知,其余不受影响 |

**凭据只由仓库所有者本人设置。** 任何 AI 协作者都不该经手这些值 —— 这是
这个项目的既定规矩,不是客套。

---

## 1. 集群

已经有集群就跳过。从一台裸 ECS 开始:

```bash
CLOUD_VM_IP=<公网IP> CLOUD_VM_KEY=<私钥路径> ./scripts/21-bootstrap-cloud-vm.sh
```

它做四件事:挂数据盘、装 Docker、装 k3s(带 `--disable traefik`,我们用
ingress-nginx)、把 kubeconfig 弄回本机。

> **k3s 在这台机器上用的是 `--docker`(cri-dockerd)**,所以镜像存在 Docker
> 里,不是 containerd 的 `k8s.io` 命名空间,`registries.yaml` 也不会被读。
> 这个细节坑过好几次,记在这里免得再查一遍。

### 买机器时怎么选云盘(选错了很贵,而且改不回来)

数据盘选 **ESSD PL0**,别选 ESSD AutoPL(`cloud_auto`)。

同样 200G,AutoPL 是 0.42 元/小时(约 302 元/月),PL0 是 0.21(约 151)——
**贵一倍**,而这个平台的 I/O 压力(demo 规模的数据 + 容器镜像读写)离 PL0
的上限还远。AutoPL 还带突发 IOPS 计费,账单上会冒出一笔对不上号的
`ioburst`。

**而且 AutoPL 换不回来**:`ModifyDiskSpec` 不支持把它转成任何其他类型
(实测 ESSD Entry / 高效云盘报 `InstanceTypeUnsupported` —— 新一代规格
只认 ESSD 系列;普通 ESSD 报 `DiskCategory not valid`)。只能新建一块盘
把数据拷过去,见 `scripts/57-migrate-data-disk.sh`。

容量按"实际用量 + 60G 左右余量"选。用量的大头是容器镜像(2026-09 实测
86G 里绝大部分是在用的镜像,清不掉多少),不是业务数据。

## 2. 选环境

三档:`local-lite`(本机 colima)、`cloud-full`(单台云主机)、`prod`。
差别只在 `environments/<env>/config.yaml`,**不是三套复制粘贴的副本**。

```bash
python3 scripts/render-environment-config.py cloud-full
git add -A && git commit -m "chore: 渲染 cloud-full" && git push
```

> **必须 commit + push。** 渲染改的是本地文件,而 ArgoCD 读的是 git 远端 ——
> 不推上去,它看到的还是旧的。一键脚本会校验这一点并在不一致时停下来,
> **但它不会替你渲染**:自动渲染只会制造"我明明渲染过了"的错觉。

仓库地址不是这个 GitHub demo 的话,先换掉 Application 里的 repoURL:

```bash
./scripts/set-repo-url.sh https://gitlab.com/<你的路径>/bigdata_ml_paltform.git
git add -A && git commit -m "chore: 迁移仓库地址" && git push
```

## 3. 一条命令拉起

```bash
./scripts/bootstrap-all.sh                                  # 默认 cloud-full
TARGET_ENV=local-lite NEEDS_LOCAL_PROXY=1 ./scripts/bootstrap-all.sh
```

27 步,**幂等**,中途失败直接重跑整份脚本,没有副作用。完整执行顺序、每步
在做什么、哪些是"必需"哪些是"尽力"见
[`scripts/README.md` 的部署主线表](../../scripts/README.md#1-从空集群拉起部署主线)
——那张表由 `scripts/check-bootstrap-coverage.py` 保证和脚本本身一致,不会
出现"文档说有这一步而脚本没做"。

日志在 `logs/bootstrap-all.log`(不进 git)。

**它跑完会出一份报告** `logs/bootstrap-report.json`,必需能力没达标会非零
退出 —— 不会出现"脚本说成功但平台是坏的"。

## 4. 补上人工凭据

一键脚本跑完之后:

```bash
kubectl -n permission-request-app create secret generic permission-request-app-git \
  --from-literal=GIT_TOKEN=<你的 PAT>
kubectl -n permission-request-app rollout restart deploy/permission-request-app
```

## 5. 确认真的能用

**不要看 `kubectl get pods` 就下结论。** 这个项目被"Pod Running / ArgoCD
Synced 但功能是坏的"坑过至少四次(清单在
[`../project/capability-matrix.md`](../project/capability-matrix.md) 底部)。

```bash
# 六条黄金链路:每条都真的查一次数据 / 采一次目录 / 发一次推理请求
kubectl -n monitoring get cronjob | grep goldenpath

# 产品层功能的回归验收(28 条,一条一条报 ✅❌)
./scripts/46-verify-p15.sh
```

> **`46-verify-p15.sh` 全部跳过会退出码 2**,不会把"什么都没验"报成通过。

### 判断"现在到底好没好"的方法

`kubectl get pods -A` 里的 `Error` 是**过去某一刻的快照**,不是现在的状态
—— 集群刚起来时定时任务会先跑一轮然后失败,这很正常。要看的是**每个
CronJob 最近一次**那个 pod:

```bash
kubectl get pods -n monitoring --sort-by=.metadata.creationTimestamp | grep goldenpath
```

## 6. 浏览器访问

`local-lite` 用的是自造域名(不是真实 DNS,见
[ADR-016](../decisions/016-ingress-domains-local-lite.md)),要在自己电脑的
`/etc/hosts` 里加一行。集群内部的 pod 靠 `platform/coredns-custom/` 自动
解析,不需要这一步。

**入口从门户进**,不要背各个组件的地址:`http://portal.<你的域名后缀>`。
门户会按当前环境拼出正确的域名和端口,还会现场探测每个工具在不在线。

> **cloud-full 上公网 IP 每次开机都会变**(不是固定 EIP)。
> `scripts/32-start-cloud-vm.sh` 会检测 `/etc/hosts` 里是不是还指着旧 IP
> 并给出可直接粘贴的修复命令。**这不只是"打不开"**:旧 IP 早被回收、多半
> 已经分给别人的实例了,而浏览器会把 `*.local-lite.test` 的 cookie 一起
> 发给那台陌生机器。

---

## 常见失败

| 症状 | 多半是 |
|---|---|
| 一堆 Pod `ImagePullBackOff`,都是自建镜像 | 没配私有仓库拉取凭据。跑 `./scripts/45-configure-acr-pull.sh`(一键脚本里有这一步,但它需要 ACR 凭据已经就位) |
| ArgoCD 里某个 Application 一直 `Missing`,说某个 kind 不存在 | 那个组件的 CRD 太大、ArgoCD 装不了,要单独装。四个:kube-prometheus / CloudNativePG / argo-workflows / Kueue,脚本 `04`/`16`/`25`/`33` |
| 组件都起来了但登录全都跳不过去 | `03-configure-keycloak.sh` 没跑,或者组件是在它之后才第一次 Sync 的 —— 重跑一次,它幂等 |
| 页面上出现"这次登录拿到的令牌里没有 groups 字段" | 同上,重跑 `03` 然后**重新登录**。这是配置问题不是权限问题,提示里写了 |
| 建表/审批/门户上某一块是空的 | 先看那块旁边有没有黄色提示 —— 这个项目刻意把"读不到数据"和"你没有数据"分开显示了 |
| 脚本没有任何输出 | **macOS 没有 `timeout` 命令**。别用 `timeout xxx ./script.sh` |

更完整的按症状检索:[`troubleshooting.md`](troubleshooting.md),顶部有 59 条
症状索引。

---

## 这套东西还没上过生产

能力表里**没有任何一格是"生产验证"**。上生产前的门禁条件列在
[`../project/production-readiness-gaps.md`](../project/production-readiness-gaps.md)
—— 多节点演练、MinIO 分布式和备份恢复、真实 IdP、真实告警渠道、RPO/RTO
演练证据等。在那些补齐之前,不要对外说"生产可用"。
