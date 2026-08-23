# CPU 架构:统一到 x86_64

2026-08-23。起因是 zhenghe 问"arm64 好像在很多场景优于 x86_64,后续还能
切回 arm64 吗",随后两次澄清把结论定了下来:

1. "x86_64 在购买服务器的时候就定了,改不了了,但我要用历史已有的服务器"
2. "arm64 这条约束可以直接删掉的。我 mac 上之前也是想对齐服务器的系统,
   避免我弄好了,实际上生产不兼容"

## 结论

**目标是三档环境全部 x86_64,包括本机。**

| 环境 | 硬件 | 架构 | 说明 |
|---|---|---|---|
| local-lite | Mac M2(colima) | **目标 x86_64** | 硬件是 arm64,但 colima 可以起 x86_64 的虚拟机,见下面 |
| cloud-full | 阿里云 ecs.g9i.4xlarge | x86_64 | 规格购买时确定,改不了 |
| prod | 公司已有服务器 | x86_64 | 已有硬件 |

**"本机跟生产对齐"这个目标,比"保持双架构"更贴近 zhenghe 真正要的东西。**
双架构保证的是"两边都能跑",而他要的是"**本地跑通 = 生产也能跑通**"——
这两件事不一样:双架构下,本地跑的是 arm64 那份二进制,生产跑的是 amd64
那份,JVM 参数、native 库、镜像层都可能有差异,本地验过的东西**并不真的
等于验过生产**。统一到 x86_64 才是这个目标的正解。

代价是 Mac 上要用 Rosetta 转译跑 x86_64,性能有损失。但 local-lite 本来就
只是"跑通流程"的环境(单机 6G/4vCPU),它从来不承担性能验证的职责。

## 怎么把本机 colima 切成 x86_64

**这一步是破坏性的**:重建 colima 虚拟机 = 现有的 k3s 集群和里面所有数据
都没了。local-lite 是可以从零重建的环境(`./scripts/bootstrap-all.sh`),
但要挑一个手上没有半截活的时候做。

```bash
colima delete            # 会问一次确认
colima start --arch x86_64 --vm-type vz --vz-rosetta   --cpu 4 --memory 6 --disk 60 --kubernetes
```

关键是 `--vm-type vz --vz-rosetta`:走 macOS 自己的虚拟化框架 + Rosetta
转译,比 QEMU 纯软件模拟快一个数量级。**不加 `--vz-rosetta` 的话是 QEMU
模拟,慢到没法用**,这是这条路上唯一容易踩错的地方。

切完之后本机的 `image-cache/` 里存的是 arm64 镜像,已经没用了,要用
`scripts/export-image-cache-amd64.sh` 那条路重新准备。

## 这件事对仓库的影响

- **新增组件不用再查 arm64 支持了。** 之前那条"加组件先确认有没有 arm64"
  的约束**删除**。
- `scripts/check-image-arch.py` **保留,但用途反过来**:现在要保证的是
  所有镜像都有 **amd64**(这本来就是绝大多数镜像的默认架构,基本不会出
  问题,但作为一致性检查留着不亏):

  ```bash
  python3 scripts/check-image-arch.py --arch amd64
  ```

- **CI 现在仍然同时构建 amd64+arm64。** 等本机 colima 确认切完之后,可以
  降成只建 amd64:构建时间减半,而且 `apps/flink-iceberg-image` 那条
  "只建 amd64"的特例注释可以直接删掉(那个特例存在的唯一原因就是
  apache-flink 没有 aarch64 wheel)。**没有现在就改**,是因为在本机切换
  完成之前把 arm64 那条腿砍掉,会让本地立刻拉不到能跑的镜像。这条记在
  `docs/BACKLOG.md` 里,不是忘了。

## 附:切换前的实测数据(2026-08-23)

`python3 scripts/check-image-arch.py --arch arm64`,77 个镜像里 62 个支持
arm64,5 个只有 amd64(`apache/hive:3.1.3`、我们自己的 `flink-iceberg`、
以及 tensorflow-serving / torchserve / mlserver 三个可选的 KServe runtime),
10 个因为网络查不到。

留着这段是因为它顺带说明了**为什么统一到 x86_64 是省事的方向**:
amd64 是所有这些镜像的共同分母,arm64 不是。

## 脚本本身踩过的一个坑,值得记

`check-image-arch.py` 第一版没加 `docker manifest inspect --verbose`,
而单架构镜像返回的 manifest 里**根本没有架构字段**(架构在 config blob
里),脚本据此把两个镜像报成"不支持 arm64"——**检查工具自己给出了一个
看起来很确定的错误结论**,正是这个项目最忌讳的那类错误。现在严格区分
"确认不支持"和"查不到",后者明确标注"不等于不支持"。

这个脚本刻意**没有放进 CI**:它要访问十几个境外 registry,在这个网络环境
下失败是常态(第一次跑 77 个里 54 个是代理报错)。放进 CI 的结果是它经常
红,然后所有人学会忽略它——**一个被习惯性忽略的检查比没有这个检查更糟**。
