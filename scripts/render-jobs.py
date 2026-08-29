#!/usr/bin/env python3
"""把 jobs/ 下的作业定义渲染成 Argo CronWorkflow + 脚本 ConfigMap。

**这是 A 线「作业模板 + CI/CD」缺的那一半**:模板解决了"怎么写一个作业",
但要让它**定时跑**,此前只有两条路——手动 `platform-submit`(不是定时),
或者写一个 Airflow DAG(为了定时引入一整套调度器的概念)。单步作业不该
为了加个定时去写 DAG。

现在:`jobs/<name>/job.yaml` 里写一个 `schedule`,push,ArgoCD 同步成
CronWorkflow。GitOps 是唯一操作接口这条原则在作业这一层也成立了。

**为什么生成 manifest 而不是运行时读 job.yaml**:生成物进 git,意味着
"这个集群上现在有哪些定时作业"这个问题能靠读仓库回答,不用连集群。也意味着
review 一个新作业时,看得到它最终会变成什么。

用法:
  python3 scripts/render-jobs.py           # 生成
  python3 scripts/render-jobs.py --check   # CI:校验生成物没漂移 + 定义合法
"""
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
JOBS = REPO / "jobs"
OUT = REPO / "apps" / "platform-jobs" / "manifests"
GROUPS_FILE = REPO / "platform" / "iam" / "groups.yaml"

NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
CRON_RE = re.compile(r"^(\S+\s+){4}\S+$")
NAMESPACE = "argo-workflows"
GENERATED_HEADER = (
    "# 这个文件是生成的,不要手改。\n"
    "# 源:jobs/ 下各作业的 job.yaml + 脚本;生成器:scripts/render-jobs.py\n"
    "# CI 会校验它和 jobs/ 不漂移。\n"
)


def load_groups() -> set[str]:
    raw = yaml.safe_load(GROUPS_FILE.read_text())
    if isinstance(raw, dict) and "groups" in raw:
        raw = raw["groups"]
    return {g["name"] if isinstance(g, dict) else g for g in raw}


def load_jobs(groups: set[str]) -> tuple[list[dict], list[str]]:
    jobs, problems = [], []
    for d in sorted(p for p in JOBS.iterdir() if p.is_dir()):
        f = d / "job.yaml"
        if not f.exists():
            problems.append(f"jobs/{d.name}/ 里没有 job.yaml")
            continue
        spec = yaml.safe_load(f.read_text()) or {}
        name = spec.get("name")
        script = spec.get("script")
        where = f"jobs/{d.name}/job.yaml"

        if not name or not script:
            problems.append(f"{where}: name 和 script 是必填的")
            continue
        if not NAME_RE.match(name):
            problems.append(
                f"{where}: name「{name}」不能作为 Kubernetes 资源名"
                f"(只能小写字母、数字、连字符,且首尾是字母数字)")
        if not (d / script).exists():
            problems.append(f"{where}: 找不到脚本 {script}")
        sched = spec.get("schedule")
        if sched is not None and not CRON_RE.match(str(sched)):
            problems.append(f"{where}: schedule「{sched}」不是五段式 cron 表达式")
        og = spec.get("owner_group")
        if og and og not in groups:
            problems.append(
                f"{where}: owner_group「{og}」不在 platform/iam/groups.yaml 里。"
                f"写一个不存在的组,作业会一直排队等一个不存在的队列,"
                f"而且从 Workflow 状态上看不出原因。")
        spec["_dir"] = d
        jobs.append(spec)
    return jobs, problems


def render_configmap(jobs: list[dict]) -> str:
    data = {}
    for j in jobs:
        script = j["script"]
        data[f"{j['name']}--{script}"] = (j["_dir"] / script).read_text()
    cm = {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "platform-jobs-scripts", "namespace": NAMESPACE},
        "data": data,
    }
    return GENERATED_HEADER + yaml.safe_dump(cm, allow_unicode=True, sort_keys=True, width=100000)


