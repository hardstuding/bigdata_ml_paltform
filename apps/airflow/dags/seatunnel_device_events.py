# 验证 Phase 2 数据工程主线(见 docs/decisions/037-data-engineering-pipeline.md):
# Airflow 调度 SeaTunnel,SeaTunnel 通过 Hive Catalog 写进和 Trino/Spark
# 共用的同一个 Iceberg 表(demo.device_events)。
#
# 没有走 SeaTunnel 官方的 Airflow provider(apache-airflow-providers-*
# 里没有 seatunnel 这个包,SeaTunnel 社区也没发布独立的 provider),直接用
# SeaTunnel Zeta 引擎自带的 REST API(POST /hazelcast/rest/maps/submit-job,
# 见 apps/seatunnel/manifests/configmap.yaml 里 DATA endpoint group 那条
# 注释——这个 API 之前只验证过集群健康,从没真的提交过作业)。用标准库
# urllib 而不是额外装 requests,减少这个 DAG 的依赖面。
#
# MinIO 凭据从 Airflow Variable 读(scripts/14-configure-airflow-seatunnel-
# variable.sh 写入),不是 Secret 挂载——这个凭据只在拼 job 请求体这一刻
# 用得到,不需要像 apps/spark-iceberg-demo 那样在 pod 启动时就注入。
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime

from airflow.sdk import DAG, task
from airflow.sdk import Variable

SEATUNNEL_REST_URL = "http://seatunnel.seatunnel.svc.cluster.local:5801"
ICEBERG_NAMESPACE = "demo"
ICEBERG_TABLE = "device_events"


def _build_job_config(minio_key: str, minio_secret: str) -> dict:
    return {
        "env": {"job.mode": "batch"},
        "source": [
            {
                "plugin_name": "FakeSource",
                "plugin_output": "device_events_fake",
                "row.num": 20,
                "schema": {
                    "fields": {
                        "event_id": "int",
                        "device_id": "string",
                        "event_type": "string",
                        "value": "double",
                        "event_time": "timestamp",
                    }
                },
            }
        ],
        "transform": [],
        "sink": [
            {
                "plugin_name": "Iceberg",
                "plugin_input": ["device_events_fake"],
                "catalog_name": "seatunnel",
                "namespace": ICEBERG_NAMESPACE,
                "table": ICEBERG_TABLE,
                "iceberg.catalog.config": {
                    "type": "hive",
                    "uri": "thrift://hive-metastore.data.svc.cluster.local:9083",
                    "warehouse": "s3a://lakehouse/warehouse",
                },
                "hadoop.config": {
                    "fs.s3a.endpoint": "http://minio.minio.svc.cluster.local:9000",
                    "fs.s3a.access.key": minio_key,
                    "fs.s3a.secret.key": minio_secret,
                    "fs.s3a.path.style.access": "true",
                    "fs.s3a.connection.ssl.enabled": "false",
                    "fs.s3a.impl": "org.apache.hadoop.fs.s3a.S3AFileSystem",
                },
                "iceberg.table.write-props": {"write.format.default": "parquet"},
            }
        ],
    }


def _post_json(url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())




# KubernetesExecutor 起的任务 pod 默认不带任何 resources(BestEffort
# QoS)——排查一次任务被 SIGKILL(exit_code=-9)的问题时,在节点的
# kernel OOM 日志里发现这台机器当时确实在被反复 OOM(`journalctl -k`,
# 连 argocd-application-controller 都被连带杀了几次),BestEffort 的 pod
# 天然是 OOM killer 第一批目标。加个最小的 resources request,不求解决这台
# 机器整体资源紧张的根因,至少让这两个任务 pod 不是最先被杀的那批。
#
# pod_override 必须是真正的 kubernetes.client.models.V1Pod 对象,不能是
# 普通 dict——第一次直接传 dict 时 KubernetesExecutor 内部
# `PodGenerator.from_obj` 会做 isinstance(k8s.V1Pod) 检查,不通过就整个
# executor_config 判定为 invalid,任务连 pod 都起不来就直接 fail(实测
# 确认,不是猜的)。
from kubernetes.client import models as k8s

POD_OVERRIDE = {
    "pod_override": k8s.V1Pod(
        spec=k8s.V1PodSpec(
            containers=[
                k8s.V1Container(
                    name="base",
                    resources=k8s.V1ResourceRequirements(
                        # 256Mi 太紧——实测任务 pod 直接被 SIGKILL(exit_code=
                        # -9,这次没有任何 Python 异常堆栈,是容器自己的
                        # cgroup 内存上限被打到,不是节点级别 OOM)。Airflow
                        # 3.x 的任务运行时(SDK supervisor 进程 + 解析整个
                        # DAG 文件)本身占用就不小,调到 512Mi。
                        requests={"cpu": "50m", "memory": "256Mi"},
                        limits={"memory": "512Mi"},
                    ),
                )
            ]
        )
    )
}


