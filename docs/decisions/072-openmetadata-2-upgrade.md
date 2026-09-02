# ADR-072:OpenMetadata 升到 2.0.0(大版本)

日期:2026-08-26
状态:**已升级并验证通过**(cloud-full)

## 先把风险说清楚

**2.0.0 GA 是 2026-08-24 发布的,到今天(08-26)只有两天。** 把一个刚 GA
两天的大版本装到平台上,正常情况下我不会建议——上游还没经历过真实用户的
一轮洗礼,踩到的坑很可能是"全世界第一批踩到"。

(写第一版时我把日期弄错成 08-23、说成"不到一天",拿集群时钟核对后改正。
结论没变,只是没那么极端。)

使用方明确要求升,而且 **cloud-full 恰恰是干这件事的正确地方**:它是
功能完整的集成环境,不是生产。所以做法是:在这里升、在这里暴露问题,
**prod 那一档等它沉淀几个补丁版本再跟**。这条写进下面的"建议"里。

## 升级前逐条核对 breaking changes

上游列了 8 条,对这个平台的影响:

| Breaking change | 我们受影响吗 |
|---|---|
| MCP 游标分页 | 否,没有 MCP 客户端 |
| 语义搜索 / embedding 配置搬家到 `llmConfiguration` | **否**,我们没开语义搜索 |
| Profiler 默认动态采样、不再收集基数分布 | **否**,采集只配了 `DatabaseMetadata`,没开 profiler;数据质量断言用的是行数/非空/唯一/新增行数,不依赖基数 |
| Databricks Pipeline 认证结构变化 | 否,没用 Databricks |
| 自建 ingestion 镜像要重建成 Python 3.12 | 否,我们用官方 `ingestion-base`,只要改 tag |
| Chrome 插件 trusted redirect URI | 否 |
| Knowledge Center → Context Center,老链接失效 | 否,没有指向它的链接 |
| **Great Expectations 0.x 支持移除** | **否** —— [ADR-065](065-data-quality-on-openmetadata.md) 当初选的是 OpenMetadata 自带的数据质量而不是引入 GE,现在正好躲开了一次强制迁移 |

**还有一条是好消息**:2.1 会废弃"Airflow 作为**内部**编排器",官方推荐迁到
1.12 引入的 Kubernetes 原生编排。这个平台从
[ADR-015](015-openmetadata-architecture.md) 起就一直用 k8s 模式
(`pipelineServiceClientConfig.type: k8s`),**不用做任何迁移**。当初那个
取舍(不额外维护一个 Airflow 只为了跑采集)现在直接省下一次迁移。

## chart 的 values 结构没有变

`helm show values openmetadata --version 1.13.3` 和 `--version 2.0.0`
**逐字节相同**(diff 0 行)。所以 `apps/components/openmetadata.yaml` 里那份
valuesObject 一行都不用改,改动只有两处版本号:

- `targetRevision: 1.13.3` → `2.0.0`
- `ingestionImage: ...ingestion-base:1.13.3` → `:2.0.0`

这个事实值得单独查一下再动手——**大版本升级最贵的通常不是新功能,是
values 结构变了导致一堆配置静默失效**。这次没有。

## 升级步骤(和为什么是这个顺序)

1. **先备份 Postgres。** 2.0 会跑数据库迁移,迁移是**不可逆**的;
   `scripts/restore-postgres-backup.sh` 是唯一的回退路径。
   备份不做,这次升级就没有回头路。
2. 先把 1.5GB 的 server 镜像和 ingestion 镜像**预拉到节点上**。不预拉的
   话 ArgoCD 同步之后 Pod 会卡在 ImagePullBackOff 十几分钟——这个坑
   1.13.3 那次已经踩过一遍([ADR-015](015-openmetadata-architecture.md)
   的补记)。
3. git push + 同步,让 chart 的 db-migration Job 跑完。
4. 验证的是**业务结果不是 Pod 状态**:目录里的表还在不在、Trino 采集
   pipeline 还在不在、数据质量断言还在不在、能不能登录。

## 实机(cloud-full,2026-08-26)

**升级成功,验证通过。**

| 项 | 升级前 | 升级后 |
|---|---|---|
| 版本 | 1.13.3 | **2.0.0** |
| 目录里的表 | 388 | **388**(一张没少) |
| 采集/质检 pipeline | 3 个 | **3 个,名字全对** |
| 数据质量断言 | 4 条,都有结果 | **4 条,都有结果** |

验收脚本是 [`scripts/37-verify-openmetadata-upgrade.sh`](../../scripts/37-verify-openmetadata-upgrade.sh)
(支持和升级前的基线做 diff,可重复跑)。

**不是只看"对象还在"**:升级后重新触发了一轮采集和质检,三个 k8s Job
(`om-job-trino-metadata` / `om-job-orders-data-quality` /
`om-job-device-events-stream-data-quality`)全部用
`ingestion-base:2.0.0` 跑成功,四条断言拿到的是**当时新产生的结果**
(13:49 / 13:51 UTC),不是迁移前的旧记录。Trino 元数据采集回来的字段
和 Trino 里真实表结构逐个对得上。

数据库迁移日志里能看到 67 个搜索索引模板全部重建成功(`Success: 67,
Failed: 0`),以及 "Successfully retrieved 3 ingestion pipelines for
secrets migration" —— 我们自己 PUT 进去的那三个 pipeline 被迁移逻辑正确
识别并搬过去了。

顺带确认了一个 2.0 的新东西:`/mcp` 端点返回 405(Method Not Allowed),
说明 MCP server 确实**默认开着**,和发布说明一致。这个平台暂时没有 MCP
客户端,先记着。

### 真正卡住这次升级的不是 OpenMetadata,是拉不到镜像

云主机在升级过程中**同时失去了到 Docker Hub 的所有通路**:直连超时、
daocloud 卡在 blob 不动(层全部 `Download complete` 却永远不 extract,
25 分钟磁盘零增长、CPU 95% 空闲)、另外试的 5 个镜像站全部超时。

解法是新写的 `scripts/38-ship-image-to-cloud.sh`:用 `crane` 在本机直接
和 registry 说话(**不需要本机 docker 守护进程**)拉成 tar,rsync 上去
`docker load`。实测 server 镜像 457MB / 本地下载 1 分 19 秒 / 上传约
90 秒,整个搬运比等镜像站快一个数量级。

这件事也把"给 k3s 配 registry mirror"从"锦上添花"变成了**真实的成本项**
——见 `docs/project/roadmap.md`。

## 建议

- **prod 不要跟这个版本。** 等 2.0.x 出到至少 2~3 个补丁版本、或者
  cloud-full 上跑满一段时间没出问题,再考虑。
- 2.1 之前要开始关注 Airflow 内部编排废弃这件事——虽然我们不受影响,但
  上游的 ingestion 镜像和文档都会跟着变。
