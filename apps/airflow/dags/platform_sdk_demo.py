# 证明 ADR-058(docs/decisions/058-lightweight-developer-experience.md)
# "环境一致"这条设计承诺真的成立:JupyterHub notebook / Argo Workflows /
# Airflow 三处都能跑同一份用 platform_sdk 写的脚本,同一个统一镜像
# (local/platform-runtime),不用各自单独适配。这个 DAG 跑的
# examples/hello-job/job.py 和 notebook 里手动跑、`submit_job()` 提交给
# Argo 跑的是完全同一份文件(通过下面的 ConfigMap 挂载,内容和
# examples/hello-job/job.py 保持同步——同一个模式见
# apps/argo-workflows-training-image/manifests/script-configmap.yaml)。
#
# 2026-08-19 晚些时候新增,回应 使用方之前的追问"同时 airflow 里也能用
# 等同的环境吗"——当时只是设计上承诺了"是同一个镜像,不是等同",这个 DAG
# 才是真正把这条承诺落地验证。

from __future__ import annotations

import os

from datetime import datetime

from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.sdk import DAG
from kubernetes.client import models as k8s

# **不写死。** 值来自 environments/<env>/config.yaml 的 platform_job_image,
# 由 scripts/check-platform-image-refs.py 保证这行和配置一致(CI 里跑)。
# 环境变量优先:Airflow worker 上如果注入了 PLATFORM_JOB_IMAGE 就用它。
PLATFORM_IMAGE = os.environ.get(
    "PLATFORM_JOB_IMAGE",
    "crpi-t6h2mzjka4hzoldo.cn-hangzhou.personal.cr.aliyuncs.com/bigdata-platform/platform-runtime:996dab804e354719e62da280970434997c7cbdf4")

SCRIPT_VOLUME = k8s.V1Volume(
    name="job-script",
    config_map=k8s.V1ConfigMapVolumeSource(name="platform-sdk-demo-script"),
)
SCRIPT_MOUNT = [
    k8s.V1VolumeMount(
        name="job-script",
        mount_path="/tmp/job.py",
        sub_path="job.py",
    ),
]

# 独立的 Trino 服务账号(ADR-021:各组件各自独立账号,方便单独追溯/吊销),
# 不复用 dbt_demo_service/superset_service 这些已有账号。
TRINO_ENV = [
    k8s.V1EnvVar(name="PLATFORM_TRINO_USER", value="platform_sdk_demo_service"),
    k8s.V1EnvVar(
        name="PLATFORM_TRINO_PASSWORD",
        value_from=k8s.V1EnvVarSource(
            secret_key_ref=k8s.V1SecretKeySelector(
                name="trino-service-account", key="password-platform_sdk_demo_service"
            )
        ),
    ),
]

# MLflow 的 artifact 落 MinIO,凭据和 apps/argo-workflows-training-image/
# manifests/workflow-template.yaml 里训练 workflow 用的是同一个 Secret,
# 同一组标准变量名(platform_sdk.connect 认的就是这几个)。
MINIO_ENV = [
    k8s.V1EnvVar(
        name="AWS_ACCESS_KEY_ID",
        value_from=k8s.V1EnvVarSource(
            secret_key_ref=k8s.V1SecretKeySelector(name="minio-root", key="rootUser")
        ),
    ),
    k8s.V1EnvVar(
        name="AWS_SECRET_ACCESS_KEY",
        value_from=k8s.V1EnvVarSource(
            secret_key_ref=k8s.V1SecretKeySelector(name="minio-root", key="rootPassword")
        ),
    ),
]

CONTAINER_RESOURCES = k8s.V1ResourceRequirements(
    requests={"cpu": "200m", "memory": "512Mi"},
    limits={"memory": "1Gi"},
)

# 见 feast_materialize.py 同一段注释的完整解释:KubernetesExecutor 的
# "任务运行时" pod(airflow 命名空间)和这个 DAG 实际拉起的目标 pod
# (platform-sdk-demo 命名空间)是两层不同的资源,ADR-041 的 batch
# 优先级两层都要加,不能只加目标 pod 这一层。
EXECUTOR_POD_OVERRIDE = {
    "pod_override": k8s.V1Pod(
        spec=k8s.V1PodSpec(
            priority_class_name="batch",
            containers=[
                k8s.V1Container(
                    name="base",
                    resources=k8s.V1ResourceRequirements(
                        requests={"cpu": "100m", "memory": "512Mi"},
                        limits={"memory": "1Gi"},
                    ),
                )
            ],
        )
    )
}

with DAG(
    dag_id="platform_sdk_demo",
    description="验证 ADR-058:同一份 platform_sdk 脚本在 Airflow 里也能跑,和 notebook/Argo Workflows 用同一个镜像",
    start_date=datetime(2026, 8, 1),
    # 这是一次性验证用的 demo,不是真实业务流水线,不设定时——和
    # feast_materialize 当初"演示/验证阶段手动触发为主"是同一个判断。
    schedule=None,
    catchup=False,
    tags=["demo", "adr-058", "platform-sdk"],
) as dag:
    run_hello_job = KubernetesPodOperator(
        task_id="run_hello_job",
        name="platform-sdk-demo-hello-job",
        namespace="platform-sdk-demo",
        image=PLATFORM_IMAGE,
        # 本地构建镜像,不是从任何 registry 拉——和这个项目其它自建镜像
        # (feast/superset/argo-training)同一个坑,必须是 IfNotPresent,
        # 不然 kubelet 会尝试联网拉一个不存在的远程镜像。
        image_pull_policy="IfNotPresent",
        cmds=["python3"],
        arguments=["/tmp/job.py"],
        volumes=[SCRIPT_VOLUME],
        volume_mounts=SCRIPT_MOUNT,
        env_vars=TRINO_ENV + MINIO_ENV,
        container_resources=CONTAINER_RESOURCES,
        priority_class_name="batch",
        # 和 feast_materialize 同一条理由:KubernetesPodOperator 判断
        # 成功/失败靠退出码,不靠这里拉日志,拉日志失败(pod 被清理太快等)
        # 不应该让整个任务状态跟着变得不确定;日志本来就有 Loki/Alloy 兜底。
        get_logs=False,
        is_delete_operator_pod=True,
        startup_timeout_seconds=300,
        executor_config=EXECUTOR_POD_OVERRIDE,
    )
