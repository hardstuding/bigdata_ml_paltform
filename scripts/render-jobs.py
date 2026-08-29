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
  python3 scripts/render-jobs.py                      # 按默认环境(cloud-full)生成
  python3 scripts/render-jobs.py prod                 # 换一个环境
  python3 scripts/render-jobs.py cloud-full --check   # CI:校验生成物没漂移 + 定义合法

作业定义里可以写的字段见 jobs/README.md。
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
PARAM_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
IMAGE_REQS = REPO / "apps" / "platform-image" / "requirements.txt"
MEMBERSHIPS = REPO / "platform" / "iam" / "memberships.csv"
EMPLOYEES = REPO / "platform" / "iam" / "employees.csv"
ENVIRONMENTS = {"local-lite", "cloud-full", "prod"}
NAMESPACE = "argo-workflows"
GENERATED_HEADER = (
    "# 这个文件是生成的,不要手改。\n"
    "# 源:jobs/ 下各作业的 job.yaml + 脚本;生成器:scripts/render-jobs.py\n"
    "# CI 会校验它和 jobs/ 不漂移。\n"
)


def load_image_packages() -> set[str]:
    """平台镜像里预装了哪些第三方包。

    这份清单是 `requires` 校验的依据。**它必须是机器读得懂的**,所以依赖
    从 Dockerfile 的续行里搬进了 requirements.txt(2026-08-29)—— 校验的
    可信度取决于清单本身可不可靠。
    """
    if not IMAGE_REQS.exists():
        return set()
    names = set()
    for line in IMAGE_REQS.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        names.add(re.split(r"[=<>!~\[]", line, 1)[0].strip().lower())
    # SDK 本身不在 requirements.txt 里(它是从源码装的),但作业当然可以用。
    names.add("platform-sdk")
    names.add("platform_sdk")
    return names


def load_memberships() -> dict[str, set[str]]:
    """username -> 他所在的组。"""
    out: dict[str, set[str]] = {}
    if not MEMBERSHIPS.exists():
        return out
    for line in MEMBERSHIPS.read_text().splitlines()[1:]:
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 2 and parts[0]:
            out.setdefault(parts[0], set()).add(parts[1])
    return out


def load_email_to_username() -> dict[str, str]:
    out = {}
    if not EMPLOYEES.exists():
        return out
    rows = EMPLOYEES.read_text().splitlines()
    if not rows:
        return out
    header = [h.strip() for h in rows[0].split(",")]
    try:
        i_user, i_mail = header.index("username"), header.index("email")
    except ValueError:
        return out
    for line in rows[1:]:
        parts = [x.strip() for x in line.split(",")]
        if len(parts) > max(i_user, i_mail) and parts[i_mail]:
            out[parts[i_mail].lower()] = parts[i_user]
    return out


def last_author_email(job_dir: Path) -> str | None:
    """这个作业目录最后一次是谁改的(git 提交邮箱)。

    **这是"可信身份"能落地的地方**:`owner_group` 是用户自己在 yaml 里填的,
    没有任何东西保证他真的属于那个组 —— 而 owner_group 决定用哪个组的计算
    配额。填一个自己不在的组,等于蹭别人的配额,而且从 Workflow 上完全看不
    出来。git 提交者是这条链上唯一不由 yaml 内容决定的身份信号,所以拿它对账。

    拿不到(浅克隆、新文件还没提交)就返回 None,由调用方决定 —— **默认放行**,
    因为把 CI 卡在"git 历史不完整"上,只会让人去关掉这个检查。
    """
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "log", "-1", "--format=%ae", "--", str(job_dir)],
            capture_output=True, text=True, timeout=15)
        return (out.stdout.strip() or None) if out.returncode == 0 else None
    except Exception:
        return None


def load_groups() -> set[str]:
    raw = yaml.safe_load(GROUPS_FILE.read_text())
    if isinstance(raw, dict) and "groups" in raw:
        raw = raw["groups"]
    return {g["name"] if isinstance(g, dict) else g for g in raw}


