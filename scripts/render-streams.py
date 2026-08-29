#!/usr/bin/env python3
"""把 streams/ 下的流作业定义渲染成 FlinkDeployment + 脚本 ConfigMap。

**为什么需要**:在这之前,加一个 Flink 流作业要手写 ~140 行 FlinkDeployment
——S3A 凭据从哪个 Secret 取、checkpoint 间隔、Prometheus 指标端口、脚本
ConfigMap 怎么挂、PyFlink 那三行 jarURI/entryClass/args 的魔法,全靠从别的
作业抄。抄错一处的表现通常不是报错,是**作业起来了但不写数据**。

批作业那边 2026-08-29 已经有了 `jobs/` + scripts/render-jobs.py;流作业
一直是空白(zhenghe 直接问过"flink、kafka 相关的任务怎么进行开发")。
这个脚本补上对等的能力。

设计上和 render-jobs.py 保持一致(生成物进 git、CI 校验不漂移、定义本身
先校验一遍),不另造一套概念。

用法:
  python3 scripts/render-streams.py           # 生成
  python3 scripts/render-streams.py --check   # CI:校验不漂移 + 定义合法
"""
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
STREAMS = REPO / "streams"
OUT = REPO / "apps" / "platform-streams" / "manifests"
NAMESPACE = "flink"

NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
DURATION_RE = re.compile(r"^\d+(ms|s|min|h)$")
MEM_RE = re.compile(r"^\d+(m|g|mb|gb)$", re.I)
GENERATED_HEADER = (
    "# 这个文件是生成的,不要手改。\n"
    "# 源:streams/ 下各作业的 stream.yaml + 脚本;生成器:scripts/render-streams.py\n"
    "# CI 会校验它和 streams/ 不漂移。\n"
)

# Flink 镜像。和 apps/flink-audit-sink 用的是同一个 —— 里面已经打好
# iceberg-flink-runtime / flink-connector-kafka / hadoop-aws(见
# apps/flink-iceberg-image/Dockerfile),脚本不用自己拉 jar。
FLINK_IMAGE = ("crpi-t6h2mzjka4hzoldo.cn-hangzhou.personal.cr.aliyuncs.com/bigdata-platform/flink-iceberg:"
               "3a01421f1383c9028ed8fb6754f244c7680b49b5")
# PyFlink 的 jar 名字带版本号,Flink 1.16 起模块坐标从 flink-python_2.12
# 改成不带 Scala 后缀的 flink-python(查过 Maven Central 版本列表确认)。
PYFLINK_JAR = "local:///opt/flink/opt/flink-python-1.20.5.jar"


def load(problems: list[str]) -> list[dict]:
    out = []
    if not STREAMS.exists():
        return out
    for d in sorted(p for p in STREAMS.iterdir() if p.is_dir()):
        f = d / "stream.yaml"
        if not f.exists():
            problems.append(f"streams/{d.name}/ 里没有 stream.yaml")
            continue
        spec = yaml.safe_load(f.read_text()) or {}
        where = f"streams/{d.name}/stream.yaml"
        name, script = spec.get("name"), spec.get("script")
        if not name or not script:
            problems.append(f"{where}: name 和 script 是必填的")
            continue
        if not NAME_RE.match(name):
            problems.append(f"{where}: name「{name}」不能作为 Kubernetes 资源名")
        if not (d / script).exists():
            problems.append(f"{where}: 找不到脚本 {script}")
        ci = str(spec.get("checkpoint_interval", "30s"))
        if not DURATION_RE.match(ci):
            problems.append(
                f"{where}: checkpoint_interval「{ci}」不是 Flink 认的时长格式"
                f"(要像 30s / 5min / 1h)。写错的表现是 JobManager 起不来。")
        for k in ("jobmanager_memory", "taskmanager_memory"):
            v = spec.get(k)
            if v is not None and not MEM_RE.match(str(v)):
                problems.append(f"{where}: {k}「{v}」不是内存格式(要像 1024m / 2g)")
        p = spec.get("parallelism", 1)
        if not isinstance(p, int) or p < 1:
            problems.append(f"{where}: parallelism 要是 >=1 的整数")
        spec["_dir"] = d
        out.append(spec)
    return out


def configmap(s: dict) -> dict:
    script = s["script"]
    return {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": f"stream-{s['name']}-script", "namespace": NAMESPACE},
        "data": {script: (s["_dir"] / script).read_text()},
    }