def render_cronworkflow(j: dict) -> dict:
    name, script = j["name"], j["script"]
    env = {
        # 和 platform_sdk.submit 用的是同一套集群内地址,作业不用自己填连接串。
        "MLFLOW_TRACKING_URI": "http://mlflow.mlflow.svc.cluster.local:5000",
        "MLFLOW_S3_ENDPOINT_URL": "http://minio.minio.svc.cluster.local:9000",
        "PLATFORM_TRINO_HOST": "trino.trino.svc.cluster.local",
        "PLATFORM_TRINO_PORT": "8443",
    }
    env.update({k: str(v) for k, v in (j.get("env") or {}).items()})

    pod_labels = {"app.kubernetes.io/managed-by": "render-jobs"}
    if j.get("owner_group"):
        # Kueue 的队列标签**必须打在 Pod 上**:Argo 建的是裸 Pod,Kueue 走的是
        # pod 集成,看的是 Pod 自己的标签。打在 Workflow 上不会往下传,结果是
        # "标签写了但配额完全没生效",而且从外面看不出来(和 platform_sdk 里
        # 那段注释是同一个坑)。
        pod_labels["kueue.x-k8s.io/queue-name"] = j["owner_group"]

    cpu = str(j.get("cpu", "200m"))
    mem = str(j.get("memory", "512Mi"))
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "CronWorkflow",
        "metadata": {
            "name": name, "namespace": NAMESPACE,
            "labels": {"app.kubernetes.io/managed-by": "render-jobs",
                       "platform-jobs/name": name},
        },
        "spec": {
            # Argo v3.6 起字段是 `schedules`(复数、列表),不是 `schedule`。
            # 我们跑的是 chart 1.0.24 = Argo v4.0.8,只认新写法。
            "schedules": [str(j["schedule"])],
            "timezone": j.get("timezone", "UTC"),
            # Forbid:上一次还没跑完就不再起新的。定时作业绝大多数是幂等
            # 的数据处理,重叠跑通常是灾难而不是加速。
            "concurrencyPolicy": "Forbid",
            # 和调度周期同量级。这台机器大部分时间关机,给足余量让开机后能
            # 补跑一次最近的;太小的话每次开机都恰好错过(黄金链路探针和
            # OpenMetadata 采集都栽过这个,见 docs/operations/troubleshooting.md)。
            "startingDeadlineSeconds": 3600,
            "successfulJobsHistoryLimit": 3,
            "failedJobsHistoryLimit": 3,
            "workflowSpec": {
                "entrypoint": "main",
                # 不指定会落到没有 workflowtaskresults 权限的 default SA,
                # 表现成"脚本跑完了但 Workflow 判定 Error"。
                "serviceAccountName": "argo-workflow",
                "templates": [{
                    "name": "main",
                    "metadata": {"labels": pod_labels},
                    "container": {
                        "image": j.get("image", "local/platform-runtime:0.1.0"),
                        "imagePullPolicy": "IfNotPresent",
                        "command": ["python3", f"/scripts/{name}--{script}"],
                        "env": [{"name": k, "value": v} for k, v in sorted(env.items())],
                        "envFrom": [{"secretRef": {"name": "platform-job-credentials",
                                                   "optional": True}}],
                        "volumeMounts": [{"name": "scripts", "mountPath": "/scripts"}],
                        "resources": {"requests": {"cpu": cpu, "memory": mem},
                                      "limits": {"memory": mem}},
                    },
                    "volumes": [{"name": "scripts",
                                 "configMap": {"name": "platform-jobs-scripts"}}],
                }],
            },
        },
    }


def main() -> None:
    check = "--check" in sys.argv
    groups = load_groups()
    jobs, problems = load_jobs(groups)
    if problems:
        print("作业定义有问题:")
        for p in problems:
            print("  -", p)
        sys.exit(1)

    scheduled = [j for j in jobs if j.get("schedule")]
    files = {"scripts-configmap.yaml": render_configmap(jobs)}
    if scheduled:
        docs = [yaml.safe_dump(render_cronworkflow(j), allow_unicode=True, sort_keys=True)
                for j in scheduled]
        files["cronworkflows.yaml"] = GENERATED_HEADER + "---\n".join(docs)

    OUT.mkdir(parents=True, exist_ok=True)
    drift = []
    for fname, content in files.items():
        path = OUT / fname
        if not path.exists() or path.read_text() != content:
            if check:
                drift.append(str(path.relative_to(REPO)))
            else:
                path.write_text(content)
    # 作业被删掉时,对应的生成物也要清掉,否则集群上留着一个没人认领的 CronWorkflow
    for stale in OUT.glob("*.yaml"):
        if stale.name not in files:
            if check:
                drift.append(f"{stale.relative_to(REPO)}(源已删除,生成物还在)")
            else:
                stale.unlink()

    print(f"{len(jobs)} 个作业,其中 {len(scheduled)} 个有 schedule(会生成 CronWorkflow)。")
    if drift:
        print("\n生成物和 jobs/ 漂移了,跑一次 python3 scripts/render-jobs.py:")
        for d in drift:
            print("  -", d)
        sys.exit(1)
    if not check:
        print(f"已写入 {OUT.relative_to(REPO)}/")


if __name__ == "__main__":
    main()
