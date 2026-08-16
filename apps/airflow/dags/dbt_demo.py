# 分析师开发平台(ADR-012)最小骨架:dbt build 一遍这个平台的 demo 项目
# (apps/dbt-demo/project/,建在 iceberg.demo.orders 之上),产物
# manifest.json/catalog.json 上传到 MinIO,给以后接 OpenMetadata 的 dbt
# 摄入连接器用。
#
# 范围边界(ADR-053,如实标注,不是全套 ADR-012):
# - 用 KubernetesPodOperator 跑一个 `dbt build` 任务,不是 Cosmos 拆出的
#   逐模型 Airflow 任务——Cosmos 要在 DAG 解析阶段(scheduler/dag-processor
#   进程本身)导入 astronomer-cosmos 这个包,这意味着要改这两个组件的 Python
#   依赖(这个项目的 Airflow 部署现在没有走自定义镜像/gitSync,加包目前
#   只能靠官方 chart 的 `_PIP_ADDITIONAL_REQUIREMENTS`,官方文档自己写明
#   "不建议生产使用,只适合快速试验"),这次没有贸然改动核心组件的运行时
#   依赖,先把"dbt 模型建在 Iceberg 上、产物能被下游摄入"这条链路做扎实,
#   Cosmos 这层"逐模型可见"的体验留作有意的下一步,不是忘了做。
# - 没有配置 OpenMetadata 的 dbt 摄入连接器去真正读 MinIO 里的这两个
#   artifact——那需要验证 OpenMetadata 读 MinIO/S3 兼容存储这条路径(ADR-014
#   记录过这条路径有已知的兼容性问题,不能想当然),这次只做到"产物已经
#   放在约定好的位置",接 OpenMetadata 留作下一步。
from __future__ import annotations

from datetime import datetime

from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.sdk import DAG
from kubernetes.client import models as k8s

DBT_IMAGE = "python:3.12-slim@sha256:876416ecde9aca2bcc90e1fb0c7a9500bbf749f5788b70f82d4c5a5c2357f8b4"

# 和 apps/feast/manifests 的 subPath 挂法不同(dbt 不是 Python
# importlib 相对导入,不会撞上那个坑,见 project-configmap.yaml 的注释)——
# 这里直接把整个 ConfigMap 挂成一个目录,但 ConfigMap key 里的下划线是
# 编码过的路径分隔符,需要用 items 逐个映射回真实的子目录结构。
PROJECT_VOLUME = k8s.V1Volume(
    name="dbt-project",
    config_map=k8s.V1ConfigMapVolumeSource(
        name="dbt-demo-project",
        items=[
            k8s.V1KeyToPath(key="dbt_project.yml", path="dbt_project.yml"),
            k8s.V1KeyToPath(key="profiles.yml", path="profiles.yml"),
            k8s.V1KeyToPath(key="models_staging_sources.yml", path="models/staging/sources.yml"),
            k8s.V1KeyToPath(key="models_staging_stg_orders.sql", path="models/staging/stg_orders.sql"),
            k8s.V1KeyToPath(key="models_marts_daily_order_totals.sql", path="models/marts/daily_order_totals.sql"),
        ],
    ),
)
PROJECT_MOUNT = [
    k8s.V1VolumeMount(name="dbt-project", mount_path="/project"),
]

# ADR-053:dbt_demo_service 是这个平台专属给 dbt 用的 Trino 服务账号(和
# table_registration_service/superset_service 同一套模式,ADR-021 的
# "各组件各自独立账号"原则),密码走 Secret,不写死。
TRINO_ENV = [
    k8s.V1EnvVar(
        name="DBT_TRINO_USER",
        value="dbt_demo_service",
    ),
    k8s.V1EnvVar(
        name="DBT_TRINO_PASSWORD",
        value_from=k8s.V1EnvVarSource(
            secret_key_ref=k8s.V1SecretKeySelector(name="trino-service-account", key="password-dbt_demo_service")
        ),
    ),
]

# 和 feast_materialize.py 同一个教训:KubernetesExecutor 的任务运行时 pod
# (跑 KubernetesPodOperator 自己的调度/轮询逻辑那层,在 airflow 命名空间)
# 默认资源/优先级不够,会被 OOM/抢占杀掉,两层都要补。
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

CONTAINER_RESOURCES = k8s.V1ResourceRequirements(
    requests={"cpu": "200m", "memory": "512Mi"},
    limits={"memory": "1Gi"},
)

BASE_KWARGS = dict(
    namespace="dbt",
    image=DBT_IMAGE,
    volumes=[PROJECT_VOLUME],
    volume_mounts=PROJECT_MOUNT,
    env_vars=TRINO_ENV,
    container_resources=CONTAINER_RESOURCES,
    priority_class_name="batch",
    # 见 docs/operations/troubleshooting.md"kubectl logs/exec 报 Internal
    # Privoxy Error"那条,和 feast_materialize.py 同一个应对方式:关掉
    # get_logs,只轮询 pod phase,不走会被拦截的 kubelet containerLogs 路径。
    get_logs=False,
    is_delete_operator_pod=True,
    startup_timeout_seconds=180,
    executor_config=EXECUTOR_POD_OVERRIDE,
)


