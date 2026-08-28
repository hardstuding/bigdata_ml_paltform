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


# ------------------------------------------------------- 作业模板(A 线第一步)


def test_列出模板(capsys):
    from platform_sdk.cli import main

    assert main(["--list-templates"]) == 0
    out = capsys.readouterr().out
    # 四个模板都要在,而且每个都带一句说明——只列名字的清单等于没有清单,
    # 人还是得逐个点开看才知道该用哪个。
    for name in ("hello-job", "batch-etl", "train-model", "data-quality-check"):
        assert name in out
    assert "→" in out or "断言" in out


def test_从模板生成新作业(tmp_path):
    from platform_sdk.cli import main

    target = tmp_path / "my-etl"
    assert main(["--new", "batch-etl", "--into", str(target)]) == 0
    assert (target / "job.py").is_file()
    assert (target / "job.yaml").is_file()


def test_不覆盖已存在的目录(tmp_path, capsys):
    # 脚手架把人写了一半的代码盖掉是不可接受的,所以这条单独测。
    from platform_sdk.cli import main

    target = tmp_path / "占用了"
    target.mkdir()
    assert main(["--new", "batch-etl", "--into", str(target)]) == 1
    assert "不覆盖" in capsys.readouterr().err


def test_模板名写错时列出可选的(tmp_path, capsys):
    from platform_sdk.cli import main

    assert main(["--new", "不存在的模板", "--into", str(tmp_path / "x")]) == 1
    err = capsys.readouterr().err
    # 报错要能自救:光说"没这个模板"还得再跑一次 --list-templates。
    assert "batch-etl" in err
