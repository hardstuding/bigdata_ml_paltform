# 等 cloud-full 环境再启用

这几个 Application 定义(Kafka/Spark Operator/Airflow)配置已经写好、语法验证过,
但**不在 `apps/definitions/` 里,所以本地 `apps-root` 不会自动同步它们**。

## 为什么挪出来

2026-08-08 晚上试过把这四个组件一起部署到本地 colima 虚拟机(6GB 内存),
直接把 VM 内存打满(5.8GB 用到只剩几十 MB,load average 36+),API server
TLS 握手超时、整个集群一度失联,靠重启 colima 才恢复。

这不是配置错误,是资源规划错误——这几个组件在 [`docs/architecture.md`](../../docs/architecture.md)
的组件清单里本来就标的是 cloud-full 阶段,不是 local-lite。当时图省事一次性全装,
违反了我们自己在 [ADR-004](../../docs/decisions/004-environment-profiles.md) 定的
规则:local-lite 不该同时跑 Phase 2 这些重组件。

## 部署时验证到的情况(供后续参考)

- **Spark Operator**:干净部署成功,`Synced`/`Healthy`。
- **Strimzi Kafka Operator + KRaft 单节点集群**:操作符和 Kafka broker 都正常
  跑起来了(`Running`),配置是对的。
- **Airflow**:配置本身应该没问题,但没跑到验证完——`airflow-db-init` 这个
  Job 还没来得及执行(数据库还没建),内存就先爆了,所以 Airflow 的 webserver/
  scheduler/triggerer/dag-processor 一直卡在等数据库迁移完成的 init 阶段,
  没有真正验证通。等重新启用时这部分要重新走一遍。

## 怎么重新启用

有了 cloud-full 环境(云服务器或公司 IDC,内存建议 ≥32GB)之后:

```bash
git mv environments/cloud-full/pending-definitions/*.yaml apps/definitions/
git commit -m "cloud-full: 启用 Kafka/Spark Operator/Airflow"
git push
```

ArgoCD 会自动同步。如果是全新集群,记得 `scripts/00-generate-secrets.sh`
里 airflow 相关的 Secret 生成逻辑要先跑一遍,再跑 `scripts/05-configure-airflow.sh`
建管理员账号。
