# 定时作业

**在这里放一个作业,给它写个 `schedule`,它就会定时跑** —— 不用写 Airflow
DAG,也不用手动 `platform-submit`。

```
jobs/
  <作业名>/
    job.yaml     # 和 examples/ 里的 job.yaml 同一套字段,多一个 schedule
    job.py       # 作业本体
```

改完 `git push`,ArgoCD 会把它同步成一个 Argo `CronWorkflow`。

## 和别的提交方式的区别

| 方式 | 什么时候用 |
|---|---|
| `submit_job()`(notebook 里) | 试验、一次性跑 |
| `platform-submit job.yaml` | 从终端或 CI 手动提交一次 |
| **`jobs/` + `schedule`** | **要定时、要长期存在的作业** |
| Airflow DAG | 多步骤、步骤之间有依赖或要传数据 |

最后一行是真正的分界:**单步作业不该为了定时去写 DAG**。一个 `schedule`
字段能解决的事,不值得引入一个调度器的全部概念。

## job.yaml

```yaml
name: daily-order-summary   # 必填,同时是 CronWorkflow 的名字
script: job.py              # 必填
schedule: "0 2 * * *"       # 有它才会生成 CronWorkflow;没有就只是个模板
timezone: "Asia/Shanghai"   # 可选,默认 UTC
cpu: "500m"                 # 可选
memory: "1Gi"               # 可选
owner_group: data-analysts  # 可选,决定用哪个组的资源配额(Kueue 队列)
env:                        # 可选
  LOOKBACK_DAYS: "7"
```

## 几件必须知道的事

- **`name` 会成为 Kubernetes 资源名**,只能用小写字母、数字和连字符。
- **`owner_group` 必须是 `platform/iam/groups.yaml` 里真实存在的组**,
  否则作业会一直排队等一个不存在的队列 —— CI 会拦住这种情况。
- **`schedule` 用 UTC**,除非显式写了 `timezone`。
- 作业跑失败不会自动重试。要重试逻辑,自己在脚本里做,或者用 Airflow。
- 定时作业和临时作业共用 `argo-workflows` 命名空间的资源配额。加作业之前
  想一下会不会把配额吃满 —— 超配额的表现是 Workflow 卡在 Pending。

## 加一个新作业

```bash
cp -r examples/batch-etl jobs/my-daily-job
# 改 jobs/my-daily-job/job.yaml:改 name、加 schedule
python3 scripts/render-jobs.py      # 生成 manifest
git add -A && git commit && git push
```

`render-jobs.py` 生成的东西在 `apps/platform-jobs/manifests/`,**不要手改**
——CI 会校验它和 `jobs/` 不漂移。
