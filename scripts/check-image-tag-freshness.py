#!/usr/bin/env python3
"""检查自建应用的镜像 tag 是不是落后于它自己的源码。

**这个检查是 2026-08-30 一天之内撞到三次同一个形态之后加的:**

  改了 apps/<x>/src/ 里的代码 -> 提交 -> CI 构建出新镜像 ->
  **但 manifests/deployment.yaml 里的 tag 没跟着改** -> 集群上跑的还是旧镜像

这个失败模式最难受的地方在于**它长得不像 bug**:

- ArgoCD 是 Synced/Healthy(清单确实和 git 一致,只是 tag 指着旧的)
- Pod 是 Running(旧镜像跑得好好的)
- 单元测试全绿(测的是仓库里的新代码)
- 你去看源码,新功能明明写着

当天撞到的三次:
- inference-log-sink 钉在改 kafka-python 之前的 commit,/readyz 永远 503
- permission-request-app 的 /api/table-governance 接口 404,而代码里有
- platform-portal / table-registration-app 同样落后

判据:某个应用 `src/`(或 requirements.txt / Dockerfile)的最后一次提交,
必须就是 deployment.yaml 里钉的那个 tag。落后就报错。

**有一格宽限**:源码的最后一次改动如果就是 HEAD,说明镜像正在构建,放行。
再往后(下一次提交)就必须跟上 —— 否则这个检查会在每一次源码改动上必然
变红,而"总是红的检查"等于没有检查。

**不检查"这个 tag 在 registry 里存不存在"** —— 那需要凭据、需要网络,
而且 CI 里跑的时候镜像可能还没构建完。这个脚本只回答"仓库自己前后一致
吗",registry 那一层由部署时的 ImagePullBackOff 自己暴露。
"""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 自建应用:源码在这个仓库里、由 .github/workflows/build-images.yml 构建。
# 新增自建应用时要加进来 —— 漏加的后果就是这个检查对它无效。
APPS = [
    "platform-portal",
    "permission-request-app",
    "table-registration-app",
    "inference-log-sink",
]


def last_commit(paths):
    """这些路径上的最后一次提交。路径不存在的会被 git 忽略,不影响结果。"""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--"] + [str(p) for p in paths],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    return result.stdout.strip()


def main():
    head_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
        capture_output=True, text=True).stdout.strip()

    problems = []
    for app in APPS:
        app_dir = REPO_ROOT / "apps" / app
        manifest = app_dir / "manifests" / "deployment.yaml"
        if not manifest.exists():
            problems.append(f"{app}:找不到 {manifest.relative_to(REPO_ROOT)}")
            continue

        m = re.search(rf"{re.escape(app)}:([0-9a-f]{{40}})", manifest.read_text(encoding="utf-8"))
        if not m:
            problems.append(
                f"{app}:{manifest.relative_to(REPO_ROOT)} 里找不到 40 位 commit SHA 的镜像 tag。"
                "\n    (用浮动 tag 的话这个检查失效,而且哪个 commit 构建的就不可追溯了)")
            continue
        pinned = m.group(1)

        # 构建上下文是整个 apps/<app>/,但只有这几处会改变镜像内容 ——
        # manifests/ 自己改了不需要重新构建(否则每次改 tag 都会让检查
        # 立刻失败,变成一个永远追不上的循环)。
        watched = [app_dir / sub for sub in ("src", "requirements.txt", "Dockerfile")]
        latest = last_commit(watched)
        if not latest:
            continue

        # **给一格宽限,否则这个检查会必然误报。**
        #
        # 改镜像内容天然是两次提交:第一次改源码(CI 拿到这个 commit 才开始
        # 构建),第二次把 tag 改成第一次那个 SHA。在第一次提交上,镜像
        # **还不存在**,tag 不可能已经指向它 —— 如果这时候就报错,那么每一次
        # 源码改动都会让 CI 变红,而"总是红的检查"等于没有检查。
        #
        # 所以:源码的最后一次改动就是 HEAD 时放行(正在构建),再往后就必须
        # 跟上。连着推两次源码改动而不补 tag,第二次就会被拦住。
        if latest == head_commit:
            continue

        if pinned != latest:
            problems.append(
                f"{app}:镜像钉在 {pinned[:8]},但源码最后一次提交是 {latest[:8]}\n"
                f"    -> 集群上跑的是旧代码。等 CI 构建完 {latest[:8]},把\n"
                f"       {manifest.relative_to(REPO_ROOT)} 里的 tag 改成 {latest}")

    # ---- 统一运行时镜像:pin 不在 deployment.yaml 里,而在环境配置里 ----
    #
    # 它和上面四个是同一个失败模式,只是 pin 的位置不同:
    # `environments/<env>/config.yaml` 的 `platform_job_image`。
    # local-lite 那档是本地构建的、没有 commit SHA,跳过。
    import yaml as _yaml
    #
    # **只看真正影响镜像行为的路径。** Dockerfile 是 `COPY platform-sdk/`
    # 整个目录,所以严格说改 README 也会让镜像字节变化 —— 但那不改变任何
    # 行为,而一个会因为改文档而变红的检查,只会训练出"看到红就忽略"。
    # 和上面四个应用同一个取舍(那边看的是 src/requirements/Dockerfile,
    # 不是整个应用目录)。
    runtime_sources = [REPO_ROOT / "apps" / "platform-image",
                       REPO_ROOT / "platform-sdk" / "platform_sdk",
                       REPO_ROOT / "platform-sdk" / "pyproject.toml"]
    runtime_latest = last_commit(runtime_sources)
    if runtime_latest and runtime_latest != head_commit:
        for env in ("cloud-full", "prod"):
            cfg_path = REPO_ROOT / "environments" / env / "config.yaml"
            if not cfg_path.exists():
                continue
            cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            img = cfg.get("platform_job_image", "")
            tag = img.rsplit(":", 1)[-1] if ":" in img else ""
            if not re.fullmatch(r"[0-9a-f]{40}", tag):
                # local 构建那种写法(0.1.0)在云端两档不该出现,但这里不是
                # 判断这个的地方 —— check-platform-image-refs.py 管一致性,
                # tests/test_render_jobs.py 管"云端必须是 40 位 SHA"。
                continue
            if tag != runtime_latest:
                problems.append(
                    f"platform-runtime({env}):镜像钉在 {tag[:8]},但 "
                    f"apps/platform-image/ 或 platform-sdk/ 最后一次提交是 "
                    f"{runtime_latest[:8]}\n"
                    f"    -> notebook 和定时作业跑的是旧运行时。等 CI 构建完 "
                    f"{runtime_latest[:8]},\n"
                    f"       把 environments/{env}/config.yaml 的 "
                    f"platform_job_image 换成那个 SHA")

    if problems:
        print("镜像 tag 落后于源码:\n")
        for p in problems:
            print(f"  - {p}")
        print("\n为什么这条值得拦:ArgoCD 会 Synced/Healthy、Pod 会 Running、"
              "单测会全绿 —— 唯独集群上跑的不是你写的那份代码。")
        return 1

    print(f"检查了 {len(APPS)} 个自建应用 + 统一运行时镜像,tag 都和各自源码的最新提交一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
