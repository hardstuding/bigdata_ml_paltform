# CPU 架构:现状、约束、以及要守住的那条线

2026-08-23。起因是 zhenghe 问"arm64 好像在很多场景优于 x86_64,后续还能
切回 arm64 吗",随后补充"x86_64 在购买服务器的时候就定了,改不了了,
但我要用历史已有的服务器"。

## 结论先说

**生产不用切,也切不了,这件事已经定了**:目标硬件是已有的那几台
x86_64 机器。所以"要不要迁到 arm64"这个问题在这个项目里不成立。

**但真正要守的不是"用哪个架构",是"两个架构都能跑"**,因为:

| 环境 | 架构 | 为什么 |
|---|---|---|
| local-lite | **arm64** | 开发机是 Mac M2([ADR-001](../decisions/001-kubernetes-colima.md)) |
| cloud-full | x86_64 | ECS 规格购买时确定 |
| prod | x86_64 | 已有服务器 |

**一旦某个组件掉了 arm64,受损的不是生产,是本地开发**——本地跑不起来,
就没法在推上云之前先验一遍,而"先在便宜的地方验"正是这个仓库分三档环境
的全部意义。

## 现状(实测,不是估计)

`python3 scripts/check-image-arch.py --arch arm64`,77 个镜像:

- **62 个支持 arm64** ✅
- **5 个只有 amd64**
- **10 个查不到**(digest 固定的 KServe runtime 居多,`docker manifest
  inspect` 对 digest 引用会做校验而失败;**查不到不等于不支持**)

### 5 个只有 amd64 的,分别是什么情况

| 镜像 | 影响 | 结论 |
|---|---|---|
| `apache/hive:3.1.3` | Hive Metastore,**平台核心** | 上游只发 amd64。本地靠 colima 的模拟跑。**这是本地唯一一个真正被架构卡住的核心组件** |
| `ghcr.io/.../flink-iceberg` | 我们自己构建的 | **不是疏漏,是记录在案的取舍**:apache-flink 1.20.5 在 PyPI 上没有 aarch64 wheel,arm64 那条腿只能从源码编、在 QEMU 下必然失败(见 `.github/workflows/build-images.yml` 里那段注释)。local-lite 本来也没启用 Flink |
| `tensorflow/serving:2.6.2` | KServe 的 TF runtime | 只在真用 TensorFlow 模型时才有影响 |
| `pytorch/torchserve-kfs:0.9.0` | KServe 的 TorchServe runtime | 同上 |
| `seldonio/mlserver:1.7.1` | KServe 的 MLServer runtime | 同上 |

**没有一个是"因为我们没注意所以掉了 arm64"**——一个是上游不发,一个是
上游依赖不发且已写明理由,三个是可选的推理 runtime。

## 要守住的那条线

新增组件时,**先查一下它有没有 arm64**,别等本地跑不起来才发现:

```bash
python3 scripts/check-image-arch.py --arch arm64
```

**这个脚本刻意没有放进 CI。** 它要访问十几个境外 registry,在这个网络
环境下失败是常态(第一次跑 77 个里 54 个查不到,全是代理报错)。放进 CI
的结果是它经常红,然后所有人学会忽略它——**一个被习惯性忽略的检查比没有
这个检查更糟**。它是"加新组件时手动跑一次"的工具。

脚本本身踩过一个坑值得记:第一版没加 `--verbose`,单架构镜像返回的
manifest 里根本没有架构字段(架构在 config blob 里),脚本据此把两个镜像
报成"不支持 arm64"——**检查工具自己给出了一个看起来很确定的错误结论**,
正是这个项目最忌讳的那类错误。现在区分"确认不支持"和"查不到",后者
明确标注"不等于不支持"。

## 如果哪天真的要上 arm64

只有 `apache/hive:3.1.3` 是硬骨头(另外四个要么可选、要么已有取舍)。
那时候的正确问法不是"怎么给 Hive 编个 arm64 镜像",而是
**"还需不需要 Hive Metastore"**——Iceberg 现在有 REST catalog,不依赖
Hive。那是一次架构简化,不是一次架构移植,应该单独立项评估。