def flinkdeployment(s: dict) -> dict:
    name, script = s["name"], s["script"]
    return {
        "apiVersion": "flink.apache.org/v1beta1",
        "kind": "FlinkDeployment",
        "metadata": {"name": name, "namespace": NAMESPACE,
                     "labels": {"app.kubernetes.io/managed-by": "render-streams",
                                "platform-streams/name": name}},
        "spec": {
            "image": FLINK_IMAGE,
            "imagePullPolicy": "IfNotPresent",
            "flinkVersion": "v1_20",
            "serviceAccount": "flink",
            "flinkConfiguration": {
                "taskmanager.numberOfTaskSlots": "1",
                # Prometheus 指标。不配的话 Grafana 上这个作业就是一片空白,
                # 而"作业还活着但没在处理数据"恰恰只有指标能看出来。
                "metrics.reporter.prom.factory.class":
                    "org.apache.flink.metrics.prometheus.PrometheusReporterFactory",
                "metrics.reporter.prom.port": "9249",
                "execution.checkpointing.interval": str(s.get("checkpoint_interval", "30s")),
                # S3A:凭据走下面 podTemplate 里的环境变量,不写进配置文件。
                "flink.hadoop.fs.s3a.endpoint": "http://minio.minio.svc.cluster.local:9000",
                "flink.hadoop.fs.s3a.path.style.access": "true",
                "flink.hadoop.fs.s3a.connection.ssl.enabled": "false",
                "flink.hadoop.fs.s3a.impl": "org.apache.hadoop.fs.s3a.S3AFileSystem",
                "flink.hadoop.fs.s3a.aws.credentials.provider":
                    "com.amazonaws.auth.EnvironmentVariableCredentialsProvider",
            },
            "podTemplate": {"spec": {
                "containers": [{
                    "name": "flink-main-container",
                    "ports": [{"name": "metrics", "containerPort": 9249}],
                    "env": [
                        {"name": "AWS_ACCESS_KEY_ID",
                         "valueFrom": {"secretKeyRef": {"name": "minio-root", "key": "rootUser"}}},
                        {"name": "AWS_SECRET_ACCESS_KEY",
                         "valueFrom": {"secretKeyRef": {"name": "minio-root", "key": "rootPassword"}}},
                    ],
                    "volumeMounts": [{"name": "job-script", "mountPath": "/opt/flink/usrlib"}],
                }],
                "volumes": [{"name": "job-script",
                             "configMap": {"name": f"stream-{name}-script"}}],
            }},
            "jobManager": {"resource": {
                "memory": str(s.get("jobmanager_memory", "1024m")),
                "cpu": s.get("cpu", 0.3)}},
            "taskManager": {"resource": {
                "memory": str(s.get("taskmanager_memory", "1792m")),
                "cpu": s.get("cpu", 0.3)}},
            "job": {
                "jarURI": PYFLINK_JAR,
                "entryClass": "org.apache.flink.client.python.PythonDriver",
                "args": ["-pyclientexec", "/usr/bin/python3", "-py",
                         f"/opt/flink/usrlib/{script}"],
                "parallelism": s.get("parallelism", 1),
                # stateless:改了脚本重新部署会从头开始,不接着上次的位点。
                # 要有状态升级得改成 savepoint 并配存储 —— 那是另一个决定,
                # 不该由这个生成器替使用者默认做了。
                "upgradeMode": "stateless",
            },
        },
    }


def main() -> None:
    check = "--check" in sys.argv
    problems: list[str] = []
    streams = load(problems)
    if problems:
        print("流作业定义有问题:")
        for p in problems:
            print("  -", p)
        sys.exit(1)

    files = {}
    if streams:
        docs = []
        for s in streams:
            docs.append(yaml.safe_dump(configmap(s), allow_unicode=True, sort_keys=True, width=100000))
            docs.append(yaml.safe_dump(flinkdeployment(s), allow_unicode=True, sort_keys=True))
        files["streams.yaml"] = GENERATED_HEADER + "---\n".join(docs)

    OUT.mkdir(parents=True, exist_ok=True)
    drift = []
    for fname, content in files.items():
        path = OUT / fname
        if not path.exists() or path.read_text() != content:
            drift.append(str(path.relative_to(REPO))) if check else path.write_text(content)
    for stale in OUT.glob("*.yaml"):
        if stale.name not in files:
            drift.append(f"{stale.relative_to(REPO)}(源已删除)") if check else stale.unlink()

    print(f"{len(streams)} 个流作业。")
    if drift:
        print("\n生成物和 streams/ 漂移了,跑一次 python3 scripts/render-streams.py:")
        for d in drift:
            print("  -", d)
        sys.exit(1)
    if not check and files:
        print(f"已写入 {OUT.relative_to(REPO)}/")


if __name__ == "__main__":
    main()
