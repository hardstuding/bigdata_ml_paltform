#!/usr/bin/env python3
"""查这个项目用到的每个镜像支不支持某个 CPU 架构。

**为什么需要**:2026-08-23 zhenghe 提出"arm64 在我们这个场景可能优于
x86_64,后续能不能切回去"。这个问题不能拍脑袋回答——切架构的成本几乎
全部集中在"有没有哪个镜像只发了 amd64",而那个镜像可能是链路上不起眼的
一环,等部署到一半才炸出来。这个脚本把这件事变成一次可重复的检查。

用法:
  python3 scripts/check-image-arch.py            # 查 arm64
  python3 scripts/check-image-arch.py --arch amd64

镜像清单直接复用 scripts/list-project-images.py,不另维护一份。

判定用 `docker manifest inspect`(只查 registry 的 manifest list,不拉
镜像),所以本机是什么架构不影响结果。
"""
import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def list_images() -> list[str]:
    out = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "list-project-images.py"), "--include-pending"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    return sorted({line.strip() for line in out.stdout.splitlines() if line.strip()})


def arches(image: str) -> tuple[str, list[str] | None, str]:
    """返回 (镜像, 架构列表 or None, 备注)。None 表示查不到,**不等于不支持**
    ——查不到和不支持是两回事,混为一谈会导致"因为网络抖动就判定不能切"。"""
    # 一定要 --verbose:不带的时候,**单架构镜像返回的 manifest 里根本没有
    # 架构字段**(架构在 config blob 里),脚本只能得出"unknown"。第一版就
    # 是这样,把两个镜像报成"不支持 arm64",而实际上只是查不出来——
    # 这正是这个项目最忌讳的那类错误:检查工具自己给出了一个看起来确定
    # 的错误结论。--verbose 会把 Descriptor.platform 一起带出来。
    last = "查询失败"
    for _ in range(3):  # 境内访问这些 registry 抖动很常见,失败重试而不是直接判死
        r = subprocess.run(["docker", "manifest", "inspect", "--verbose", image],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            break
        last = (r.stderr.strip().splitlines() or ["查询失败"])[-1][:120]
    else:
        return image, None, last
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return image, None, "返回的不是 JSON"
    entries = d if isinstance(d, list) else [d]
    found = []
    for e in entries:
        plat = (e.get("Descriptor") or {}).get("platform") or {}
        arch = plat.get("architecture")
        if arch and arch != "unknown":
            found.append(arch)
    if not found:
        return image, None, "manifest 里没有架构信息,需人工确认"
    return image, sorted(set(found)), ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", default="arm64")
    args = ap.parse_args()

    images = list_images()
    print(f"共 {len(images)} 个镜像,查 {args.arch} 支持情况 ...\n", file=sys.stderr)

    ok, missing, unknown = [], [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for img, found, note in pool.map(arches, images):
            if found is None:
                unknown.append((img, note))
            elif args.arch in found:
                ok.append(img)
            else:
                missing.append((img, found, note))

    print(f"✅ 支持 {args.arch}:{len(ok)} 个")
    if missing:
        print(f"\n❌ 不支持 {args.arch}:{len(missing)} 个 —— 这些就是切架构的全部成本")
        for img, found, note in missing:
            print(f"   {img}\n      现有:{found} {note}")
    if unknown:
        print(f"\n⚠️  查不到:{len(unknown)} 个(**查不到不等于不支持**,多半是网络或权限)")
        for img, note in unknown:
            print(f"   {img}\n      {note}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
