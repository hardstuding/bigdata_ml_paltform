#!/usr/bin/env python3
"""把 packages/ 下的内部包构建成 wheel,发布到 MinIO 上的 PEP 503 静态索引。

跑在集群里的 CronJob 中(apps/internal-packages/manifests/publish-cronjob.yaml),
理由见 docs/decisions/083-internal-package-registry.md —— 简单说:MinIO 没有
公网入口,CI 到不了它。

PEP 503「简单索引」的全部要求就是两层 HTML:
    /simple/                → 列出所有包名的链接
    /simple/<名字>/         → 列出该包所有文件的链接
所以静态对象存储直接就能当索引用,不需要任何服务进程。
"""
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import tomllib

import boto3

REPO = pathlib.Path(os.environ.get("REPO_DIR", "/repo"))
PACKAGES = REPO / "packages"
BUCKET = os.environ.get("PACKAGES_BUCKET", "packages")
ENDPOINT = os.environ["MINIO_ENDPOINT"]
WHEELHOUSE = pathlib.Path("/tmp/wheels")


def normalize(name: str) -> str:
    """PEP 503 的名字规范化:大小写、`-`/`_`/`.` 都归一。

    不做这一步的话 `pip install My_Pkg` 和目录名 `my-pkg` 对不上,
    表现是"包明明发布了但装不到",而且报错只说 404。
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def build() -> dict[str, list[pathlib.Path]]:
    """每个包构建成 wheel。**一个包失败不影响其它包** —— 否则某个人的包写坏了,
    全公司的内部包都发不出去。"""
    WHEELHOUSE.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[pathlib.Path]] = {}
    failed: list[str] = []
    for d in sorted(p for p in PACKAGES.iterdir() if p.is_dir()):
        if not (d / "pyproject.toml").exists():
            continue
        # **必须显式声明 name 和 version。**
        # 不校验的话,一个空的或者写坏的 pyproject.toml **不会构建失败** ——
        # setuptools 会兜底,产出一个名字取自目录、版本是 `0.0.0` 的包,然后
        # 它就被发布出去了。使用者装到一个 0.0.0 的包、还以为是自己写错了。
        # 2026-08-29 写测试时撞到这个行为(本来想构造一个"构建失败"的用例,
        # 结果它构建成功了)。
        try:
            meta = tomllib.loads((d / "pyproject.toml").read_text())
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{d.name}: pyproject.toml 解析不了 —— {exc}")
            continue
        proj = meta.get("project") or {}
        missing = [k for k in ("name", "version") if not proj.get(k)]
        if missing:
            failed.append(
                f"{d.name}: pyproject.toml 里缺 {'/'.join(missing)}。"
                f"不补的话会静默发布一个 0.0.0 的包。")
            continue
        target = WHEELHOUSE / d.name
        target.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(target), str(d)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            failed.append(f"{d.name}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else '构建失败'}")
            continue
        wheels = sorted(target.glob("*.whl"))
        if wheels:
            out[normalize(d.name)] = wheels
    if failed:
        print("!! 这些包构建失败(其它包照常发布):")
        for f in failed:
            print("   -", f)
    return out


def main() -> None:
    if not PACKAGES.exists():
        print(f"{PACKAGES} 不存在,没有内部包要发布。")
        return

    s3 = boto3.client("s3", endpoint_url=ENDPOINT)
    try:
        s3.head_bucket(Bucket=BUCKET)
    except Exception:  # noqa: BLE001
        s3.create_bucket(Bucket=BUCKET)
        print(f"已创建 bucket {BUCKET}")

    packages = build()
    if not packages:
        print("没有可发布的包。")
        return

    for name, wheels in sorted(packages.items()):
        links = []
        for w in wheels:
            data = w.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            key = f"simple/{name}/{w.name}"
            s3.put_object(Bucket=BUCKET, Key=key, Body=data,
                          ContentType="application/octet-stream")
            # PEP 503 建议把哈希放进链接的 fragment,pip 会用它校验完整性。
            links.append(f'    <a href="{w.name}#sha256={digest}">{w.name}</a><br/>')
            print(f"  已发布 {name}/{w.name}")
        page = ("<!DOCTYPE html><html><head><title>Links for %s</title></head><body>\n"
                "<h1>Links for %s</h1>\n%s\n</body></html>\n" % (name, name, "\n".join(links)))
        # **同一份内容写两个键。**
        # pip 按 PEP 503 请求的是 `<索引>/<包名>/`(带尾斜杠),而 **S3 不会
        # 把目录 URL 解析成 index.html** —— 那是静态网站托管的行为,普通
        # S3/MinIO 端点没有。所以必须额外写一个**键名以斜杠结尾**的对象,
        # pip 才拿得到。2026-08-29 实测:只写 index.html 的话 pip 报
        # "Could not find a version that satisfies the requirement",
        # 而它其实读到了索引地址 —— 最容易误判成"索引没生成"。
        #
        # index.html 那份保留,是给人用浏览器翻的。
        for key in (f"simple/{name}/index.html", f"simple/{name}/"):
            s3.put_object(Bucket=BUCKET, Key=key, Body=page.encode(), ContentType="text/html")

    root_links = "\n".join(f'    <a href="{n}/">{n}</a><br/>' for n in sorted(packages))
    root = ("<!DOCTYPE html><html><head><title>Internal packages</title></head><body>\n"
            "<h1>Internal packages</h1>\n%s\n</body></html>\n" % root_links)
    for key in ("simple/index.html", "simple/"):
        s3.put_object(Bucket=BUCKET, Key=key, Body=root.encode(), ContentType="text/html")

    print(f"发布完成:{len(packages)} 个包。索引 {ENDPOINT}/{BUCKET}/simple/")


if __name__ == "__main__":
    main()
