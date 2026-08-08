# 012. 分析师开发平台:dbt + Cosmos + OpenMetadata(经 MinIO 中转血缘文件)

- 状态: 已采纳(2026-08-09,方向已定,还未实施,Phase 4 之后落地)

## 背景

见 [ADR-011](011-seatunnel-not-airbyte.md) 和更早的架构讨论:希望分析师主要写 SQL/Python,
自动获得 Git 版本管理和血缘可视化,不直接暴露 Airflow 的完整灵活度(避免代码风格失控)。

## 决策

- 分析师的 SQL 建模用 **dbt Core**,模型文件走 Git,依赖关系用 `ref()`/`source()` 声明。
- dbt 不单独起服务,以 **Airflow Job** 形式跑(`KubernetesPodOperator`),用
  **Astronomer Cosmos** 把 dbt 项目拆成 Airflow 里逐模型可见、可重试的任务,
  不是一个不透明的大任务。
- dbt 跑完产出的 `manifest.json`/`catalog.json` 写入 **MinIO**(已有组件,
  不用新增基础设施),OpenMetadata 的 dbt connector 从 MinIO 读取,建立血缘。

## 理由

- dbt 在 k8s 上以 Job 形式跑、配合 Airflow 调度,是业界成熟模式,不是自己发明的架构。
- OpenMetadata 摄入 dbt 血缘的前提是能访问 `manifest.json`/`catalog.json`,
  但它不能直接读 dbt 实际运行的机器的文件系统,必须经对象存储中转——
  这正好是 MinIO 已经在做的事,不需要为这一件事再引入新组件。
- Cosmos 避免"Airflow 里只看到一个大黑盒任务"的问题,分析师/工程师排查
  单个模型失败时能直接在 Airflow UI 里定位,不用去翻 dbt 的日志文件。

## 后果

- 需要评估 dbt 项目的目录结构规范(哪些是分析师能碰的模型目录,哪些是
  工程师维护的 Airflow DAG/Cosmos 配置),这是"职责分离"这条原则(见
  ADR-011 背景里提到的担忧)真正落地的地方,现在还没细化。
- SeaTunnel(负责数据搬进湖仓)和 dbt(负责湖仓内部转换)是两个独立环节,
  不是同一个工具做两件事——数据先进来,再由 dbt 转换,顺序不能反。
