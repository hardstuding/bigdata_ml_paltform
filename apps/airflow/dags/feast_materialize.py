# 定时把 Feast 的离线特征(Spark 读 Iceberg)物化进 Redis 在线存储,见
# docs/decisions/042-feast-feature-store.md。
#
# 用 KubernetesPodOperator 而不是 @task(跑在 Airflow worker 自带的镜像
# 里)——feast+pyspark+JRE 这套依赖不轻(pyspark 一个包解压后几百 MB),
# 每次任务运行现装一遍既慢又占带宽。改用
# apps/feast/feature-server-image/Dockerfile 构建的自定义镜像
# (local/feast-feature-server:0.65.0-spark,和 Feast Serving 本身用的是
# 同一个镜像,見那份 Dockerfile 顶部注释——两边都要 pyspark+JRE 是 Feast
# 自身的架构限制,不是各自单独的选择)。这个镜像目前只在这台机器的本地
# docker 里,没有推到任何 registry——image_pull_policy 设成 Never,k3s 的
# docker 运行时能直接用本地镜像(这台机器的常规模式,见
# scripts/17-load-image-cache.sh 的说明);换到真正的多节点集群时,这个
# 镜像需要改成推到一个大家都能拉到的 registry,这是已知的后续课题,不是
# 这次疏漏。
from __future__ import annotations

from datetime import datetime

from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.sdk import DAG
from kubernetes.client import models as k8s

FEAST_IMAGE = "local/feast-feature-server:0.65.0-spark"

# feature_store.yaml/definitions.py 权威源是 scripts/feast_feature_repo/,
# 部署态的拷贝在 apps/feast/manifests/feature-repo-configmap.yaml——和
# Feast Serving 的 feature_store_yaml_base64 是同一类"chart/Operator 只认
# 静态内容,不能引用仓库里的文件"限制,不是这次偷懒复制的。
FEATURE_REPO_VOLUME = k8s.V1Volume(
    name="feature-repo",
    config_map=k8s.V1ConfigMapVolumeSource(name="feast-feature-repo"),
)
FEATURE_REPO_MOUNT = k8s.V1VolumeMount(
    name="feature-repo",
    mount_path="/feature_repo",
)

MINIO_ENV = [
    k8s.V1EnvVar(
        name="MINIO_ACCESS_KEY",
        value_from=k8s.V1EnvVarSource(
            secret_key_ref=k8s.V1SecretKeySelector(name="minio-root", key="rootUser")
        ),
    ),
    k8s.V1EnvVar(
        name="MINIO_SECRET_KEY",
        value_from=k8s.V1EnvVarSource(
            secret_key_ref=k8s.V1SecretKeySelector(name="minio-root", key="rootPassword")
        ),
    ),
]

# KubernetesExecutor 这台机器上默认 BestEffort QoS 的 pod 是 OOM killer
# 第一目标(见 seatunnel_device_events.py 里同样的教训),pyspark 本地模式
# 启动一个 JVM,内存要给够,不能沿用那个 DAG 的 256Mi/512Mi。这是
# KubernetesPodOperator 实际拉起来跑 feast 命令那个目标 pod(在 feast
# 命名空间)的资源。
CONTAINER_RESOURCES = k8s.V1ResourceRequirements(
    requests={"cpu": "300m", "memory": "1Gi"},
    limits={"memory": "2Gi"},
)

# 这条是另一层、容易漏掉的坑:KubernetesPodOperator 自己的调度/轮询逻辑
# 跑在 KubernetesExecutor 起的"任务运行时" pod 里(在 airflow 命名空间,
# 不是上面 CONTAINER_RESOURCES 覆盖的那个 feast 命名空间 pod)——这层用的
# 是平台级默认值(apps/definitions/airflow.yaml 里 workers.kubernetes.
# resources,256Mi/512Mi),第一次跑就被实测证实不够:任务进程直接
# SIGKILL(exit_code=-9),不是我这次自定义的目标 pod 出问题。和
# seatunnel_device_events.py 的 POD_OVERRIDE 是同一个坑,同一个修法,只是
# 这里在这条 DAG 里第一次真正踩到。
EXECUTOR_POD_OVERRIDE = {
    "pod_override": k8s.V1Pod(
        spec=k8s.V1PodSpec(
            containers=[
                k8s.V1Container(
                    name="base",
                    resources=k8s.V1ResourceRequirements(
                        requests={"cpu": "100m", "memory": "512Mi"},
                        limits={"memory": "1Gi"},
                    ),
                )
            ]
        )
    )
}

BASE_KWARGS = dict(
    namespace="feast",
    image=FEAST_IMAGE,
    image_pull_policy="Never",
    volumes=[FEATURE_REPO_VOLUME],
    volume_mounts=[FEATURE_REPO_MOUNT],
    env_vars=MINIO_ENV,
    container_resources=CONTAINER_RESOURCES,
    get_logs=True,
    is_delete_operator_pod=True,
    startup_timeout_seconds=300,
    executor_config=EXECUTOR_POD_OVERRIDE,
)

with DAG(
    dag_id="feast_materialize",
    description="feast apply + materialize-incremental,离线 Iceberg 特征刷新到 Redis 在线存储",
    start_date=datetime(2026, 8, 1),
    # 演示/验证阶段手动触发为主,先不定时跑——demo 数据是固定的历史订单,
    # 定时物化不会产生新增量,意义不大。真的接入会持续变化的数据源之后,
    # 再按需要的新鲜度设置 schedule(比如每小时)。
    schedule=None,
    catchup=False,
    tags=["demo", "phase3.5", "feast", "feature-store"],
) as dag:
    # KubernetesPodOperator 这个版本没有 working_dir 参数(实测 TypeError
    # 才发现,inspect.signature 确认这个 provider 版本压根不支持),feast
    # apply/materialize 又必须在包含 feature_store.yaml 的目录下跑,改用
    # sh -c 手动 cd 到挂载点。
    apply = KubernetesPodOperator(
        task_id="feast_apply",
        name="feast-apply",
        cmds=["sh", "-c"],
        arguments=["cd /feature_repo && feast apply"],
        **BASE_KWARGS,
    )

    # materialize-incremental 的截止时间用当前 UTC 时间,把从上次物化以来
    # (或者第一次跑的话,从 FeatureView 的 ttl 往前推)新增的特征值刷进
    # Redis——不是重新物化全部历史,增量语义。
    materialize = KubernetesPodOperator(
        task_id="feast_materialize_incremental",
        name="feast-materialize-incremental",
        cmds=["sh", "-c"],
        arguments=["cd /feature_repo && feast materialize-incremental '{{ ts }}'"],
        **BASE_KWARGS,
    )

    apply >> materialize
