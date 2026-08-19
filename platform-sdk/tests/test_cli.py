"""cli.py 的 job.yaml 解析逻辑测试,不需要真实集群(不调用 submit_job)。"""

import pathlib

import pytest
import yaml

from platform_sdk.cli import _load_job_yaml


def _write(tmp_path: pathlib.Path, content: dict) -> pathlib.Path:
    path = tmp_path / "job.yaml"
    path.write_text(yaml.dump(content), encoding="utf-8")
    return path


def test_minimal_job_yaml(tmp_path):
    path = _write(tmp_path, {"name": "hello", "script": "job.py"})
    spec = _load_job_yaml(path)
    assert spec["name"] == "hello"
    # script 路径按 job.yaml 所在目录解析,不是当前工作目录。
    assert spec["script"] == tmp_path / "job.py"


def test_missing_required_field(tmp_path):
    path = _write(tmp_path, {"name": "hello"})  # 缺 script
    with pytest.raises(ValueError, match="script"):
        _load_job_yaml(path)


def test_unknown_field_rejected(tmp_path):
    path = _write(tmp_path, {"name": "hello", "script": "job.py", "typo_field": 1})
    with pytest.raises(ValueError, match="typo_field"):
        _load_job_yaml(path)


def test_not_a_dict(tmp_path):
    path = tmp_path / "job.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="不是一个 YAML 字典"):
        _load_job_yaml(path)
