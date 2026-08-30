# ADR-075:KServe runtime 只默认装四个,其余挪进 optional/

日期:2026-08-26
状态:**已部署**;默认那 4 个 runtime 由 `inference` 黄金链路探针持续验证(2026-08-29 实测通过)

## 背景

`docs/project/roadmap.md` 里一直挂着一条"KServe runtime 矩阵设计",说明写得很清楚
——2026-08-15 那次只做了"把 7 个浮动 `latest` 固定成版本号 + digest",
**要不要精简是故意留着的更大判断,不是遗漏**。zhenghe 2026-08-26 说"要做"。

vendor 进来的是官方全套 12 个 ClusterServingRuntime。问题是:**这个平台上
没有任何一个 InferenceService 在用其中八个**,而它们的镜像要跟着走完整套
镜像准备流程(`scripts/23`、`export-image-cache-amd64.sh`)。

代价不是抽象的。2026-08-26 升级 OpenMetadata 时,云主机的镜像拉取被
`kserve/huggingfaceserver:v0.19.0-gpu` 占着——**那个镜像 22.9GB**,而这台
机器上**根本没有 GPU**。为了一个永远不会被调度的 runtime,在按小时计费的
机器上占了拉取通道。

## 决策:按"这个平台的数据实际长什么样"来选,不是按"KServe 支持什么"

这个平台接的是 Iceberg 湖仓上的结构化数据,真实场景是**表格类建模**。
所以默认装这四个:

| runtime | 留它的理由 |
|---|---|
| `kserve-sklearnserver` | **平台唯一真正跑通过的推理路径**(`scripts/09` 训练 → `scripts/11` 上线) |
| `kserve-mlserver` | 支持 `mlflow` 模型格式,而这个平台的模型注册表就是 MLflow ——**战略上最重要的一个** |
| `kserve-xgbserver` | 表格类建模最常用,镜像小 |
| `kserve-lgbserver` | 同上 |

其余八个挪到 `apps/kserve-runtimes/optional/`,**不是删除**:文件原样、版本
和 digest 原样,要用时一条命令加回来:

```bash
kubectl apply -k apps/kserve-runtimes/optional/
```

分三类,每类的理由写在那份 kustomization 里:

- **深度学习框架**(tensorflow-serving / torchserve / tritonserver /
  huggingfaceserver ×2):没有 GPU 节点,也没有任何 PyTorch/TF 模型。
  顺带一提 tensorflow-serving 固定的版本是 **2.6.2,2021 年的**——真要用
  之前得先升级,而不是直接打开。
- **小众/遗留格式**(paddleserver / pmmlserver):平台上没有这两类模型。
- **被覆盖**(predictiveserver):它 `autoSelect=false`,而它支持的
  sklearn/xgboost/lightgbm 三个格式已经各有专用 runtime。

## 效果

镜像清单从 **77 → 69**(`scripts/list-project-images.py`)。这不是"数字好看"
——它直接决定 `scripts/23` 要拉多少、`export-image-cache` 要打包多少,而这两
件事今天刚证明是按小时烧钱的。

机制上不用改任何脚本:`list-project-images.py` 是跑
`kubectl kustomize apps/kserve-runtimes/manifests` 拿镜像的,resources 里没有
的自然就不出现了。

## 实机(cloud-full,2026-08-26)

`scripts/10` 重跑之后,**集群里那 8 个 ClusterServingRuntime 仍然在**
——`kubectl apply -k` 不会删除"不再出现在 kustomization 里"的对象(没有
prune 这回事)。这是预料之中的,但值得写下来,免得有人以为改完 git 就完事了。

`kubectl get inferenceservice -A` 确认**整个集群一个 InferenceService 都
没有**,所以那 8 个是纯粹的模板残留,不影响任何东西(它们只是声明,不跑
Pod)。

**没有顺手删掉它们**:ClusterServingRuntime 是集群级资源,而 `CLAUDE.md`
把集群级资源的增删改列为要先问用户的动作(这台机器和 Codex 那个项目共用
同一个 k3s)。虽然这里能证明零引用、风险极低,但"能证明安全"和"可以绕过
写下来的规则"是两回事。清理命令留在这里,由用户决定什么时候执行:

```bash
kubectl delete -k apps/kserve-runtimes/optional/
```

不删的实际代价接近于零:它们不占资源,而 ADR 的主要收益(镜像清单 77 → 69,
少拉几十 GB)在**下一次部署/镜像准备**时就已经拿到了。

## 后果与注意

- **提交 `modelFormat` 是这八种之一的 InferenceService 会失败**(找不到
  runtime)。现在没有这样的服务,但以后有人要上 PyTorch 模型时,报错会是
  "no runtime found",不是"不支持"——`optional/` 那份 kustomization 的开头
  就是写给那个时刻看的。
- 加回来之前先看版本:tensorflow-serving 2.6.2 / torchserve 0.9.0 都很旧了。
