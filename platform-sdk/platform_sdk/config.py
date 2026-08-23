"""环境配置解析——整个 SDK 只有这一处读环境变量。

设计原则(ADR-058 的核心,不是随手定的):**同一份代码在本地 IDE、
JupyterHub notebook、调度器任务 pod 里都不用改,差异全部收敛到环境变量。**
这个原则 2026-08-19 已经用 `scripts/train_demo_model.py` 真实验证过——
那个脚本被本机 port-forward 的 shell 脚本和集群内的 Argo WorkflowTemplate
复用同一份文件,零改动。

**为什么这里全是函数、没有模块级常量**:写成 `TRINO_HOST = _get(...)`
这种常量,值会在 `import` 的那一刻固化。notebook 里非常常见的用法是
先 `import platform_sdk`,再 `os.environ["PLATFORM_TRINO_HOST"] = ...`
调成本机 port-forward 的地址——如果是常量,这个设置会**静默失效**,
用户看到的现象是"我明明改了地址,它还是连的老地址",极难排查。
写成函数每次调用都重新读,没有这个陷阱。

地址 vs 凭据,两类变量的处理方式**故意不同**:

- **地址有集群内默认值**。这些值不是秘密(就是 K8s Service 的 DNS 名),
  而且绝大多数使用场景就是在集群里跑。要求用户每次都显式设置,正是
  `docs/usage-guide.md` 记录的"要自己填连接串"那个体验缺口本身,
  这个 SDK 就是来消掉它的。
- **凭据没有默认值,缺了就明确报错**。绝不内置任何账号密码,也不做
  "猜一个默认账号"这种事——报错要能直接告诉人该设哪个变量。

沿用各工具自己的标准环境变量,不另起炉灶:MLflow 认
`MLFLOW_TRACKING_URI`,boto3/MLflow-S3 认 `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` / `MLFLOW_S3_ENDPOINT_URL`。只有 Trino 没有
公认的标准变量名,才用 `PLATFORM_TRINO_*` 这个前缀。这条也是
`train_demo_model.py` 那次定下来的做法。
"""

from __future__ import annotations

import os


class MissingCredential(RuntimeError):
    """凭据类环境变量没设置。单独一个异常类型,方便调用方区分
    "配置没给全"和"网络/服务真的有问题"——这两类问题的排查方向完全不同。
    """


def _get(name: str, default: str | None = None) -> str | None:
    """读环境变量,空字符串等同于没设置。

    空字符串这个处理不是多余的:K8s 的 `env: [{name: X, value: ""}]` 和
    Secret 里缺 key 的降级路径都会产生空串,如果按"已设置"处理,后面会
    拿着空账号去连接,报出来的错(认证失败)和真实原因(变量没配)对不上。
    """
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value


def require(name: str, hint: str = "") -> str:
    """读一个必须存在的变量,没有就抛 MissingCredential。

    报错信息里带上该怎么办——排查这类问题的人往往不熟悉这个平台,
    只说"变量缺失"帮助不大。
    """
    value = _get(name)
    if value is None:
        suffix = f" {hint}" if hint else ""
        raise MissingCredential(f"环境变量 {name} 没有设置,无法继续。{suffix}")
    return value


# ---------------------------------------------------------------- Trino

# 下面这些默认值和 apps/dbt-demo/project/profiles.yml 里已经真实跑通过的
# 配置完全一致(不是照着 chart 文档猜的):Trino 关掉了明文 HTTP
# (`http-server.http.enabled=false`,见 apps/definitions/trino.yaml),
# 只在 8443 上提供 HTTPS。


def trino_host() -> str:
    return _get("PLATFORM_TRINO_HOST", "trino.trino.svc.cluster.local")


def trino_port() -> int:
    return int(_get("PLATFORM_TRINO_PORT", "8443"))


def trino_scheme() -> str:
    return _get("PLATFORM_TRINO_SCHEME", "https")


def trino_catalog() -> str:
    return _get("PLATFORM_TRINO_CATALOG", "iceberg")


def trino_schema() -> str:
    return _get("PLATFORM_TRINO_SCHEMA", "demo")


