# 流式作业

**写一个 PyFlink 脚本 + 一个几行的 `stream.yaml`,push,流作业就跑起来了。**

在这之前,加一个流作业要手写 ~140 行 `FlinkDeployment`:S3A 凭据从哪个
Secret 取、checkpoint 间隔、Prometheus 指标端口、脚本 ConfigMap 怎么挂、
PyFlink 的 `jarURI`/`entryClass`/`args` 那三行魔法 —— 全靠从别的作业抄。
抄错一处的表现通常不是报错,是作业起来了但不写数据。

```
streams/
  <作业名>/
    stream.yaml   # 几行配置
    job.py        # PyFlink 脚本
```

## stream.yaml

```yaml
name: device-events            # 必填,同时是 FlinkDeployment 的名字
script: job.py                 # 必填
parallelism: 1                 # 可选,默认 1
checkpoint_interval: "30s"     # 可选,默认 30s
jobmanager_memory: "1024m"     # 可选
taskmanager_memory: "1792m"    # 可选
cpu: 0.3                       # 可选,JM/TM 各自的 CPU
environments:                  # 可选,不写 = 所有环境
  - cloud-full
```

**多文件和按环境晋级和 `jobs/` 是同一套语义**(2026-08-29 对齐):作业目录
下所有 `.py` 都会被挂进容器;`environments` 里加一个环境名就是晋级。两边
语义不一致本身就是个坑 —— 一个人在 `jobs/` 里学会的东西,到 `streams/`
发现不认,只会以为自己写错了。

生成命令也按环境给:

```bash
python3 scripts/render-streams.py cloud-full
```

**校验对所有流作业都做,不管它在不在当前环境。**

**平台自动给你配好的**(不用写、也不该在脚本里重复配):

| 东西 | 怎么来的 |
|---|---|
| MinIO / S3A 凭据 | 从 `minio-root` Secret 注入成 `AWS_*` 环境变量 |
| Iceberg warehouse 和 Hive Metastore 地址 | 和 Trino/Spark 是同一份,脚本里直接用 |
| Checkpoint | 按 `checkpoint_interval` 开好 |
| Prometheus 指标 | 9249 端口 + reporter 配好,Grafana 能直接看到 |
| 脚本挂载 | `job.py` 打进 ConfigMap 挂到 `/opt/flink/usrlib/` |

## 和批作业的分界

| | 用什么 |
|---|---|
| 跑一次就结束 | [`../jobs/`](../jobs/)(定时)或 `platform-submit`(手动) |
| **一直跑、处理不断到来的数据** | **这里** |

## 加一个新流作业

```bash
mkdir -p streams/my-stream
# 写 stream.yaml 和 job.py(照 streams/device-events/ 改最快)
python3 scripts/render-streams.py
git add -A && git commit && git push
```

生成物在 `apps/platform-streams/manifests/`,**不要手改** —— CI 会校验它和
`streams/` 不漂移。

## 几件必须知道的事

- **`upgradeMode: stateless`**:改了脚本重新部署会**从头开始**,不接着上次
  的位点。要有状态升级(savepoint)得改成 `savepoint` 模式并配存储,现在
  没配 —— demo 场景不需要,真实场景要先想清楚。
- **flink 命名空间有资源配额**。加作业前想一下 TM 内存会不会把配额吃满,
  超了的表现是 pod 建不出来(和 Airflow 那次一样)。
- **PyFlink 版本必须和集群的 Flink 一致**(现在是 1.20.5),镜像里已经对齐,
  脚本里不要再自己 pip install apache-flink。