def load_jobs(groups: set[str], env_name: str | None = None) -> tuple[list[dict], list[str]]:
    jobs, problems = [], []
    image_packages = load_image_packages()
    memberships = load_memberships()
    email_to_user = load_email_to_username()
    skipped_identity: list[str] = []
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

        # owner_group 和**提交人真实所属的组**对账。
        #
        # owner_group 决定这个作业占哪个组的计算配额,而它是用户自己在 yaml
        # 里填的 —— 填一个自己不在的组等于蹭别人配额,从 Workflow 上完全看
        # 不出来。git 提交者是这条链上唯一不由 yaml 内容决定的身份信号。
        #
        # **拿不到身份时放行**(浅克隆、新文件还没提交、提交邮箱不在
        # employees.csv 里)。把 CI 卡在这些情况上只会让人去关掉这个检查,
        # 而一个被关掉的检查等于没有。
        if og and memberships:
            email = last_author_email(d)
            user = email_to_user.get((email or "").lower())
            if user and user in memberships and og not in memberships[user]:
                problems.append(
                    f"{where}: owner_group 写的是「{og}」,但这个作业最后一次是 "
                    f"{user} 改的,而他在 {sorted(memberships[user])} —— 不在 {og}。"
                    f"owner_group 决定占用哪个组的计算配额,不能填一个自己不在的组。"
                    f"要么改成自己的组,要么让 {og} 的人来提交这次改动。")
            elif email and not user:
                # **说出来,不要静默跳过。**
                #
                # 今天这个仓库里这条检查其实一次都不会触发:真实提交邮箱是
                # 个人邮箱,而 employees.csv 是占位 demo 数据,两边对不上,
                # 于是每次都走"拿不到身份 → 放行"。机制是对的,但在接真实
                # HR 数据之前它是**inert 的**。
                #
                # 不打这行的话,这就变成又一个"看起来有检查、其实永远走
                # else"的东西 —— 这个项目今天已经因为这个模式栽过三次
                # (ADR-078 的 Trino group provider、Superset 的 groups
                # scope、permission-request-app 的 is_approver)。
                skipped_identity.append(f"{d.name}({email})")

        # requires:声明这个作业用到哪些第三方包,和平台镜像预装的清单对账。
        # **不装任何东西** —— 运行时 pip install 是这个项目明确记过的反模式。
        # 这个校验把"半夜跑到 import 那一行才 ModuleNotFoundError"提前到 CI。
        for pkg in (spec.get("requires") or []):
            if str(pkg).lower().replace("_", "-") not in {
                    n.replace("_", "-") for n in image_packages}:
                problems.append(
                    f"{where}: requires 里的「{pkg}」不在平台镜像里"
                    f"(清单:apps/platform-image/requirements.txt)。"
                    f"作业不会在运行时安装依赖 —— 那是这个项目踩过的反模式。"
                    f"需要它就加进那份 requirements.txt 并重建镜像;"
                    f"如果是内部包,先按 ADR-083 发布。")

        # params:作业参数。参数化是"补数"能成立的前提 —— 没有参数,重跑
        # 一个日更作业只会再算一遍今天。
        params = spec.get("params") or {}
        if not isinstance(params, dict):
            problems.append(f"{where}: params 要写成 key: 默认值 的映射")
            params = {}
        for k in params:
            if not PARAM_RE.match(str(k)):
                problems.append(f"{where}: 参数名「{k}」不合法(只能字母数字下划线,不能数字开头)")

        # environments:这个作业在哪些环境里生效。不写 = 所有环境。
        # **晋级就是往这个列表里加一个环境名**,不是复制一份 yaml 到别处。
        envs = spec.get("environments")
        if envs is not None:
            if not isinstance(envs, list) or not envs:
                problems.append(f"{where}: environments 要写成非空列表")
            else:
                unknown = [e for e in envs if e not in ENVIRONMENTS]
                if unknown:
                    problems.append(
                        f"{where}: environments 里有不认识的环境 {unknown},"
                        f"只能是 {sorted(ENVIRONMENTS)}")

        spec["_dir"] = d
        spec["_files"] = sorted(f.name for f in d.iterdir()
                                if f.is_file() and f.suffix == ".py")
        jobs.append(spec)

    if skipped_identity:
        print(f"注意:{len(skipped_identity)} 个作业的 owner_group 没能对账 —— "
              f"提交邮箱不在 platform/iam/employees.csv 里:{', '.join(skipped_identity)}。"
              f"这条检查在接上真实 HR/IdP 数据之前是不生效的。")

    if env_name:
        # 不在当前环境里的作业:定义仍然读进来(校验照做),但不生成资源。
        jobs = [j for j in jobs
                if j.get("environments") is None or env_name in j["environments"]]
    return jobs, problems


