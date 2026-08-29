"""作业提交:把一个本地 Python 脚本变成集群里跑的 Argo Workflow。

对应 ysb/algo 里 `$EXECUTENOTEBOOK` + `pydolphinscheduler` 那一层——
用户不用写 Argo Workflow YAML,也不用懂 Kubernetes。

**为什么脚本走 ConfigMap 而不是 git clone**(这是有意的取舍,不是偷懒):
git 拉代码更贴近 ysb/algo 现在的做法,长期也确实该支持;但开发循环里
"改一行就要 commit+push 一次才能试跑"是很重的摩擦,而 notebook 里迭代
恰恰是改动最频繁的场景。ConfigMap 上传让"本地改完直接提交试跑"成立,
和 apps/argo-workflows-training-image/ 已经在用的挂载方式也是同一个模式。
代价是单个 ConfigMap 有 1MB 上限,只适合单文件脚本——真正的多文件项目
应该走 git,那是 ADR-058 第二批的事,到时候加一个 `source: git` 选项即可,
不会推翻现在这套。
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

from . import config

# ConfigMap 的 data 总大小上限是 1MiB(K8s 硬限制)。留一点余量提前报错,
# 免得攒到 API server 那里才失败、错误信息还不好懂。
_CONFIGMAP_LIMIT_BYTES = 900 * 1024

# K8s 对象名规则(RFC 1123):小写字母数字和 -,不能以 - 开头结尾。
_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

# K8s 标签值的规则:字母数字开头结尾,中间可以有 - _ .,最长 63 个字符。
_LABEL_VALUE_RE = re.compile(r"^[A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?$")


def _k8s_clients():
    """拿到 k8s 客户端,自动适配"在集群里跑"和"在本机跑"两种情况。

    集群里(notebook pod / 任务 pod)用 ServiceAccount 挂载的凭据;
    本机用 ~/.kube/config。调用方不用关心区别。
    """
    from kubernetes import client, config as kube_config

    try:
        kube_config.load_incluster_config()
    except kube_config.ConfigException:
        kube_config.load_kube_config()
    return client.CoreV1Api(), client.CustomObjectsApi()


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(
            f"作业名 {name!r} 不符合 Kubernetes 命名规则:只能用小写字母、数字和短横线,"
            "且不能以短横线开头或结尾。"
        )


def _upload_script(core_api, name: str, script_path: pathlib.Path, namespace: str) -> str:
    """把脚本内容放进 ConfigMap,返回 ConfigMap 名字。

    同名重复提交时覆盖(replace),不是报错——重复试跑同一个作业是开发
    循环里的常态。
    """
    from kubernetes.client.exceptions import ApiException

    source = script_path.read_text(encoding="utf-8")
    size = len(source.encode("utf-8"))
    if size > _CONFIGMAP_LIMIT_BYTES:
        raise ValueError(
            f"脚本 {script_path} 有 {size} 字节,超过了单个 ConfigMap 的可用上限"
            f"({_CONFIGMAP_LIMIT_BYTES} 字节)。多文件/大项目请改用 git 方式"
            "(见 ADR-058 第二批),不要试图拆成多个 ConfigMap 绕过去。"
        )

    cm_name = f"{name}-script"
    body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": cm_name,
            "namespace": namespace,
            # 打上标记,方便以后批量清理这类 SDK 自动创建的对象——不打标签
            # 的话,几个月后没人分得清哪些 ConfigMap 是能删的。
            "labels": {"app.kubernetes.io/managed-by": "platform-sdk"},
        },
        "data": {script_path.name: source},
    }

    try:
        core_api.create_namespaced_config_map(namespace=namespace, body=body)
    except ApiException as exc:
        if exc.status != 409:  # 409 = 已存在
            raise
        core_api.replace_namespaced_config_map(
            name=cm_name, namespace=namespace, body=body
        )
    return cm_name


def _build_workflow(
    name: str,
    cm_name: str,
    script_name: str,
    image: str,
    env: dict[str, str],
    cpu: str,
    memory: str,
    service_account: str,
    queue: str | None,
    submitted_by: str | None,
) -> dict[str, Any]:
    """拼出 Argo Workflow 对象。

    这里刻意用朴素的 dict 拼装,不引 Hera(Argo 的 Python SDK)。理由写在
    ADR-058:单步作业的 manifest 就这么点结构,引一个 SDK 反而多一层
    版本耦合;等真要做多步骤 DAG 编排时,既定选择就是 Hera,那时候它
    才有价值。
    """
    # 平台组件的地址来自 config(它已经处理好"集群内默认值 + 环境变量覆盖"),
    # 保证提交上去的作业和提交者本地看到的是同一套配置。
    base_env = {
        "MLFLOW_TRACKING_URI": config.mlflow_tracking_uri(),
        "MLFLOW_S3_ENDPOINT_URL": config.s3_endpoint_url(),
        "PLATFORM_TRINO_HOST": config.trino_host(),
        "PLATFORM_TRINO_PORT": str(config.trino_port()),
    }
    base_env.update(env)

    # Kueue 队列标签。**必须打在 Pod 上,不是打在 Workflow 上**——Argo 直接
    # 建裸 Pod(不是 batch/Job),Kueue 用的是 pod 集成,它看的是 Pod 自己的
    # 标签。打在 Workflow 的 metadata 上不会往下传,结果就是"标签写了但配额
    # 完全没生效",而且从外面看不出来。
    pod_labels = {"app.kubernetes.io/managed-by": "platform-sdk"}
    if queue:
        pod_labels["kueue.x-k8s.io/queue-name"] = queue

    # 提交人标签打在 **Workflow** 上(不是 Pod 上,和队列标签正相反):
    # 门户查的是 Workflow 列表,按这个标签过滤出"我的作业"(ADR-067)。
    wf_labels = {
        "app.kubernetes.io/managed-by": "platform-sdk",
        "platform-sdk/job": name,
    }
    if submitted_by:
        # 标签值必须符合 K8s 的规则(字母数字加 -_. ,最长 63)。Keycloak
        # 的用户名一般没问题,但邮箱形态的用户名带 @ 就会被 API server
        # 直接拒绝——**那会让整个作业提交失败**,为了一个"锦上添花"的
        # 归属标签把主流程搞挂是不能接受的,所以这里过滤掉不合法的值,
        # 宁可门户上少显示一个作业。
        if _LABEL_VALUE_RE.match(submitted_by):
            wf_labels["platform-sdk/submitted-by"] = submitted_by

    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {
            "generateName": f"{name}-",
            "labels": wf_labels,
        },
        "spec": {
            "entrypoint": "main",
            # 不指定的话会落到没有权限创建 workflowtaskresults 的 default SA,
            # 表现成"训练跑完了但 Workflow 判定 Error"——2026-08-19 真实踩过
            # 这个坑,见 apps/argo-workflows-training-image/manifests/
            # workflow-template.yaml 的注释。
            "serviceAccountName": service_account,
            # 作业跑完不自动删,方便回头查日志。真正的清理策略应该由平台
            # 统一配(Argo 的 workflow GC),不该每个作业自己决定。
            "templates": [
                {
                    "name": "main",
                    "metadata": {"labels": pod_labels},
                    "container": {
                        "image": image,
                        # 本地构建的镜像必须是 IfNotPresent,否则 kubelet 会去
                        # 联网拉一个不存在的远程镜像,直接 ErrImagePull。
                        "imagePullPolicy": "IfNotPresent",
                        "command": ["python3", f"/scripts/{script_name}"],
                        "env": [{"name": k, "value": v} for k, v in base_env.items()],
                        # 凭据不走上面的明文 env,单独从 Secret 引用。
                        "envFrom": [
                            {"secretRef": {"name": "platform-job-credentials", "optional": True}}
                        ],
                        "volumeMounts": [{"name": "script", "mountPath": "/scripts"}],
                        "resources": {
                            "requests": {"cpu": cpu, "memory": memory},
                            "limits": {"memory": memory},
                        },
                    },
                    "volumes": [
                        {"name": "script", "configMap": {"name": cm_name}}
                    ],
                }
            ],
        },
    }


def submit_job(
    name: str,
    script: str | pathlib.Path,
    image: str | None = None,
    env: dict[str, str] | None = None,
    cpu: str = "200m",
    memory: str = "512Mi",
    namespace: str | None = None,
    service_account: str = "argo-workflow",
    queue: str | None = None,
) -> str:
    """把本地脚本提交成一个 Argo Workflow,返回生成的 workflow 名字。

    用法:

        from platform_sdk import submit_job
        wf = submit_job("my-training", "train.py")
        print(wf)

    参数说明只讲不显然的:
    - image: 不传就用统一镜像(config.default_job_image())。"环境一致"要成立,
      默认值必须指向统一镜像。
    - service_account: 默认 argo-workflow,它有创建 workflowtaskresults 的
      权限(见 apps/argo-workflows-training-image/manifests/
      workflow-serviceaccount.yaml)。
    - queue: Kueue 队列(计算配额)。**默认不用传**——平台按提交者所在的组
      自动推断(config.queue_name(),见那里的说明:队列归属由平台决定而
      不是用户输入,否则谁都能填别的组的队列去占人家配额)。这个参数留
      出来只为两种情况:平台自己的运维作业要显式指定队列,以及本机调试。
      推断不出来时作业不打队列标签,行为和引入 Kueue 之前一样。
    """
    _validate_name(name)
    script_path = pathlib.Path(script).expanduser().resolve()
    if not script_path.is_file():
        raise FileNotFoundError(f"找不到脚本文件: {script_path}")

    namespace = namespace or config.argo_namespace()
    image = image or config.default_job_image()
    queue = queue or config.queue_name()
    submitted_by = config.submitter()

    core_api, custom_api = _k8s_clients()
    cm_name = _upload_script(core_api, name, script_path, namespace)
    workflow = _build_workflow(
        name=name,
        cm_name=cm_name,
        script_name=script_path.name,
        image=image,
        env=env or {},
        cpu=cpu,
        memory=memory,
        service_account=service_account,
        queue=queue,
        submitted_by=submitted_by,
    )

    created = custom_api.create_namespaced_custom_object(
        group="argoproj.io",
        version="v1alpha1",
        namespace=namespace,
        plural="workflows",
        body=workflow,
    )
    return created["metadata"]["name"]


def job_status(workflow_name: str, namespace: str | None = None) -> str:
    """查一个 workflow 现在什么状态(Pending/Running/Succeeded/Failed/Error)。

    刚提交、控制器还没来得及写 status 时返回 "Pending",不返回空字符串
    ——调用方不用为"还没有 status 字段"这个中间态单独写判断。
    """
    _, custom_api = _k8s_clients()
    obj = custom_api.get_namespaced_custom_object(
        group="argoproj.io",
        version="v1alpha1",
        namespace=namespace or config.argo_namespace(),
        plural="workflows",
        name=workflow_name,
    )
    return obj.get("status", {}).get("phase") or "Pending"


def job_logs(workflow_name: str, namespace: str | None = None, tail: int = 200) -> str:
    """取这个 workflow 各个 pod 的 main 容器日志,拼成一段文本返回。

    Argo 的日志分散在每个步骤各自的 pod 上,单步作业其实就一个 pod;
    这里按 workflow 标签把它们都捞出来,省得用户自己去找 pod 名。
    """
    core_api, _ = _k8s_clients()
    namespace = namespace or config.argo_namespace()
    pods = core_api.list_namespaced_pod(
        namespace=namespace,
        label_selector=f"workflows.argoproj.io/workflow={workflow_name}",
    )
    chunks = []
    for pod in pods.items:
        try:
            log = core_api.read_namespaced_pod_log(
                name=pod.metadata.name,
                namespace=namespace,
                container="main",
                tail_lines=tail,
            )
        except Exception as exc:  # pod 可能还没起来/已经被清理
            log = f"(取不到日志: {exc})"
        chunks.append(f"===== {pod.metadata.name} =====\n{log}")
    return "\n".join(chunks) if chunks else "(还没有 pod,作业可能刚提交)"


def run_workflow_template(
    template_name: str,
    parameters: dict[str, str] | None = None,
    namespace: str | None = None,
) -> str:
    """触发一个已经部署好的 Argo WorkflowTemplate,返回生成的 workflow 名字。

    和 `submit_job()` 是两回事,不要混淆:`submit_job()` 提交的是调用方
    自己写的临时脚本(走 ConfigMap 上传那条路径);这个函数触发的是平台
    已经声明式部署好的 WorkflowTemplate(比如 `train-demo-model`,见
    `apps/argo-workflows-training-image/manifests/workflow-template.yaml`)
    ——模板本身的镜像/资源/凭据都已经在模板里配好,调用方不需要、也不能
    改这些,只能传模板允许的 parameters(模板目前没有声明任何 parameters,
    传了不存在的 key 会被 Argo 忽略,不会报错,但也不会生效——这不是这个
    函数的 bug,是模板本身还没加参数化,见 docs/project/roadmap.md P1.7)。

    这是 BACKLOG P1.7"notebook 里触发训练"这条空白的落地:在
    JupyterHub notebook 里跑

        from platform_sdk import run_workflow_template
        wf = run_workflow_template("train-demo-model")

    就能触发已经在 `argo-training-workflow-template` 这个组件里定义好的
    训练流程,不需要 `kubectl create` 或者知道 Workflow YAML 长什么样。

    用法和 `submit_job()` 保持一致的返回值/后续操作:拿到的名字直接传给
    `job_status()`/`job_logs()` 查状态、看日志。
    """
    namespace = namespace or config.argo_namespace()
    workflow = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {
            "generateName": f"{template_name}-",
            "labels": {
                "app.kubernetes.io/managed-by": "platform-sdk",
                "platform-sdk/workflow-template": template_name,
            },
        },
        "spec": {
            "workflowTemplateRef": {"name": template_name},
        },
    }
    if parameters:
        workflow["spec"]["arguments"] = {
            "parameters": [{"name": k, "value": v} for k, v in parameters.items()]
        }

    _, custom_api = _k8s_clients()
    created = custom_api.create_namespaced_custom_object(
        group="argoproj.io",
        version="v1alpha1",
        namespace=namespace,
        plural="workflows",
        body=workflow,
    )
    return created["metadata"]["name"]