@task(executor_config=POD_OVERRIDE)
def submit_seatunnel_job(**context) -> str:
    # 一开始用 context['ts_nodash'] 拼作业名,这个 DAG 是 schedule=None 手动
    # 触发,没有真正的 logical_date/data_interval——实测确认 Airflow 3.x
    # 在这种情况下压根不会往 context 里塞 ts_nodash 这个键,直接 KeyError。
    # 改用 run_id(手动触发的 dag_run 也一定有,格式是
    # "manual__2026-...+00:00")清理成安全的作业名。
    run_id = context["run_id"]
    safe_run_id = run_id.replace(":", "").replace("+", "").replace(".", "")
    minio_key = Variable.get("minio_access_key")
    minio_secret = Variable.get("minio_secret_key")
    job_config = _build_job_config(minio_key, minio_secret)
    job_name = f"device-events-{safe_run_id}"
    result = _post_json(
        f"{SEATUNNEL_REST_URL}/hazelcast/rest/maps/submit-job?jobName={job_name}",
        job_config,
    )
    if result.get("status") == "fail":
        raise RuntimeError(f"SeaTunnel 拒绝提交作业: {result.get('message')}")
    job_id = result["jobId"]
    print(f"已提交 SeaTunnel 作业 {job_name},jobId={job_id}")
    return job_id


@task(executor_config=POD_OVERRIDE)
def wait_for_completion(job_id: str) -> None:
    # SeaTunnel REST API 没有"阻塞直到完成"的接口,只能轮询 job-info——这个
    # demo 作业量很小,预期几秒到十几秒内跑完,轮询间隔不用做得太精细。
    deadline = time.time() + 300
    while time.time() < deadline:
        info = _get_json(f"{SEATUNNEL_REST_URL}/hazelcast/rest/maps/job-info/{job_id}")
        status = info.get("jobStatus")
        print(f"jobId={job_id} status={status}")
        if status == "FINISHED":
            return
        if status in ("FAILED", "CANCELED", "UNKNOWABLE"):
            raise RuntimeError(f"SeaTunnel 作业 {job_id} 失败: {info.get('errorMsg')}")
        time.sleep(10)
    raise TimeoutError(f"SeaTunnel 作业 {job_id} 5 分钟内没跑完")


# ---- ADR-011/ADR-051 之后补的表级血缘推送 ----
# 设计:表名直接从 SeaTunnel job 配置结构里读(ADR-011 原文的思路),不解析
# SQL——这个 DAG 的 sink 是 Iceberg 类型插件,配置里 namespace/table 是
# 明写的字段。OpenMetadata 里这张表的 databaseService 固定是 "trino"、
# database 固定是 "iceberg"(和 apps/table-registration-app/src/app.py 里
# `databaseSchema: f"trino.{catalog}.{schema}"` 是同一个注册方式,这个平台
# 目前所有 Iceberg 表都是经同一个 Trino service 注册进 OpenMetadata 的,
# 不是每个数据管道各自另起一套)——SeaTunnel job 配置里自己的
# `catalog_name`(这里是 "seatunnel")只是它自己连 Hive Metastore 用的
# 内部命名,和 OpenMetadata 的服务名无关,这里不能直接拿来拼 FQN。
OPENMETADATA_URL = "http://openmetadata.openmetadata.svc.cluster.local:8585"
PIPELINE_SERVICE_NAME = "airflow-platform"


def extract_sink_table_fqns(job_config: dict) -> list[str]:
    """从 SeaTunnel job 配置里的 sink 块提取真正落地的表(OpenMetadata FQN
    形式)。只认 Iceberg 类型的 sink——这个平台现在只有这一种表级 sink 在用,
    以后接 Jdbc/Hive 类型的 sink 时,这里要照着各自插件的字段名补对应分支,
    不能假设所有 sink 插件都用同一套 namespace/table 字段名。"""
    fqns = []
    for sink in job_config.get("sink", []):
        if sink.get("plugin_name") == "Iceberg":
            fqns.append(f"trino.iceberg.{sink['namespace']}.{sink['table']}")
    return fqns


