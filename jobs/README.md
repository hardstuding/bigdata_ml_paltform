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
script: job.py              # 必填,入口文件
schedule: "0 2 * * *"       # 有它才会生成 CronWorkflow;没有就只是个模板
timezone: "Asia/Shanghai"   # 可选,默认 UTC
cpu: "500m"                 # 可选
memory: "1Gi"               # 可选
owner_group: data-analysts  # 可选,决定用哪个组的资源配额(Kueue 队列)
env:                        # 可选,固定的环境变量
  LOOKBACK_DAYS: "7"

requires:                   # 可选,声明用到的第三方包
  - trino
params:                     # 可选,作业参数(补数靠它)
  run_date: ""
environments:               # 可选,不写 = 所有环境
  - cloud-full
```

## 多文件作业

**作业目录下所有 `.py` 都会被一起挂进容器**,`import` 直接可用:

```
jobs/my-job/
  job.yaml
  job.py        # 入口(job.yaml 里的 script)
  jobkit.py     # from jobkit import run_date  ← 直接能用
```

**不支持子目录**。ConfigMap 的 key 不能带 `/`,要支持嵌套就得自己编码路径
再在容器里还原 —— 那是在 ConfigMap 上模拟文件系统。真需要多层结构的代码,
该打成一个内部包发布([ADR-083](../docs/decisions/083-internal-package-registry.md)),
而不是继续往 ConfigMap 里塞。

## 依赖:声明,但平台不会替你装

```yaml
requires: [trino, pandas]
```

这些名字会和 `apps/platform-image/requirements.txt` 对账。**写了镜像里没有
的包,CI 直接红** —— 而不是等作业半夜跑到 `import` 那一行才
`ModuleNotFoundError`。

**平台不会在运行时 `pip install` 任何东西**,这是这个项目明确记过的反模式:
每次 pod 重启重装一遍,遇到这片网络的 PyPI 限速直接卡死探针预算(2026-08-16
真实故障,SIGKILL 5 分 44 秒整)。需要新包就加进那份 requirements.txt 并重建
镜像;是自己写的包就按 ADR-083 发布,它已经在镜像的 pip 索引里。

## 参数和补数

```yaml
params:
  run_date: ""     # 空 = 用今天
```

平台把参数注成 `PARAM_<大写名>` 环境变量。**补数就是带着不同的参数提交一次**:

```bash
argo submit --from cronwf/daily-order-summary -n argo-workflows \
  -p run_date=2026-08-01
```

没有参数的话,重跑一个日更作业只会再算一遍今天 —— 那不叫补数。
`jobs/daily-order-summary/jobkit.py` 里有一个可以照抄的 `param()` 读法,
包括"Argo 没替换到值时会留下 `{{workflow.parameters.x}}`"这个坑的处理。

## 晋级:dev → test → prod

```yaml
environments:
  - cloud-full        # 先只在这里跑
```

**晋级就是往这个列表里加一个环境名**,不是复制一份 yaml 到别处。不写
`environments` 等于所有环境都生效。

生成命令按环境给,和 `render-environment-config.py` 一个写法:

```bash
python3 scripts/render-jobs.py cloud-full
```

**校验对所有作业都做,不管它在不在当前环境** —— 否则"只在 prod 生效"的
作业可以一直绕过检查,等真的晋级那天一次性爆出来。

## 几件必须知道的事

- **`name` 会成为 Kubernetes 资源名**,只能用小写字母、数字和连字符。
- **`owner_group` 必须是 `platform/iam/groups.yaml` 里真实存在的组**,
  否则作业会一直排队等一个不存在的队列 —— CI 会拦住这种情况。
- **`owner_group` 还要和你本人所属的组对得上。** 它决定占用哪个组的计算
  配额,填一个自己不在的组等于蹭别人的配额,而且从 Workflow 上完全看不
  出来。CI 拿这个作业目录最后一次提交的作者去对账 `memberships.csv`。
  要给别的组建作业,让那个组的人来提交这次改动。

  > **今天这条检查其实不生效**,如实说清楚:真实的 git 提交邮箱是个人邮箱,
  > 而 `platform/iam/employees.csv` 里是占位 demo 数据,两边对不上,于是每次
  > 都走"拿不到身份 → 放行"。机制是对的,接上真实 HR/IdP 数据之后自动生效。
  > `render-jobs.py` 每次都会把跳过的作业和它的提交邮箱打出来 —— 不打的话,
  > 这就成了又一个"看起来有检查、其实永远走 else"的东西,而这个仓库已经
  > 因为那个模式栽过三次。
- **`schedule` 用 UTC**,除非显式写了 `timezone`。
- 作业跑失败不会自动重试。要重试逻辑,自己在脚本里做,或者用 Airflow。
- 定时作业和临时作业共用 `argo-workflows` 命名空间的资源配额。加作业之前
  想一下会不会把配额吃满 —— 超配额的表现是 Workflow 卡在 Pending。

## 加一个新作业

```bash
cp -r examples/batch-etl jobs/my-daily-job
# 改 jobs/my-daily-job/job.yaml:改 name、加 schedule
python3 scripts/render-jobs.py cloud-full   # 生成 manifest
git add -A && git commit && git push
```

`render-jobs.py` 生成的东西在 `apps/platform-jobs/manifests/`,**不要手改**
——CI 会校验它和 `jobs/` 不漂移。
