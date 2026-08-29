# 作业模板

四个能直接跑的模板,覆盖平台上最常见的四类活。**先跑通一个,再改成自己的**
——不用先读完架构文档。

| 模板 | 干什么 | 跑通之后你就知道 |
|---|---|---|
| [`hello-job`](hello-job/) | 查一下 Iceberg 里的 demo 数据 | 提作业不用手填连接串、不用管 K8s |
| [`batch-etl`](batch-etl/) | 从 Iceberg 读 → 算 → 写回 Iceberg | 批处理该怎么落地 |
| [`train-model`](train-model/) | 取数 → 训练 → 记实验 → 注册模型 | 模型怎么进注册表 |
| [`data-quality-check`](data-quality-check/) | 查一张表 → 断言 → 不合格就非零退出 | 质量卡点怎么写成作业 |

## 30 秒跑第一个

在 JupyterHub 的 notebook 里(镜像里已经带了 `platform_sdk`):

```python
from platform_sdk import submit_job, job_status
name = submit_job(name="hello-job", script="job.py")
job_status(name)
```

或者从终端 / CI 提交:

```bash
platform-submit examples/hello-job/job.yaml
```

## 从模板开一个自己的

```bash
platform-submit --list-templates          # 看有哪些
platform-submit --new train-model --into my-first-model
```

生成的目录里是 `job.py` + `job.yaml` 两个文件,改完直接提交。

## job.yaml 里能写什么

字段和 `platform_sdk.submit.submit_job()` 的参数**一一对应,没有第二套
schema 要学**。只有 `name` 和 `script` 是必填:

```yaml
name: my-job              # 必填
script: job.py            # 必填
cpu: "500m"               # 不填用默认 200m
memory: "1Gi"             # 不填用默认 512Mi
image: ...                # 不填用平台统一镜像(带 platform_sdk / Trino / MLflow 客户端)
env:                      # 传给作业的环境变量
  MY_PARAM: value
```

## 几件值得先知道的事

- **资源配额按组走**。作业会自动带上你所在组的队列标签(Kueue),超出本组
  配额时会排队;同一个 cohort 里别的组有空闲,可以借。不用自己填队列名。
- **不用管镜像**。默认镜像里已经有 `platform_sdk`、Trino 客户端、MLflow
  客户端、scikit-learn 等常用包。要额外的包再换 `image`。
- **提交 ≠ 上线**。训练出来的模型进的是 MLflow 注册表,上线还要走审批
  (`scripts/41-approve-model.sh`)和部署(`scripts/11-...`),见
  [ADR-080](../docs/decisions/080-model-approval-and-rollback.md)。
- CI 会校验这四个模板一直可用(`scripts/check-job-examples.py`)——模板是
  最容易腐烂的东西,一旦没人跑,坏了也不会有人发现。

平台整体怎么用,看 [`docs/QUICKSTART.md`](../docs/QUICKSTART.md);
每个工具是干什么的、归谁,看
[`docs/operations/service-catalog.md`](../docs/operations/service-catalog.md)。
