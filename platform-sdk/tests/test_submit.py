"""run_workflow_template() 的 Workflow 对象拼装逻辑测试,不需要真实集群
——mock 掉 _k8s_clients() 返回的 CustomObjectsApi,只验证提交上去的
payload 形状对不对(workflowTemplateRef/parameters 这些字段名和结构),
和 submit_job()/job_status()/job_logs() 一样,这几个函数本身需要真实
Argo Workflows API 才能端到端验证,不在这份文件的范围内。
"""

from unittest.mock import MagicMock, patch

from platform_sdk import submit


def _mock_clients(created_name="train-demo-model-abc12"):
    custom_api = MagicMock()
    custom_api.create_namespaced_custom_object.return_value = {
        "metadata": {"name": created_name}
    }
    return MagicMock(), custom_api


def test_run_workflow_template_without_parameters():
    core_api, custom_api = _mock_clients()
    with patch.object(submit, "_k8s_clients", return_value=(core_api, custom_api)):
        name = submit.run_workflow_template("train-demo-model", namespace="argo-workflows")

    assert name == "train-demo-model-abc12"
    kwargs = custom_api.create_namespaced_custom_object.call_args.kwargs
    assert kwargs["group"] == "argoproj.io"
    assert kwargs["namespace"] == "argo-workflows"
    assert kwargs["plural"] == "workflows"
    body = kwargs["body"]
    assert body["spec"]["workflowTemplateRef"] == {"name": "train-demo-model"}
    # 没传 parameters 时不该在 spec 里塞一个空的 arguments 字段。
    assert "arguments" not in body["spec"]


def test_run_workflow_template_with_parameters():
    core_api, custom_api = _mock_clients()
    with patch.object(submit, "_k8s_clients", return_value=(core_api, custom_api)):
        submit.run_workflow_template(
            "train-demo-model",
            parameters={"model_name": "demo-rf-classifier"},
            namespace="argo-workflows",
        )

    body = custom_api.create_namespaced_custom_object.call_args.kwargs["body"]
    assert body["spec"]["arguments"] == {
        "parameters": [{"name": "model_name", "value": "demo-rf-classifier"}]
    }


def test_run_workflow_template_defaults_namespace_from_config():
    core_api, custom_api = _mock_clients()
    with patch.object(submit, "_k8s_clients", return_value=(core_api, custom_api)), \
         patch.object(submit.config, "argo_namespace", return_value="argo-workflows"):
        submit.run_workflow_template("train-demo-model")

    kwargs = custom_api.create_namespaced_custom_object.call_args.kwargs
    assert kwargs["namespace"] == "argo-workflows"


def test_run_workflow_template_generate_name_uses_template_name():
    core_api, custom_api = _mock_clients()
    with patch.object(submit, "_k8s_clients", return_value=(core_api, custom_api)):
        submit.run_workflow_template("train-demo-model", namespace="argo-workflows")

    body = custom_api.create_namespaced_custom_object.call_args.kwargs["body"]
    assert body["metadata"]["generateName"] == "train-demo-model-"
    assert body["metadata"]["labels"]["platform-sdk/workflow-template"] == "train-demo-model"


# --------------------------------------------------- Kueue 队列标签
#
# 这几个用例盯的是 ADR-064 点名的那个失效模式:"标签写了但打错位置/没打",
# 从外面完全看不出来,配额静默失效。所以断言的是**标签落在 Pod 上**,
# 不是"函数被调用了"。


def _build(monkeypatch, groups=None, explicit=None):
    import platform_sdk.submit as submit_mod

    monkeypatch.delenv("PLATFORM_QUEUE", raising=False)
    monkeypatch.delenv("PLATFORM_GROUPS", raising=False)
    if groups is not None:
        monkeypatch.setenv("PLATFORM_GROUPS", groups)
    if explicit is not None:
        monkeypatch.setenv("PLATFORM_QUEUE", explicit)

    from platform_sdk import config

    return submit_mod._build_workflow(
        name="j",
        cm_name="j-script",
        script_name="t.py",
        image="img",
        env={},
        cpu="100m",
        memory="128Mi",
        service_account="argo-workflow",
        queue=config.queue_name(),
    )


def _pod_labels(wf):
    return wf["spec"]["templates"][0].get("metadata", {}).get("labels", {})


def test_队列标签打在_pod_上而不是_workflow_上(monkeypatch):
    wf = _build(monkeypatch, groups="algorithm-team")
    assert _pod_labels(wf)["kueue.x-k8s.io/queue-name"] == "algorithm-team"
    # 打在 Workflow 顶层不会往下传给 Pod,Kueue 看不到——所以这里必须没有
    assert "kueue.x-k8s.io/queue-name" not in wf["metadata"]["labels"]


def test_多个组时按固定优先级取_同一个人每次落同一个队列(monkeypatch):
    a = _build(monkeypatch, groups="platform-team,data-analysts")
    b = _build(monkeypatch, groups="data-analysts,platform-team")
    assert _pod_labels(a) == _pod_labels(b) != {}
    assert _pod_labels(a)["kueue.x-k8s.io/queue-name"] == "data-analysts"


def test_推断不出组时不打标签也不报错(monkeypatch):
    # 本机 IDE 直接提交、Airflow 系统身份跑的任务都是这种情况。宁可不受
    # 配额管,也不能让作业提交失败——那是把配额功能变成全平台故障。
    wf = _build(monkeypatch)
    assert "kueue.x-k8s.io/queue-name" not in _pod_labels(wf)


def test_viewers_组拿不到队列(monkeypatch):
    wf = _build(monkeypatch, groups="viewers")
    assert "kueue.x-k8s.io/queue-name" not in _pod_labels(wf)