with DAG(
    dag_id="dbt_demo",
    description="dbt build 平台 demo 项目(iceberg.demo.orders 之上),验证分析师开发平台主线(ADR-012/ADR-053)",
    start_date=datetime(2026, 8, 1),
    schedule=None,  # 手动触发,验证链路用的 demo,不是常驻定时任务
    catchup=False,
    tags=["demo", "dbt", "analyst-platform"],
) as dag:
    # build 和上传 MinIO 放在同一个任务/同一个 pod 里跑,不拆成两个
    # KubernetesPodOperator——两个独立的 pod 之间没有共享文件系统,
    # `target/manifest.json` 是 dbt build 在第一个 pod 本地生成的,第二个
    # pod 里根本不存在,拆开会导致上传任务必然失败(第一版这么写过,写
    # 完自己核对时发现这个问题,改成一个任务)。用 boto3(S3 协议标准
    # 客户端,不额外装 mc 这类专门工具)做上传,复用 dbt-core 安装同一次
    # pip install。
    dbt_build_and_upload = KubernetesPodOperator(
        task_id="dbt_build_and_upload",
        name="dbt-build-and-upload",
        cmds=["sh", "-c"],
        arguments=[
            # 2026-08-16 云端部署时实测踩到:dbt-core 依赖树比另外几个
            # 自建工具装的包重得多,这台机器网络繁忙时 pip 会真的卡住
            # 不动(不是慢,是 CPU 时间/RSS 都不再增长,`/proc` 里确认过
            # 连接还在但没有数据传输)——和 table-registration-app 那次
            # 是同一类问题(见 apps/table-registration-app/manifests/
            # deployment.yaml 的注释),这里之前漏了同样的超时保护。
            # 版本也补上固定(dbt-core==1.10.23 dbt-trino==1.10.3
            # boto3==1.43.72,任务#13 已经核实锁定过,这份源文件之前没跟
            # ConfigMap 同步更新——这个项目目前没有类似
            # scripts/sync-app-configmaps.py 的机制帮 Airflow DAG 源文件和
            # ConfigMap 保持一致,是已知差距,见 docs/BACKLOG.md)。
            # 2026-08-16 云端部署时实测:pypi.org 直连给 dbt-core 这个大
            # 依赖树反复卡到 300 秒超时(两次,一次挂了 8 分钟以上才手动
            # 中止),换成阿里云的公开 PyPI 镜像后同样的安装 25 秒完成——
            # 不是包太重,是这条网络路径本身的问题。这个镜像是公开、
            # 全球可访问的(不是仅限内网),local-lite 用应该也没坏处,
            # 不用按环境区分。
            # 2026-08-16 云端第一次真正端到端跑通才发现的根因性 bug(不是
            # 网络/版本问题,local-lite 上大概率同样会失败,只是之前没有
            # 真的跑完过一次):/project 是 ConfigMap 挂载,天生只读,dbt
            # 要写 target/(编译产物)和 logs/,在只读目录下会在启动阶段
            # 就静默失败——exit code 2,stdout/stderr 完全没有任何输出
            # (`dbt --version` 能正常打印,但 `dbt build`/`dbt parse` 一个
            # 字都不吐,logs/ 目录连创建都没创建,一开始怀疑是权限/网络/
            # kubectl exec 缓冲区问题,手动起一个调试 pod 逐步排查才定位到
            # 是只读文件系统)。先把 ConfigMap 内容复制到一个可写目录
            # (`cp -rL` 展开 ConfigMap 的软链接结构,不是简单 cp -r 能处理
            # 的),dbt 在这个可写副本里跑。
            "timeout -k 10 300 pip install --quiet -i https://mirrors.aliyun.com/pypi/simple/ dbt-core==1.10.23 dbt-trino==1.10.3 boto3==1.43.72 "
            "&& mkdir -p /workspace && cp -rL /project/* /workspace/ "
            "&& cd /workspace "
            "&& dbt build --profiles-dir . "
            # catalog.json 不是 `dbt build` 的产物——它是 `dbt docs
            # generate` 专门查一遍数据仓库实际的列/表元数据生成的,
            # `dbt build` 只产出 manifest.json(编译期产物,不含真实
            # schema 信息)。2026-08-16 云端第一次真正跑通才发现:上传
            # 这步会报 `FileNotFoundError: target/catalog.json`,一直没人
            # 注意到是因为这个 DAG 之前从没有真的跑到这一步过(前面两个
            # bug——只读挂载、MinIO NetworkPolicy——各自先挡住过一次)。
            "&& dbt docs generate --profiles-dir . "
            "&& python3 -c \""
            "import boto3, os; "
            "s3 = boto3.client('s3', endpoint_url='http://minio.minio.svc.cluster.local:9000', "
            "aws_access_key_id=os.environ['MINIO_ACCESS_KEY'], aws_secret_access_key=os.environ['MINIO_SECRET_KEY']); "
            "s3.upload_file('target/manifest.json', 'lakehouse', 'dbt-artifacts/platform_demo/manifest.json'); "
            "s3.upload_file('target/catalog.json', 'lakehouse', 'dbt-artifacts/platform_demo/catalog.json'); "
            "print('已上传 manifest.json/catalog.json 到 s3://lakehouse/dbt-artifacts/platform_demo/')"
            "\""
        ],
        env_vars=TRINO_ENV
        + [
            k8s.V1EnvVar(
                name="MINIO_ACCESS_KEY",
                value_from=k8s.V1EnvVarSource(secret_key_ref=k8s.V1SecretKeySelector(name="minio-root", key="rootUser")),
            ),
            k8s.V1EnvVar(
                name="MINIO_SECRET_KEY",
                value_from=k8s.V1EnvVarSource(secret_key_ref=k8s.V1SecretKeySelector(name="minio-root", key="rootPassword")),
            ),
        ],
        **{k: v for k, v in BASE_KWARGS.items() if k != "env_vars"},
    )
