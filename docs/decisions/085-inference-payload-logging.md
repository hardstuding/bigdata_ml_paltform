# ADR-085:推理请求留痕 —— 用 KServe 自带的 logger,落到 Iceberg

日期:2026-08-30
状态:**已实现,未部署验证**

## 问题

`docs/project/roadmap.md` P4 的 C 线(完整 MLOps)里,**特征漂移监控**一直
挂着"没做",而它卡在一个更基础的缺失上:**推理请求根本没有留痕**。

今天线上模型收到过什么输入、返回过什么预测,平台一概不知道。这直接导致
三件事做不了:

1. **特征漂移** —— 没有"线上输入的分布"就无从比较训练时的分布。
2. **线上问题复现** —— 有人说"模型给了个离谱的结果",没法回到那一次请求。
3. **模型的审计** —— 谁在什么时候拿什么数据请求过模型,答不上来。而
   查询审计(ADR-066)已经把 SQL 那一侧解决了,推理这一侧是空的。

## 不自己在推理服务里加埋点

最直觉的做法是在模型服务里包一层、把入参写出去。**不做**:

- 那要改推理镜像 / 加 sidecar,而模型服务是**用户的东西**,平台往里塞代码
  会变成"平台要求你用我们的 SDK 才能上线"。
- KServe **自带这个能力**(`spec.predictor.logger`),会把请求和响应作为
  CloudEvents POST 到一个 URL。复用它,不自建。

## 落到哪:Kafka → Flink → Iceberg

KServe 的 logger 只负责"POST 到某个 URL",接收端要自己提供。三种选法:

| 方案 | 结论 |
|---|---|
| **接收端 → Kafka → Flink → Iceberg** | **选这个** |
| 接收端直接 `INSERT` 进 Iceberg | 每个请求一次 INSERT,小文件问题严重;pod 重启丢内存里攒的批 |
| 只写文件/日志,靠 Loki 查 | Loki 适合看,不适合"按字段做分布统计" —— 而漂移分析要的正是后者 |

选第一条的**关键理由不是性能,是一致性**:这个平台已经有两条验证过的
Kafka → Flink → Iceberg 链路(设备事件 ADR-062、查询审计 ADR-066)。再加
一条走同一套模式,意味着运维方式、排障套路、监控指标全部复用;而且
2026-08-29 做的 `streams/` 发布机制正好让加一个 Flink 作业只需要写几行
yaml —— **这也是对那套机制的第二个真实用例**(第一个是它自己迁移过来的
设备事件流)。

## 接收端为什么是一个新组件

需要一个"接 HTTP、写 Kafka"的东西。看了一圈现有的:

- `alert-echo-sink` 是**只回显不转发**的,加转发就改变了它的定位(它存在的
  意义正是"最简单的终点",见 ADR-081)。
- Kafka 没有原生的 HTTP 入口(Karapace 是 schema registry,不是 REST proxy)。

所以新增 `apps/inference-log-sink/`,~60 行 Flask。**这是这个仓库里第 4 个
自建应用**,加之前想过是不是过度:结论是这个能力没有它就不成立,而它足够
小、职责单一(收 CloudEvent → 丢进 Kafka),不会长大。

## 表结构和字段

`iceberg.ml.inference_log`:

| 字段 | 说明 |
|---|---|
| `request_id` | KServe 给的 CloudEvent id,**同一次请求的 request/response 两条记录靠它关联** |
| `inference_service` / `namespace` | 哪个模型服务 |
| `event_type` | `request` / `response` |
| `payload` | 原始 JSON 字符串 |
| `event_ts` | 事件时间 |

**payload 存原始 JSON 而不是拆成列**:不同模型的输入 schema 完全不同,拆
成固定列意味着每加一个模型就要改表。漂移分析用 Trino 的 JSON 函数从
payload 里取字段 —— 慢一点,但不用为每个模型改 schema。

## 这条链路会记录敏感数据

推理输入很可能包含个人信息。**这一点必须在开启之前想清楚**:

- `ml` 这个 schema 的表和 `audit` 一样,**默认只有 platform-team 能读**
  (OPA 策略里加,和审计表同一个处理)。
- 保留期没做 —— Iceberg 的过期/清理这个平台整体还没做(见
  `production-readiness-gaps.md`)。**上生产前必须先解决**,否则这张表会
  无限增长且包含个人信息。
- **logger 默认不开**:`scripts/11` 需要显式传 `ENABLE_PAYLOAD_LOG=1`。
  开着它对一个 demo 平台没有必要,而"默认收集所有推理输入"不是一个应该
  默默生效的行为。

## 没做的

- **漂移检测本身**。这份 ADR 只解决"有数据"。有了 `inference_log` 之后,
  漂移是"对同一个字段,比较线上分布和训练集分布" —— 那是一个作业
  (`jobs/` 里写一个就行),不是一个新组件。
- **response 的采样**。现在 request/response 全量记。量大了要采样,但
  在有真实量之前定采样率是拍脑袋。