def trino_verify():
    """把 PLATFORM_TRINO_VERIFY 翻译成 trino 客户端认识的 verify 参数。

    集群内 Trino 用的是 cert-manager 自签证书(见
    apps/definitions/trino-tls.yaml),默认跳过校验——和 dbt-demo、
    platform-portal 探测 ArgoCD 是同一个既有取舍,不是这次新开的口子。

    三种取值:false/0/no → 不校验;true/1/yes → 用系统 CA;其它 → 当成
    CA 证书文件路径。这样同一个变量既能开关也能指定自定义 CA,不用再加
    第二个变量。真上生产换成受信任证书后,设成 true 就收紧了,不用改代码。
    """
    raw = _get("PLATFORM_TRINO_VERIFY", "false")
    normalized = (raw or "").strip().lower()
    if normalized in ("false", "0", "no", ""):
        return False
    if normalized in ("true", "1", "yes"):
        return True
    return raw  # 当作证书路径


# --------------------------------------------------------------- MLflow


def mlflow_tracking_uri() -> str:
    """MLflow 客户端自己就认 MLFLOW_TRACKING_URI(不调 set_tracking_uri 时
    会自动读),这里只是补一个集群内默认值,让 notebook 里开箱即用。
    """
    return _get(
        "MLFLOW_TRACKING_URI", "http://mlflow-mlflow.mlflow.svc.cluster.local:5000"
    )


# --------------------------------------------------------- MinIO / S3


def s3_endpoint_url() -> str:
    """MLflow 的 artifact 上传和直接用 boto3 访问对象存储,认的是同一组
    标准变量,所以这里不另外发明 PLATFORM_MINIO_* 之类的名字。
    """
    return _get("MLFLOW_S3_ENDPOINT_URL", "http://minio.minio.svc.cluster.local:9000")


# ---------------------------------------------------- Argo Workflows


def argo_namespace() -> str:
    return _get("PLATFORM_ARGO_NAMESPACE", "argo-workflows")


def default_job_image() -> str:
    """作业不显式指定镜像时用的默认镜像。

    默认值必须指向这个平台的统一镜像(apps/platform-image/)——"交互开发
    和调度执行环境一致"这条要成立,默认值就不能指向某个通用 python 基础
    镜像,否则用户很容易在不知情的情况下跑在一个缺依赖的环境里。
    """
    return _get("PLATFORM_JOB_IMAGE", "local/platform-runtime:0.1.0")


# ---------------------------------------------------- Kueue 队列

# 有计算配额的队列,按"配额从紧到松"排。一个人同时属于多个组时(比如
# platform-team 的人也在 data-analysts 里),取**排在前面的那个**——
# 顺序是确定的,同一个人每次提交都落到同一个队列,不会今天走这个队列
# 明天走那个。viewers 不在列:只读角色本来就不该提交作业。
_QUEUE_GROUPS = ("algorithm-team", "data-analysts", "platform-team")


def queue_name() -> str | None:
    """当前用户的作业该进哪个 Kueue 队列,推断不出来就返回 None。

    **为什么这个函数必须存在**(ADR-064 里点名了这是整套配额设计最容易
    做成摆设的地方):Kueue 只管那些打了 `kueue.x-k8s.io/queue-name` 标签
    的作业。如果让用户自己在提交时手写队列名,那么(a)大多数人不会写,
    作业绕过配额;(b)少数会写的人可以随便填别的组的队列去占人家的额度。
    队列归属必须由平台按提交者的组自动决定,不能是用户输入。

    组从哪来:JupyterHub 用 Keycloak 的 groups claim 认证(`manage_groups`
    已开),spawn notebook 时把用户的组注入成 `PLATFORM_GROUPS`
    (见 apps/components/jupyterhub.yaml 的 `03-inject-groups`)。

    返回 None 的场景是真实存在的、也是刻意不报错的:在本机 IDE 里直接
    用 SDK 提交、或者 Airflow 这类系统身份跑的任务,没有"提交人的组"这个
    概念。这时候作业不打队列标签,行为和引入 Kueue 之前完全一样——**宁可
    不受配额管,也不能因为推断不出组就让作业提交失败**,那会把一个配额
    功能变成一次全平台故障。
    """
    explicit = os.environ.get("PLATFORM_QUEUE", "").strip()
    if explicit:
        return explicit
    groups = {g.strip() for g in os.environ.get("PLATFORM_GROUPS", "").split(",") if g.strip()}
    for candidate in _QUEUE_GROUPS:
        if candidate in groups:
            return candidate
    return None
