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
                        requests={"cpu": "50m", "memory": "128Mi"},
                        limits={"memory": "256Mi"},
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


with DAG(
    dag_id="seatunnel_device_events",
    description="SeaTunnel FakeSource -> Iceberg(demo.device_events),验证 Phase 2 数据工程主线",
    start_date=datetime(2026, 8, 1),
    schedule=None,  # 手动触发,不是常驻定时任务——这是验证链路用的 demo
    catchup=False,
    tags=["demo", "phase2", "seatunnel", "iceberg"],
) as dag:
    job_id = submit_seatunnel_job()
    wait_for_completion(job_id)
