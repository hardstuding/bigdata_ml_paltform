"""scripts/render-jobs.py 的测试。

重点在**校验**那部分,不在 YAML 长什么样:这个脚本的价值是把一批"要等到
作业半夜真的跑起来才会暴露"的问题提前到 CI —— 依赖不在镜像里、owner_group
填了自己不在的组、分区/参数名不合法。渲染结果本身由 `--check` 防漂移。
"""
import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# 测试里用一个**明显不是真实值**的镜像名:真实值来自
# environments/<env>/config.yaml,写死在测试里的话,配置改了测试照样绿,
# 就失去了意义。真实值那条由 TestPlatformJobImage 单独盯。
TEST_IMAGE = "test-registry.invalid/platform-runtime:testtag"

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "render_jobs", REPO / "scripts" / "render-jobs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rj = _load()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """一个假的 jobs/ 目录 + 假的 iam 数据,不碰真实仓库内容。"""
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    reqs = tmp_path / "requirements.txt"
    reqs.write_text("pandas==2.2.3\ntrino==0.338.0\n# 注释\n\nscikit-learn==1.5.2\n")
    members = tmp_path / "memberships.csv"
    members.write_text("username,group\nalice,data-analysts\nbob,algorithm-team\n")
    emps = tmp_path / "employees.csv"
    emps.write_text("employee_id,username,name,email,department,title,manager_id\n"
                    "E1,alice,Alice,alice@example.com,D,T,\n"
                    "E2,bob,Bob,bob@example.com,D,T,\n")
    monkeypatch.setattr(rj, "JOBS", jobs)
    monkeypatch.setattr(rj, "IMAGE_REQS", reqs)
    monkeypatch.setattr(rj, "MEMBERSHIPS", members)
    monkeypatch.setattr(rj, "EMPLOYEES", emps)
    return jobs


def _job(jobs, name, yaml_text, files=("job.py",)):
    d = jobs / name
    d.mkdir()
    (d / "job.yaml").write_text(textwrap.dedent(yaml_text))
    for f in files:
        (d / f).write_text("print('hi')\n")
    return d


GROUPS = {"data-analysts", "algorithm-team", "platform-team"}


class TestRequiresValidation:
    """依赖声明和平台镜像清单对账。作业不会在运行时装任何东西。"""

    def test_镜像里有的包放行(self, workspace):
        _job(workspace, "a", """
            name: a
            script: job.py
            requires: [trino, pandas]
        """)
        _, problems = rj.load_jobs(GROUPS)
        assert problems == []

    def test_镜像里没有的包_ci_直接红(self, workspace):
        # 不挡的话,这个作业会在半夜跑到 import 那一行才 ModuleNotFoundError。
        _job(workspace, "a", """
            name: a
            script: job.py
            requires: [tensorflow]
        """)
        _, problems = rj.load_jobs(GROUPS)
        assert len(problems) == 1
        assert "tensorflow" in problems[0] and "不在平台镜像里" in problems[0]

    def test_下划线和连字符视为同一个包(self, workspace):
        # scikit_learn 和 scikit-learn 是同一个东西,不该因为写法不同就报错。
        _job(workspace, "a", """
            name: a
            script: job.py
            requires: [scikit_learn]
        """)
        _, problems = rj.load_jobs(GROUPS)
        assert problems == []

    def test_platform_sdk_不在_requirements_里但可用(self, workspace):
        # 它是从源码装进镜像的,不在那份第三方清单里。
        _job(workspace, "a", """
            name: a
            script: job.py
            requires: [platform_sdk]
        """)
        _, problems = rj.load_jobs(GROUPS)
        assert problems == []


class TestOwnerGroupIdentityBinding:
    """owner_group 和提交人真实所属的组对账。

    owner_group 决定占用哪个组的计算配额,而它是用户自己在 yaml 里填的 ——
    填一个自己不在的组等于蹭别人配额,从 Workflow 上完全看不出来。
    """

    def test_填自己所在的组_放行(self, workspace, monkeypatch):
        monkeypatch.setattr(rj, "last_author_email", lambda d: "alice@example.com")
        _job(workspace, "a", """
            name: a
            script: job.py
            owner_group: data-analysts
        """)
        _, problems = rj.load_jobs(GROUPS)
        assert problems == []

    def test_填自己不在的组_被挡住(self, workspace, monkeypatch):
        monkeypatch.setattr(rj, "last_author_email", lambda d: "alice@example.com")
        _job(workspace, "a", """
            name: a
            script: job.py
            owner_group: algorithm-team
        """)
        _, problems = rj.load_jobs(GROUPS)
        assert len(problems) == 1
        assert "alice" in problems[0] and "algorithm-team" in problems[0]

    def test_拿不到提交身份时放行(self, workspace, monkeypatch):
        # 浅克隆、新文件还没提交都会走到这里。把 CI 卡在这些情况上,只会让
        # 人去关掉这个检查,而一个被关掉的检查等于没有。
        monkeypatch.setattr(rj, "last_author_email", lambda d: None)
        _job(workspace, "a", """
            name: a
            script: job.py
            owner_group: algorithm-team
        """)
        _, problems = rj.load_jobs(GROUPS)
        assert problems == []

    def test_提交邮箱不在组织架构里也放行_但会说出来(self, capsys, workspace, monkeypatch):
        # **放行但要出声。** 不打这行提示的话,这就变成又一个"看起来有检查、
        # 其实永远走 else"的东西 —— 而这个仓库今天已经因为这个模式栽过三次。
        # 现实是:真实提交邮箱是个人邮箱、employees.csv 是占位数据,所以这条
        # 检查在接上真实 HR 数据之前**一次都不会触发**。
        monkeypatch.setattr(rj, "last_author_email", lambda d: "outsider@example.com")
        _job(workspace, "a", """
            name: a
            script: job.py
            owner_group: algorithm-team
        """)
        _, problems = rj.load_jobs(GROUPS)
        assert problems == []
        assert "没能对账" in capsys.readouterr().out

    def test_不存在的组仍然按老规则挡住(self, workspace, monkeypatch):
        monkeypatch.setattr(rj, "last_author_email", lambda d: None)
        _job(workspace, "a", """
            name: a
            script: job.py
            owner_group: no-such-group
        """)
        _, problems = rj.load_jobs(GROUPS)
        assert any("不在 platform/iam/groups.yaml" in p for p in problems)


class TestMultiFile:
    def test_同目录下所有_py_都进_configmap(self, workspace):
        _job(workspace, "a", """
            name: a
            script: job.py
        """, files=("job.py", "jobkit.py", "helpers.py"))
        jobs, _ = rj.load_jobs(GROUPS)
        assert jobs[0]["_files"] == ["helpers.py", "job.py", "jobkit.py"]
        cm = rj.render_configmap(jobs)
        for f in ("a--job.py", "a--jobkit.py", "a--helpers.py"):
            assert f in cm

    def test_挂进容器时还原成原本的文件名(self, workspace):
        # ConfigMap 的 key 是打平的(整个 ConfigMap 共用),但挂进去必须是
        # 原文件名,否则 `import jobkit` 找不到 jobkit.py。
        _job(workspace, "a", """
            name: a
            script: job.py
            schedule: "0 1 * * *"
        """, files=("job.py", "jobkit.py"))
        jobs, _ = rj.load_jobs(GROUPS)
        items = rj._script_items(jobs[0])
        assert {i["path"] for i in items} == {"job.py", "jobkit.py"}
        assert {i["key"] for i in items} == {"a--job.py", "a--jobkit.py"}

    def test_命令指向还原后的路径_并且_pythonpath_指过去(self, workspace):
        _job(workspace, "a", """
            name: a
            script: job.py
            schedule: "0 1 * * *"
        """, files=("job.py", "jobkit.py"))
        jobs, _ = rj.load_jobs(GROUPS)
        cw = rj.render_cronworkflow(jobs[0], TEST_IMAGE)
        c = cw["spec"]["workflowSpec"]["templates"][0]["container"]
        assert c["command"] == ["python3", "/scripts/job.py"]
        assert {"name": "PYTHONPATH", "value": "/scripts"} in c["env"]

    def test_非_py_文件不进去(self, workspace):
        d = _job(workspace, "a", """
            name: a
            script: job.py
        """)
        (d / "notes.md").write_text("# 说明")
        jobs, _ = rj.load_jobs(GROUPS)
        assert jobs[0]["_files"] == ["job.py"]


class TestParams:
    def test_参数变成_workflow_parameter_和环境变量(self, workspace):
        _job(workspace, "a", """
            name: a
            script: job.py
            schedule: "0 1 * * *"
            params:
              run_date: ""
              region: east
        """)
        jobs, _ = rj.load_jobs(GROUPS)
        cw = rj.render_cronworkflow(jobs[0], TEST_IMAGE)
        names = {p["name"] for p in cw["spec"]["workflowSpec"]["arguments"]["parameters"]}
        assert names == {"run_date", "region"}
        env = {e["name"]: e["value"]
               for e in cw["spec"]["workflowSpec"]["templates"][0]["container"]["env"]}
        assert env["PARAM_RUN_DATE"] == "{{workflow.parameters.run_date}}"
        assert env["PARAM_REGION"] == "{{workflow.parameters.region}}"

    def test_没有参数就不加_arguments(self, workspace):
        _job(workspace, "a", """
            name: a
            script: job.py
            schedule: "0 1 * * *"
        """)
        jobs, _ = rj.load_jobs(GROUPS)
        assert "arguments" not in rj.render_cronworkflow(jobs[0], TEST_IMAGE)["spec"]["workflowSpec"]

    def test_参数名不合法被挡住(self, workspace):
        _job(workspace, "a", """
            name: a
            script: job.py
            params:
              "2bad": x
        """)
        _, problems = rj.load_jobs(GROUPS)
        assert any("参数名" in p for p in problems)


class TestEnvironments:
    def test_不写就是所有环境都生效(self, workspace):
        _job(workspace, "a", "name: a\nscript: job.py\n")
        for env in ("local-lite", "cloud-full", "prod"):
            jobs, _ = rj.load_jobs(GROUPS, env)
            assert len(jobs) == 1

    def test_只在列出的环境里生成(self, workspace):
        _job(workspace, "a", """
            name: a
            script: job.py
            environments: [cloud-full]
        """)
        assert len(rj.load_jobs(GROUPS, "cloud-full")[0]) == 1
        assert len(rj.load_jobs(GROUPS, "prod")[0]) == 0

    def test_不认识的环境被挡住(self, workspace):
        _job(workspace, "a", """
            name: a
            script: job.py
            environments: [staging]
        """)
        _, problems = rj.load_jobs(GROUPS)
        assert any("不认识的环境" in p for p in problems)

    def test_校验对不在当前环境的作业照做(self, workspace):
        # 过滤发生在校验之后 —— 否则"只在 prod 生效"的作业可以永远绕过检查,
        # 等到真的晋级到 prod 那天才一次性爆出来。
        _job(workspace, "a", """
            name: a
            script: job.py
            environments: [prod]
            requires: [tensorflow]
        """)
        jobs, problems = rj.load_jobs(GROUPS, "cloud-full")
        assert jobs == []
        assert any("tensorflow" in p for p in problems)


class TestCredentialsSecret:
    """作业可以有自己的 Trino 身份(2026-08-30 加,起因是 iceberg-maintenance)。

    **这不是给某个作业开后门**:需要更高权限的作业就该有自己的身份,才
    追溯得了、也收窄得了。共用账号 `notebook_service` 是所有 notebook 和
    作业都在用的 —— 给它开敏感 schema 的口子等于"任何能提交作业的人都能
    读审计表"。
    """

    def test_不写就只挂共用凭据(self, workspace):
        _job(workspace, "a", "name: a\nscript: job.py\nschedule: \"0 1 * * *\"\n")
        jobs, _ = rj.load_jobs(GROUPS)
        ef = rj.render_cronworkflow(jobs[0], TEST_IMAGE)["spec"]["workflowSpec"]["templates"][0]["container"]["envFrom"]
        assert [e["secretRef"]["name"] for e in ef] == ["platform-job-credentials"]

    def test_写了就追加在后面(self, workspace):
        """**顺序有意义**:k8s 的 envFrom 后写的覆盖前面的同名变量,所以
        专用凭据必须排在共用凭据后面,否则覆盖不掉。"""
        _job(workspace, "a", """
            name: a
            script: job.py
            schedule: "0 1 * * *"
            credentials_secret: my-own-credentials
        """)
        jobs, _ = rj.load_jobs(GROUPS)
        ef = rj.render_cronworkflow(jobs[0], TEST_IMAGE)["spec"]["workflowSpec"]["templates"][0]["container"]["envFrom"]
        assert [e["secretRef"]["name"] for e in ef] == [
            "platform-job-credentials", "my-own-credentials"]

    def test_专用凭据也是_optional(self, workspace):
        # Secret 不存在时 Pod 照常起来,一调 query() 才报 MissingCredential
        # —— 和共用那份一致,不引入新的失败模式。
        _job(workspace, "a", """
            name: a
            script: job.py
            schedule: "0 1 * * *"
            credentials_secret: my-own-credentials
        """)
        jobs, _ = rj.load_jobs(GROUPS)
        ef = rj.render_cronworkflow(jobs[0], TEST_IMAGE)["spec"]["workflowSpec"]["templates"][0]["container"]["envFrom"]
        assert all(e["secretRef"]["optional"] for e in ef)


class TestPlatformJobImage:
    """统一运行时镜像来自环境配置,不是写死的。

    2026-08-30 之前它硬编码成 `local/platform-runtime:0.1.0` —— 一个只
    存在于当时那台机器上、靠手工 docker build 出来的镜像。换台机器就没了,
    而且没有任何地方记录它是从哪个 commit 构建的。
    """

    def test_三个环境都配了这个键(self):
        for env in sorted(rj.ENVIRONMENTS):
            img = rj.platform_job_image(env)
            assert img, f"{env} 没有 platform_job_image"
            assert ":" in img, f"{env} 的镜像没带 tag:{img}"

    def test_云端两档指向_ACR_且_tag_是_commit_SHA(self):
        import re as _re
        for env in ("cloud-full", "prod"):
            img = rj.platform_job_image(env)
            assert "aliyuncs.com" in img, f"{env} 应该指向 ACR,实际是 {img}"
            tag = img.rsplit(":", 1)[1]
            assert _re.fullmatch(r"[0-9a-f]{40}", tag), (
                f"{env} 的 tag 应该是 40 位 commit SHA,实际是 {tag} —— "
                "浮动 tag 会让'集群上跑的是哪个 commit'不可追溯")

    def test_本地那档仍然用本地构建的镜像(self):
        # local-lite 上没有 ACR 拉取凭据,而且本地开发该能改完立刻用。
        assert rj.platform_job_image("local-lite") == "local/platform-runtime:0.1.0"

    def test_作业自己写了_image_就用它自己的(self):
        jobs, _ = rj.load_jobs(set())
        j = dict(jobs[0]); j["image"] = "my/own:1"
        cw = rj.render_cronworkflow(j, TEST_IMAGE)
        assert cw["spec"]["workflowSpec"]["templates"][0]["container"]["image"] == "my/own:1"