def render_configmap(jobs: list[dict]) -> str:
    """把每个作业目录下**所有** .py 文件放进 ConfigMap。

    2026-08-29 之前只放 `script` 那一个文件,于是一个作业只能是一个文件 ——
    稍微长一点的作业就只能把所有东西堆进一个几百行的脚本,或者复制粘贴。
    现在同目录下的 .py 都会被挂进去,`import helpers` 直接可用(容器里
    PYTHONPATH 指向作业自己的目录)。

    **不支持子目录**:ConfigMap 的 key 不能带 `/`,要支持嵌套就得自己编码
    路径再在容器里还原,那是在 ConfigMap 上模拟文件系统。真需要多层结构的
    作业,该走"打成一个包发布"(ADR-083)而不是继续往 ConfigMap 里塞 ——
    这条边界写在这里,免得下一个人顺手把它扩成一个半吊子的打包机制。
    """
    data = {}
    for j in jobs:
        for fname in j["_files"]:
            data[f"{j['name']}--{fname}"] = (j["_dir"] / fname).read_text()
    cm = {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "platform-jobs-scripts", "namespace": NAMESPACE},
        "data": data,
    }
    return GENERATED_HEADER + yaml.safe_dump(cm, allow_unicode=True, sort_keys=True, width=100000)


def _script_items(j: dict) -> list[dict]:
    """ConfigMap 的 key 是打平的(`<作业名>--<文件名>`,因为整个 ConfigMap 是
    共用的),但挂进容器时要还原成原本的文件名,否则 `import helpers` 找不到
    `helpers.py`。用 items 逐个映射。"""
    return [{"key": f"{j['name']}--{f}", "path": f} for f in j["_files"]]


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

    # 参数:声明在 job.yaml 的 `params` 里,以 Argo workflow parameter 的形式
    # 暴露(定时跑用默认值),同时作为 `PARAM_<大写名>` 注进环境变量给脚本读。
    #
    # **参数化是"补数"能成立的前提** —— 没有参数,重跑一个日更作业只会再算
    # 一遍今天。补数的做法是带着不同的参数提交一次:
    #   argo submit --from cronwf/<名字> -p run_date=2026-08-01
    params = j.get("params") or {}
    param_defs = [{"name": k, "value": str(v)} for k, v in sorted(params.items())]
    for k in sorted(params):
        env[f"PARAM_{k.upper()}"] = "{{workflow.parameters." + k + "}}"

    # 作业自己的目录进 PYTHONPATH,同目录下的其它 .py 才 import 得到。
    env.setdefault("PYTHONPATH", "/scripts")
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
                **({"arguments": {"parameters": param_defs}} if param_defs else {}),
                # 不指定会落到没有 workflowtaskresults 权限的 default SA,
                # 表现成"脚本跑完了但 Workflow 判定 Error"。
                "serviceAccountName": "argo-workflow",
                "templates": [{
                    "name": "main",
                    "metadata": {"labels": pod_labels},
                    "container": {
                        "image": j.get("image", "local/platform-runtime:0.1.0"),
                        "imagePullPolicy": "IfNotPresent",
                        "command": ["python3", f"/scripts/{script}"],
                        "env": [{"name": k, "value": v} for k, v in sorted(env.items())],
                        "envFrom": [{"secretRef": {"name": "platform-job-credentials",
                                                   "optional": True}}],
                        "volumeMounts": [{"name": "scripts", "mountPath": "/scripts"}],
                        "resources": {"requests": {"cpu": cpu, "memory": mem},
                                      "limits": {"memory": mem}},
                    },
                    "volumes": [{"name": "scripts",
                                 "configMap": {"name": "platform-jobs-scripts",
                                               "items": _script_items(j)}}],
                }],
            },
        },
    }


DEFAULT_ENV = "cloud-full"


def main() -> None:
    check = "--check" in sys.argv
    # 环境:和 render-environment-config.py 一样按位置参数给。默认 cloud-full
    # —— 那是当前真正在跑的环境,也是 CI 校验的那一档。生成物只有一份,所以
    # 必须明确它代表哪个环境,否则"这个集群上有哪些定时作业"就答不上来。
    positional = [a for a in sys.argv[1:] if not a.startswith("-")]
    env_name = positional[0] if positional else DEFAULT_ENV
    if env_name not in ENVIRONMENTS:
        print(f"不认识的环境「{env_name}」,只能是 {sorted(ENVIRONMENTS)}", file=sys.stderr)
        sys.exit(1)
    groups = load_groups()
    jobs, problems = load_jobs(groups, env_name)
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

    print(f"[{env_name}] {len(jobs)} 个作业在这个环境生效,"
          f"其中 {len(scheduled)} 个有 schedule(会生成 CronWorkflow)。")
    if drift:
        print("\n生成物和 jobs/ 漂移了,跑一次 python3 scripts/render-jobs.py:")
        for d in drift:
            print("  -", d)
        sys.exit(1)
    if not check:
        print(f"已写入 {OUT.relative_to(REPO)}/")


if __name__ == "__main__":
    main()
