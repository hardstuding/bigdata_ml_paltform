# 007. 消息队列用 Kafka(不是 Redpanda)

- 状态: 已采纳(2026-08-08,当天修订)

## 背景

Phase 2 数据工程层需要消息队列能力(行为数据总线、后续 CDC/实时特征的输入)。最初考虑用 Redpanda(Kafka 协议兼容、资源占用更低、运维更简单),理由是"要的是消息能力,不是运营 Kafka 集群本身"。

## 决策(修订)

改用 Kafka 本体,不用 Redpanda。

## 理由

- 公司现有生产环境已经在用 Kafka,团队对它熟悉,排障、监控、调优经验都是现成的。
- 这个平台的目标之一是未来能对接/替换现有生产系统(见 [ADR-003](003-no-hdfs-on-k8s.md)),消息队列这一层如果用了协议兼容但实现不同的 Redpanda,等真正对接生产 Kafka 集群时,细节行为差异(比如某些运维工具、监控指标、broker 级配置)可能需要重新踩坑。直接用同一个东西,经验可以复用。
- Redpanda 更省资源这个优势,在 local-lite 阶段用不上(Kafka 本来就不在 local-lite 的组件清单里,只在 cloud-full 才启用,那时资源不是本地 16GB 的约束了)。

## 后果

- Phase 2 在 `apps/kafka/` 下用标准 Kafka Helm chart(如 Bitnami 或 Strimzi Operator,具体选型到 Phase 2 落地时再定)。
- 之前 local-lite/cloud-full 组件清单和架构图里的 "Redpanda" 字样需要改回 "Kafka",已同步更新 `docs/architecture.md` 和架构 artifact。
