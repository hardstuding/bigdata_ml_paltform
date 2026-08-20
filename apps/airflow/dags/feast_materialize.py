# 定时把 Feast 的离线特征(Spark 读 Iceberg)物化进 Redis 在线存储,见
# docs/decisions/042-feast-feature-store.md。
#
# 用 KubernetesPodOperator 而不是 @task(跑在 Airflow worker 自带的镜像
# 里)——feast+pyspark+JRE 这套依赖不轻(pyspark 一个包解压后几百 MB),
# 每次任务运行现装一遍既慢又占带宽。改用
# apps/feast/feature-server-image/Dockerfile 构建的自定义镜像(和 Feast
# Serving 本身用的是同一个镜像,見那份 Dockerfile 顶部注释——两边都要
# pyspark+JRE 是 Feast 自身的架构限制,不是各自单独的选择)。
#
# 2026-08-20(BACKLOG 2.1):这个镜像从"只在本地 docker 里,image_pull_
# policy=Never"改成 GitHub Actions 自动构建推 GHCR,digest 引用 +
# IfNotPresent——不再要求"这台机器之前手动 build 过"这个隐藏前提,换
# 到真正的多节点集群也不用再单独处理镜像分发。
from __future__ import annotations

from datetime import datetime

from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.sdk import DAG
from kubernetes.client import models as k8s

FEAST_IMAGE = "ghcr.io/hardstuding/bigdata_ml_paltform/feast-feature-server@sha256:dd0fbc978b6d30c099dc9d6929a895bdd8f03a38f102cdd1b347c4e2f0b55b2f"

# feature_store.yaml/definitions.py 权威源是 scripts/feast_feature_repo/,
# 部署态的拷贝在 apps/feast/manifests/feature-repo-configmap.yaml——和
# Feast Serving 的 feature_store_yaml_base64 是同一类"chart/Operator 只认
# 静态内容,不能引用仓库里的文件"限制,不是这次偷懒复制的。
# 2026-08-14 实测发现:整个 ConfigMap 目录挂进去,`feast apply` 会报
# `TypeError: the 'package' argument is required to perform a relative
# import for '..2026_08_14_10_28_51.1747361403.definitions'`——和
# Airflow 自己挂 DAG 目录踩过的坑(见上面 DAGS 相关注释)是同一个原因:
# ConfigMap 卷靠 `..data` 软链 + 带时间戳的隐藏目录做原子更新,Feast 解析
# `definitions.py` 时(`importlib.import_module`)顺着软链解析到真实路径,
# 把这个时间戳目录名当成了 Python 包路径的一部分,relative import 直接
# 炸掉。用 subPath 分别挂每个文件,绕开这层目录结构,和 dags-configmap 的
# 挂法一致。代价同样是 ConfigMap 改了以后这两个文件不会自动热更新,需要
# 重启相关 pod(这里是每次任务起新 pod,天然没有这个问题)。
FEATURE_REPO_VOLUME = k8s.V1Volume(
    name="feature-repo",
    config_map=k8s.V1ConfigMapVolumeSource(name="feast-feature-repo"),
)
FEATURE_REPO_MOUNT = [
    k8s.V1VolumeMount(
        name="feature-repo",
        mount_path="/feature_repo/feature_store.yaml",
        sub_path="feature_store.yaml",
    ),
    k8s.V1VolumeMount(
        name="feature-repo",
        mount_path="/feature_repo/definitions.py",
        sub_path="definitions.py",
    ),
]

