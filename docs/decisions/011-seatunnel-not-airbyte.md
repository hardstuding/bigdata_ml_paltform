# 011. 数据集成工具:SeaTunnel(不是 Airbyte),自建血缘

- 状态: 已采纳(2026-08-09)

## 背景

Phase 2 数据管道原计划用 SeaTunnel,后来发现它没有官方 Helm chart、也没有和
OpenMetadata/DataHub 的血缘集成,一度考虑换成 Airbyte(社区更大、有官方
OpenMetadata pipeline connector)。调研之后又发现 Airbyte 那个官方血缘集成
本身历史上反复出问题([open-metadata/OpenMetadata#26993](https://github.com/open-metadata/OpenMetadata/issues/26993)
等多个 issue),不是装上就能用的成熟功能。

## 决策

只装一个数据集成工具:**SeaTunnel**。不引入 Airbyte。

血缘不依赖任何工具自带的"官方集成",自己按需要建:
- **表级血缘**:SeaTunnel 的 job 配置是结构化的 HOCON/JSON,`source`/`sink`
  块的表名是明写的,直接读配置结构提取,不需要解析 SQL。
- **列级血缘**(用到 SeaTunnel 内置 SQL transform 的场景):复用用户在
  DataX 上已经验证过的思路——用 SQL 解析库提取,但优先评估 **sqlglot**
  (DataHub 用的,方言覆盖更广、号称 97-99% 准确率)而不是 sqllineage
  (OpenMetadata 内置用的,但方言兼容性issues 更明显,数据库语法升级容易
  跟不上——这是用户基于 DataX 二次开发经验提出的真实顾虑)。
- 解析结果通过 OpenMetadata 官方支持的自定义血缘 API(`PUT /api/v1/lineage`)
  写入,这是一等公民的扩展点,不是野路子。

## 理由

- **功能匹配度**:SeaTunnel 支持自动建表/改表、CDC 全库同步、跑在 Flink 上
  做真正的低延迟流式同步;Airbyte 的"实时"本质是间隔轮询式 CDC,做不到
  同等延迟。
- **现有系统兼容**:团队现在用 Doris,SeaTunnel 对 Doris 这类项目的连接器
  支持通常比 Airbyte(偏西方 SaaS/云仓)更好。
- **复用已有经验**:用户已经有基于 SQL 解析做血缘的 DataX 二次开发经验,
  这套能力可以直接迁移到 SeaTunnel 上,不是从零学一个新工具的血缘方案。
- 社区规模(Airbyte GitHub star 数是 SeaTunnel 的 2 倍多)是权衡过的已知
  代价,不是没考虑到——用户明确表示在功能匹配和复用经验面前,这个代价可以接受。
- 只装一个工具,不同时维护 SeaTunnel + Airbyte 两套重叠能力,避免为不确定
  会用到的场景预先背维护成本(和 [ADR-010](010-optional-components-versioning.md)
  的"按需引入"原则一致)。

## 后果

- 没有官方 Helm chart,部署方式是手写 StatefulSet + ConfigMap(Zeta 引擎),
  比 Airbyte 装官方 chart 麻烦一些,是已知、接受的代价。
- Kafka Connect + Iceberg Sink Connector 仍然保留用于纯流式/CDC 直接写入
  Iceberg 的场景——这条路径独立于批量集成工具的选择,不受这次决策影响。
- 血缘解析这部分是需要自己维护的代码,不是开箱即用的官方功能,和
  "分析师开发平台"那批调研(dbt + OpenMetadata,见 `docs/architecture.md`
  路线图)放在一起做,不单独立项。
