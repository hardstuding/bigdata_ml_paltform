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
