#!/usr/bin/env python3
"""CI 检查:不允许浮动的镜像 tag(latest/stable/main/...)或者压根没有 tag。

**为什么需要这条检查**:ADR-010 早就定了"版本要锁定",但那是一条纪律,没有
机制保证。2026-08-22 真实踩到:OpenMetadata 采集用的
`ingestion-base` 镜像用的是 chart 默认的 `:latest`,而且它**不出现在任何
静态 manifest 的 `image:` 字段里**——是 OpenMetadata 运行时自己建采集 Job
时才引用的,人眼 review 根本看不到。那个镜像有 1.5GB,浮动 tag 意味着某天
上游一推新版,这个平台的采集任务就换了个没人验证过的镜像。

镜像清单来自 `scripts/list-project-images.py`(它已经能扫到 helm
valuesObject 里写死的 `xxxImage:` 这类字段)。

用法:
    python3 scripts/check-image-tags.py     # 有浮动 tag 就非零退出(CI 用)
"""
import subprocess
import sys

# 显式豁免。**每一条都必须写清楚为什么**——豁免清单没有理由就会退化成
# "报错了就往里加一行"。
ALLOWED = {
    # 下面三个都来自 vendor 进仓库的 loki chart(platform/loki-chart/)的
    # 默认 values,对应的是**我们没有启用的子组件**:enterprise-logs 是
    # Grafana 的企业版 GEL,busybox 是 chart 内部的辅助容器。它们不会被
    # 真正部署,改 vendor 进来的上游 chart 默认值反而会让下次升级产生
    # 无谓的 diff。
    "grafana/enterprise-logs:latest": "vendor 的 loki chart 里未启用的企业版子组件",
    "busybox": "vendor 的 loki chart 内部辅助容器,未启用",
    "busybox:latest": "同上",
}

FLOATING = {"latest", "stable", "main", "master", "edge", "dev", "nightly"}


def main() -> int:
    result = subprocess.run(
        [sys.executable, "scripts/list-project-images.py"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("!! list-project-images.py 跑失败了:", result.stderr.strip()[-300:], file=sys.stderr)
        return 1

    bad = []
    for img in result.stdout.split():
        if "@sha256:" in img:
            continue  # digest 固定,最强的那种
        if img in ALLOWED:
            continue
        ref = img.rsplit("/", 1)[-1]
        tag = ref.split(":", 1)[1] if ":" in ref else None
        if tag is None:
            bad.append((img, "没有 tag(等价于 latest)"))
        elif tag in FLOATING:
            bad.append((img, f"浮动 tag `{tag}`"))

    if bad:
        print(f"!! 发现 {len(bad)} 个没锁版本的镜像(见 ADR-010):", file=sys.stderr)
        for img, why in bad:
            print(f"   {img}  —— {why}", file=sys.stderr)
        print("\n   要么锁一个具体版本/digest,要么在 scripts/check-image-tags.py 的"
              "\n   ALLOWED 里加一条**带理由**的豁免。", file=sys.stderr)
        return 1

    total = len(result.stdout.split())
    print(f"共 {total} 个镜像,全部锁定版本(豁免 {len(ALLOWED)} 条,理由见脚本)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