# 2026-08-14 实测发现:光有 MINIO_ACCESS_KEY/MINIO_SECRET_KEY 不够。
# registry(type: file, s3:// path)走的是 Feast 自己的 boto3/pyarrow S3
# 客户端,认标准 AWS_* 环境变量,不是这两个——那两个只在 feature_store.yaml
# 的 offline_store.spark_conf 里 ${env.*} 替换生效,是 Hadoop S3A 客户端的
# 配置面,和 boto3 是两条完全独立的路径。缺了 AWS_* 这组,`feast apply`
# 建 Registry 时直接报 botocore.exceptions.NoCredentialsError。和
# apps/feast/manifests/feature-server.yaml 里 Feast Serving 自己踩过、
# 已经修过的同一个坑,这次是 DAG 这边独立配置的 env,没有共享,也要补一份。
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
    k8s.V1EnvVar(name="AWS_ENDPOINT_URL_S3", value="http://minio.minio.svc.cluster.local:9000"),
    k8s.V1EnvVar(name="AWS_DEFAULT_REGION", value="us-east-1"),
    # 2026-08-14 实测发现:容器用的是官方 quay.io/feastdev/feature-server
    # 镜像原生的 USER 1001(数字 UID,/etc/passwd 里没有对应条目,常见的
    # OpenShift 风格"任意 UID"镜像惯例)。Spark/Hadoop 启动时
    # UserGroupInformation.getCurrentUser() 会走 JVM 的 UnixLoginModule,
    # 靠 OS 层查用户名,查不到就抛
    # `KerberosAuthException: ... NullPointerException: invalid null
    # input: name`,把整个 spark-submit 直接搞崩(Java gateway 进程还没起
    # 来就退出,PySpark 报 JAVA_GATEWAY_EXITED)。
    # 补充(实测):单独设 HADOOP_USER_NAME 不够——UnixLoginModule 在
    # doSubjectLogin 里是先建 Subject(这一步就是崩溃点),HADOOP_USER_NAME
    # 是后一步才生效的"改名"机制,不能跳过前面这步。真正绕开的办法是让容器
    # 以 UID 0(root)跑,见下面 BASE_KWARGS 里的 security_context——root 的
    # /etc/passwd 查找天然不会失败。HADOOP_USER_NAME 留着不影响正确性,一起
    # 保留。
    k8s.V1EnvVar(name="HADOOP_USER_NAME", value="feast"),
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
#
# 2026-08-14 补充:光加够资源还不够。这台机器 CPU request 常态接近满载
# (ADR-041 记录过),这个 pod 之前没设 priorityClassName,默认优先级最低,
# 实测被另一个更高优先级的 pod 抢占杀掉(kubectl get events 里能看到明确
# 的 Preempted 记录,pod 起来还没 260ms 就被杀,不是业务逻辑报错)。补上
# ADR-041 已经定义好的 batch 优先级(见 platform/priority-classes/),
# 这层和下面 CONTAINER_RESOURCES 对应的目标 pod 都要加,不能只加一层。
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
            ]
        )
    )
}

# 2026-08-14 实测发现:get_logs=True 在这台机器上会把任务拖垮,不是
# feast/Spark 本身的问题。本机代理软件拦截了走 kubelet containerLogs 的
# 流量(docs/operations/troubleshooting.md 里"kubectl logs/exec 报
# Internal Privoxy Error"那条已经记过,任何走这条路径的组件都会撞上,不只
# 是本机 kubectl),KubernetesPodOperator 的 get_logs=True 内部走的是同一条
# 路径,反复 ApiException(500)重试(1/2/4/8秒退避)大概两分半后放弃、直接
# 把还在正常跑的 pod 删掉判定失败——实测确认业务逻辑(建 Registry、解析
# Spark 依赖)当时都是成功的,纯粹是读日志流这条路把整个任务拖死。关掉
# get_logs,operator 改成只轮询 pod phase(走 K8s API,不经过 kubelet
# containerLogs,不受影响)判断成功/失败,日志本来就有 Loki/Alloy 兜底
# (见 troubleshooting.md 里同一条记录的"从设计上完全绕开这条路径")。
# 让容器以 root(UID 0)跑,绕开上面 HADOOP_USER_NAME 那条注释说的
# UnixLoginModule 崩溃——root 的 /etc/passwd 查找天然能成功。这是本机
# local-lite 阶段的务实选择,不是长期方案:更干净的修法是重新 build
# apps/feast/feature-server-image 时在 entrypoint 里给 UID 1001 动态补一条
# /etc/passwd(常见的"任意 UID"镜像 nss_wrapper 套路),留作后续课题,记进
# ADR-042。
ROOT_SECURITY_CONTEXT = k8s.V1PodSecurityContext(run_as_user=0)

BASE_KWARGS = dict(
    namespace="feast",
    image=FEAST_IMAGE,
    image_pull_policy="IfNotPresent",
    volumes=[FEATURE_REPO_VOLUME],
    volume_mounts=FEATURE_REPO_MOUNT,
    env_vars=MINIO_ENV,
    container_resources=CONTAINER_RESOURCES,
    priority_class_name="batch",
    security_context=ROOT_SECURITY_CONTEXT,
    get_logs=False,
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
    # 2026-08-14 实测发现:`{{ ts }}` 这个 Airflow 模板变量在这条 DAG 上
    # 渲染报 `UndefinedError: 'ts' is undefined`——`schedule=None`、手动
    # 触发的 DAG Run 没有 data_interval,`ts`/`ds` 这类基于调度时间的宏
    # 都是未定义的,不是 Airflow 3.x 废弃了这个宏(带 schedule 的 DAG 上
    # 这个宏还能正常用)。改成直接在 shell 里取当前 UTC 时间,不依赖
    # Airflow 的模板渲染上下文,手动触发/定时触发都能一致工作。
    materialize = KubernetesPodOperator(
        task_id="feast_materialize_incremental",
        name="feast-materialize-incremental",
        cmds=["sh", "-c"],
        arguments=[
            "cd /feature_repo && "
            "feast materialize-incremental \"$(date -u +%Y-%m-%dT%H:%M:%S)\""
        ],
        **BASE_KWARGS,
    )

    apply >> materialize
