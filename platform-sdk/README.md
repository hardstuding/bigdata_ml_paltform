# platform_sdk

这个数据+AI平台的薄客户端:连接封装 + 作业提交。设计背景和边界见
[ADR-058](../docs/decisions/058-lightweight-developer-experience.md)——
**只做这两件事**,不是一个平台,不会长成一个平台。

## 装在哪、怎么用

正常情况下你**不需要自己 pip install**——JupyterHub notebook 和调度任务
用的统一镜像(`apps/platform-image/`)里已经装好了。这份文档是给两类人看的:
(1) 想在本机 IDE 里用同一套连接方式的人,(2) 要改 SDK 本身的人。

本机安装(可编辑模式,改代码立刻生效):

```bash
cd platform-sdk
pip install -e ".[submit]"
```

## 三个最常用的函数

### 查数据,不用自己填 Trino 连接串

```python
from platform_sdk import query

df = query("select * from iceberg.demo.orders limit 10")
```

`query()` 走的 Trino 地址/端口/catalog 是平台默认值(集群内 Service DNS),
账号密码要通过环境变量提供:

```bash
export PLATFORM_TRINO_USER=你的服务账号名
export PLATFORM_TRINO_PASSWORD=对应密码
```

这两个变量现在还没有"自动带上当前登录用户凭据"这种体验(见
`docs/usage-guide.md` 的说明,这条明确没做),要自己去
`trino-service-account` 这个 Secret 或者对应组件的账号里找。

需要更细的控制,用 `trino_connection()` 拿到原生 DBAPI 连接对象。

### MLflow 实验跟踪,一行配好

```python
from platform_sdk import mlflow_setup

mlflow = mlflow_setup("my-experiment")
with mlflow.start_run():
    mlflow.log_param("n_estimators", 100)
    mlflow.log_metric("accuracy", 0.9)
    mlflow.sklearn.log_model(model, name="model")
```

### 把本地脚本提交到集群跑

**方式一,直接调用:**

```python
from platform_sdk import submit_job, job_status, job_logs

wf_name = submit_job("my-training", "train.py")
print(job_status(wf_name))   # Pending / Running / Succeeded / Failed / Error
print(job_logs(wf_name))
```

**方式二,写一份 `job.yaml`(参考 `../examples/hello-job/`):**

```yaml
name: my-training
script: train.py
# 下面这些都有默认值,不填就用平台统一镜像/默认资源配额:
# **不写死镜像名**:统一镜像按环境不同(本地开发是本地构建的那份,云端是
# ACR 上带 commit SHA 的那份),值在 environments/<env>/config.yaml 的
# platform_job_image。要覆盖才写这一行。
# image: <你自己的镜像>
# cpu: 200m
# memory: 512Mi
```

```bash
platform-submit job.yaml
```

两种方式是同一套底层逻辑,`job.yaml` 的字段和 `submit_job()` 的参数
一一对应,没有另外一套配置语法要学。

### 触发平台已经部署好的 WorkflowTemplate

和上面"提交自己的脚本"是两回事——这个是触发平台已经声明式部署好的
Argo WorkflowTemplate(比如训练流程 `train-demo-model`),不是重新写
一份等价的脚本:

```python
from platform_sdk import run_workflow_template

wf_name = run_workflow_template("train-demo-model")
```

模板自己的镜像/资源/凭据都已经配好,调用方不用管。返回的名字一样能传
给 `job_status()`/`job_logs()` 查状态、看日志。

## 环境变量一览

| 变量 | 作用 | 有没有默认值 |
|---|---|---|
| `PLATFORM_TRINO_USER` / `PLATFORM_TRINO_PASSWORD` | Trino 账号密码 | 没有,必须自己设 |
| `PLATFORM_TRINO_HOST` / `PORT` / `SCHEME` / `CATALOG` / `SCHEMA` | Trino 连接细节 | 有(集群内默认值) |
| `MLFLOW_TRACKING_URI` | MLflow server 地址 | 有(集群内默认值) |
| `MLFLOW_S3_ENDPOINT_URL` / `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | MinIO/S3 | 地址有默认值,账号密码没有 |
| `PLATFORM_ARGO_NAMESPACE` | 作业提交到哪个命名空间 | 有,`argo-workflows` |
| `PLATFORM_JOB_IMAGE` | 作业默认用哪个镜像 | 有,统一镜像 |

**在本机 IDE 里用**:先 `kubectl port-forward` 把 Trino/MLflow/MinIO 转发
到本地端口,再把上面的地址类变量覆盖成 `localhost:<端口>` 就行——和
`scripts/09-train-demo-model.sh` 里的做法是同一个思路,`platform_sdk`
只是把这个思路固化成库,不是发明新东西。

## 这个 SDK 不做什么

不做 ORM、不做 DAG 编排 DSL、不做多文件项目打包(单文件脚本走
ConfigMap,几百 KB 内;真正的多文件项目应该走 git,这是以后要补的能力,
见 ADR-058"实施顺序"那节)。任何"顺手加个功能"的想法,先记
`docs/project/roadmap.md`,不要直接塞进这个包。