def _om_request(method: str, path: str, token: str, body: dict | None = None) -> dict | None:
    """OpenMetadata API 请求的通用封装。404 返回 None(调用方按"这个实体
    还不存在"处理,不是异常),其他错误正常抛出——这个函数不吞掉真实的
    权限/网络错误,只吞"实体不存在"这一种,呼应下面 idempotent 创建逻辑的
    "先查再建"模式。"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{OPENMETADATA_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body_text = resp.read()
            return json.loads(body_text) if body_text else None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _ensure_pipeline_service(token: str) -> None:
    if _om_request("GET", f"/api/v1/services/pipelineServices/name/{PIPELINE_SERVICE_NAME}", token):
        return
    _om_request(
        "POST",
        "/api/v1/services/pipelineServices",
        token,
        {
            "name": PIPELINE_SERVICE_NAME,
            "serviceType": "Airflow",
            "description": "这个平台自己的 Airflow 实例(apps/airflow),给数据管道的 Pipeline 血缘节点用",
            "connection": {
                "config": {
                    "type": "Airflow",
                    "hostPort": "http://airflow-api-server.airflow.svc.cluster.local:8080",
                    "connection": {"type": "Backend"},
                }
            },
        },
    )


def _ensure_pipeline(token: str, dag_id: str) -> str:
    """返回这个 DAG 对应的 Pipeline 实体 id,不存在就先建(幂等)。"""
    existing = _om_request(
        "GET", f"/api/v1/pipelines/name/{PIPELINE_SERVICE_NAME}.{dag_id}", token
    )
    if existing:
        return existing["id"]
    created = _om_request(
        "POST",
        "/api/v1/pipelines",
        token,
        {
            "name": dag_id,
            "service": PIPELINE_SERVICE_NAME,
            "sourceUrl": f"http://airflow.local-lite.test/dags/{dag_id}",
        },
    )
    return created["id"]


def _resolve_table_id(token: str, table_fqn: str) -> str | None:
    result = _om_request("GET", f"/api/v1/tables/name/{table_fqn}", token)
    return result["id"] if result else None


@task(executor_config=POD_OVERRIDE)
def push_lineage(**context) -> None:
    """把这次运行写进的表登记进 OpenMetadata 的血缘图(Pipeline -> Table)。
    OPENMETADATA_TOKEN 没配就静默跳过——和这个项目其他"可选凭据未配置就
    降级"的模式一致(见 permission-request-app 的 WECOM_WEBHOOK_URL),不
    应该因为血缘这个附加能力没配好就让整条数据管道失败。目标表如果还没在
    OpenMetadata 里注册(比如还没通过 table-registration-app 建过),同样
    只记日志跳过,不是报错——这属于"目录信息滞后于实际数据"的正常过渡态,
    不是这个任务自己的 bug。"""
    token = Variable.get("openmetadata_token", default="")
    if not token:
        print("openmetadata_token 未配置,跳过血缘推送")
        return

    job_config = _build_job_config("", "")  # 只读 sink 结构,凭据字段这里用不到
    sink_fqns = extract_sink_table_fqns(job_config)
    if not sink_fqns:
        print("这个 job 配置里没有可识别的表级 sink,没有血缘可推送")
        return

    _ensure_pipeline_service(token)
    pipeline_id = _ensure_pipeline(token, context["dag"].dag_id)

    for fqn in sink_fqns:
        table_id = _resolve_table_id(token, fqn)
        if not table_id:
            print(f"目标表 {fqn} 还没在 OpenMetadata 里注册,跳过这条血缘边")
            continue
        _om_request(
            "PUT",
            "/api/v1/lineage",
            token,
            {
                "edge": {
                    "fromEntity": {"id": pipeline_id, "type": "pipeline"},
                    "toEntity": {"id": table_id, "type": "table"},
                }
            },
        )
        print(f"已推送血缘边:pipeline {context['dag'].dag_id} -> table {fqn}")

with DAG(
    dag_id="seatunnel_device_events",
    description="SeaTunnel FakeSource -> Iceberg(demo.device_events),验证 Phase 2 数据工程主线",
    start_date=datetime(2026, 8, 1),
    # 2026-08-29:从 schedule=None 改成定时。
    #
    # **为什么反悔**:这些 DAG 原本刻意设成手动触发("验证链路用的 demo,
    # 不是常驻定时任务")。但 08-29 做 dbt 血缘时撞到后果——dbt 产物是
    # `dbt_demo` 产出的,而它从 08-22 推倒重建之后就没人跑过,
    # `s3://lakehouse/dbt-artifacts/` **压根是空的**,血缘无从谈起。
    # 特征物化同理:不跑,Redis 里的在线特征就一直是旧的。
    # 一个"什么都要人手动点一下才会发生"的平台,谈不上生产可用。
    #
    # **为什么是这个时间**:三条 DAG 错开,不要在同一时刻和开机时几十个
    # 组件抢资源(Trino 就因为开机满载被 startupProbe 杀过,见
    # apps/components/trino.yaml)。`catchup=False` 保持不变——这台机器
    # 大部分时间关机,补跑历史区间没有意义,开机后跑最近这一次就够了。
    schedule="0 3 * * *",  # 每天 03:00 UTC,和前两条错开
    catchup=False,
    tags=["demo", "phase2", "seatunnel", "iceberg"],
) as dag:
    job_id = submit_seatunnel_job()
    wait_for_completion(job_id) >> push_lineage()
