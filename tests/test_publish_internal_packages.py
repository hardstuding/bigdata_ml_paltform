"""内部包发布逻辑的测试(ADR-083)。

**为什么值得测**:这段代码跑在一个每小时一次的 CronJob 里,产物是一个
**静态索引** —— 结构写错的表现不是报错,是 `pip install` 报 404,而 CronJob
本身是绿的。索引这种"给别人消费的产物",必须在这一侧就验结构。
"""
import importlib.util
import pathlib
import sys
import types

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


def load_module(monkeypatch, repo_dir):
    """导入待测脚本,同时把 boto3 换成一个把对象存在内存里的桩。"""
    stored = {}

    class FakeS3:
        def head_bucket(self, **kw):
            pass

        def create_bucket(self, **kw):
            pass

        def put_object(self, Bucket, Key, Body, **kw):
            stored[Key] = Body

    fake = types.ModuleType("boto3")
    fake.client = lambda *a, **k: FakeS3()
    monkeypatch.setitem(sys.modules, "boto3", fake)
    monkeypatch.setenv("REPO_DIR", str(repo_dir))
    monkeypatch.setenv("MINIO_ENDPOINT", "http://fake:9000")

    spec = importlib.util.spec_from_file_location(
        "pub", REPO / "scripts" / "publish_internal_packages.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, stored


def make_pkg(root, name, version="0.1.0", broken=False):
    d = root / "packages" / name
    (d / name.replace("-", "_")).mkdir(parents=True)
    (d / "pyproject.toml").write_text(
        "" if broken else
        f'[project]\nname = "{name}"\nversion = "{version}"\nrequires-python = ">=3.10"\n')
    (d / name.replace("-", "_") / "__init__.py").write_text("x = 1\n")
    return d


pytest.importorskip("build", reason="没装 build 就跳过(CI 里会装)")


def test_生成_pep503_两层索引(tmp_path, monkeypatch):
    make_pkg(tmp_path, "demo-utils")
    mod, stored = load_module(monkeypatch, tmp_path)
    mod.main()
    assert "simple/index.html" in stored, "缺根索引,pip 找不到任何包"
    assert "simple/demo-utils/index.html" in stored, "缺包索引"
    assert any(k.endswith(".whl") for k in stored), "wheel 本身没上传"


def test_包索引里带_sha256(tmp_path, monkeypatch):
    """PEP 503 建议把哈希放进链接 fragment,pip 会用它校验完整性。
    少了它,传输损坏或被替换都不会被发现。"""
    make_pkg(tmp_path, "demo-utils")
    mod, stored = load_module(monkeypatch, tmp_path)
    mod.main()
    assert "sha256=" in stored["simple/demo-utils/index.html"].decode()


def test_包名按_pep503_规范化(tmp_path, monkeypatch):
    """`My_Pkg` / `my-pkg` / `my.pkg` 在 PEP 503 里是同一个名字。不规范化的话
    表现是"包明明发布了但装不到",而 pip 只报 404。"""
    mod, _ = load_module(monkeypatch, tmp_path)
    assert mod.normalize("My_Pkg") == "my-pkg"
    assert mod.normalize("my.pkg") == "my-pkg"
    assert mod.normalize("my--pkg") == "my-pkg"


def test_一个包坏了不影响其它包(tmp_path, monkeypatch):
    """**这条最重要**:否则某个人的包写坏了,全公司的内部包都发不出去。"""
    make_pkg(tmp_path, "good-pkg")
    make_pkg(tmp_path, "broken-pkg", broken=True)   # 空 pyproject.toml
    mod, stored = load_module(monkeypatch, tmp_path)
    mod.main()
    assert "simple/good-pkg/index.html" in stored, "好包被坏包连累了"
    assert "simple/broken-pkg/index.html" not in stored


def test_没有_packages_目录时不报错(tmp_path, monkeypatch):
    """全新集群上 packages/ 可能还不存在,这个任务不该因此每小时红一次。"""
    mod, stored = load_module(monkeypatch, tmp_path)
    mod.main()
    assert stored == {}


def test_没有_pyproject_的目录被跳过(tmp_path, monkeypatch):
    (tmp_path / "packages" / "just-a-folder").mkdir(parents=True)
    make_pkg(tmp_path, "real-pkg")
    mod, stored = load_module(monkeypatch, tmp_path)
    mod.main()
    assert "simple/real-pkg/index.html" in stored
    assert not any("just-a-folder" in k for k in stored)


def test_空的_pyproject_不会被静默发布成_0_0_0(tmp_path, monkeypatch):
    """**这条来自一次真实的意外发现**:本来想构造"构建失败"的用例,结果空的
    pyproject.toml **构建成功了** —— setuptools 兜底产出一个 0.0.0 的包,
    然后它被发布出去,使用者装到一个 0.0.0 还以为是自己写错了。
    现在缺 name/version 会被显式拒绝。"""
    make_pkg(tmp_path, "no-meta", broken=True)
    mod, stored = load_module(monkeypatch, tmp_path)
    mod.main()
    assert not any("no-meta" in k for k in stored), "缺元数据的包不该被发布"


def test_pyproject_语法错也不会连累别人(tmp_path, monkeypatch):
    make_pkg(tmp_path, "good-pkg")
    bad = tmp_path / "packages" / "syntax-bad"
    bad.mkdir(parents=True)
    (bad / "pyproject.toml").write_text("[project\nname = 没闭合")
    mod, stored = load_module(monkeypatch, tmp_path)
    mod.main()
    assert "simple/good-pkg/index.html" in stored
    assert not any("syntax-bad" in k for k in stored)


def test_同时写了带尾斜杠的键(tmp_path, monkeypatch):
    """**pip 按 PEP 503 请求的是 `<索引>/<包名>/`(带尾斜杠),而 S3 不会把
    目录 URL 解析成 index.html。** 只写 index.html 的话 pip 报
    "Could not find a version that satisfies the requirement" —— 而它其实
    读到了索引地址,最容易误判成"索引根本没生成"。2026-08-29 实测踩到。"""
    make_pkg(tmp_path, "demo-utils")
    mod, stored = load_module(monkeypatch, tmp_path)
    mod.main()
    assert "simple/" in stored, "根索引缺带尾斜杠的键,pip 读不到"
    assert "simple/demo-utils/" in stored, "包索引缺带尾斜杠的键,pip 装不到"
    # 两份内容必须一样,否则浏览器看到的和 pip 看到的不是一回事
    assert stored["simple/demo-utils/"] == stored["simple/demo-utils/index.html"]
