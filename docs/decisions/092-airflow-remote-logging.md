# ADR-092:Airflow 的任务日志写 MinIO,不然失败一次就查不到原因

日期:2026-09-02
状态:**已实现并实机验证(重跑一次失败任务,日志从 MinIO 读得到)**

## 问题

排查 `dbt_demo` 这条 DAG 为什么在 2026-09-02 失败时,界面上和 API 里
拿到的全部内容是这一句:

```
Could not read served logs: HTTPConnectionPool(
  host='dbt-demo-dbt-build-and-upload-4hom9itf', port=8793):
  Failed to resolve 'dbt-demo-dbt-build-and-upload-4hom9itf'
```

Airflow 去连那个跑任务的 pod 的 8793 端口要日志 —— **而那个 pod 早就
不存在了**。KubernetesExecutor 的 task pod 跑完即删,日志跟着一起没。

结果是:**那次失败的原因永远查不到了**,只能重跑一次看它还失不失败。
对一个要上生产的调度平台,这不是"体验不好",是排障能力等于零。

顺带说明这个坑为什么没早点暴露:平时看的都是**成功**的运行,而成功的
运行没人去点日志。只有失败时才需要它,而那时已经晚了。

## 决定

任务日志写 MinIO(`s3://airflow-logs`),连接用 `AIRFLOW_CONN_MINIO_S3`
环境变量声明。

**为什么用环境变量而不是在数据库里建 Connection 记录**:环境变量形式的
连接是声明式的 —— 从空环境拉起时不需要额外跑一步命令式配置,也不会因为
有人在界面上改过它而漂移。整串 JSON(里面有 MinIO 密码)由
`scripts/00-generate-secrets.sh` 拼好放进 `airflow-remote-logging` 这个
Secret。

**这一份 Secret 每次都覆盖**,不走 `ensure_secret` 那套"已存在就跳过":
MinIO 密码轮换之后,一份对不上的连接串会让远程日志**静默失效** ——
任务照常跑完,只是日志写不上去。那比报错难发现得多。

**配置项用 `env:` 而不是 chart 的 `config.logging.*`**:这个 chart 的
`config:` 合并只认预置在模板里的固定键,新加的键会被静默丢掉(同一份
文件里 `proxy_fix_x_port` 那段注释记着实测结果)。

## 代价

- 多占 MinIO 的空间。日志没有配保留策略,后面要加(记在
  `docs/project/roadmap.md`)。
- 任务日志多一跳网络。MinIO 在同一个集群里,这一跳可以忽略。

## 没做的

**没有配失败告警。** 这一档按约定是"留好配置、上生产再接真实渠道"。
不过日志能查是告警的前提 —— 收到"某个任务失败了"却查不到日志,和没有
告警差不多。
