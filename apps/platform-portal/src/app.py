"""
平台门户——统一入口页面(ADR-047)。见 docs/decisions/047-platform-portal.md。

背景:这套平台的每个组件早就共用同一个 Keycloak realm(`platform`),SSO
本来就是通的——登录过一个组件之后再打开另一个,浏览器带着同一份
Keycloak 会话 cookie,不用重新输密码。真正缺的不是"免重复登录"这个
机制(那早就有了),是"一个地方能看到现在有哪些工具、分别是干什么的、
点哪里能进去"这个入口本身不存在。

这个门户本身也挂在同一个 oauth2-proxy/Keycloak 后面(和其他组件一样),
不是自己另起一套认证——打开门户本身就是对"SSO 是不是真的生效"的一次
现场验证,不是摆设。

单文件 Flask app,和这个项目其他自建薄工具(permission-request-app/
table-registration-app)同一个"不用前端框架"的路线,见 ADR-032。

工具清单没有写死"现在是不是常驻"这个状态——这台机器按需 park/unpark
组件是常态,写死的"当前状态"文字过几天就会跟仓库实际情况脱节(这个项目
已经踩过不止一次这个坑,见 docs/operations/troubleshooting.md)。改成
页面加载时**现场探测**每个工具在集群内部是否能连通,状态自己刷新,不需要
人记得回来更新这份清单。

2026-08-23 改造(ADR-067):zhenghe 的原话是"ui 设计和功能感觉都不是很好"。
拆开看是三个不同的问题,这次一起处理:

1. **它只是个链接目录。** 一个人打开门户真正想知道的是"我昨天那个任务
   跑成功了吗""我们组配额还剩多少",这些数据平台里全都有,只是没有一个
   地方按"人"聚合过——现在的做法是让人自己去五个系统里各翻一遍。这次
   加了"我的作业"和"计算配额"两块真实数据。
2. **13 个探测是串行的**,每个超时 1.5 秒,最坏情况页面要等 19.5 秒。
   这大概率就是"感觉不好"里很实在的一部分。改成线程池并发,最坏 ~2 秒。
3. **HTML/CSS 内联在 Python 字符串里**,改一个样式要动源码文件。移到
   Flask 标准的 templates/ 目录——这不违反 ADR-032 的"不用前端框架",
   Jinja 模板本来就是 Flask 自带的,和引入 React 是两回事。

**取数一律容错**:任何一块数据取不到(集群 API 连不上、RBAC 没配、
本机开发环境根本没有 k8s),对应区块显示一句说明,**不影响页面其它部分**。
门户是"哪里都进不去时最后能打开的那个页面",它自己不能因为某个后端挂了
而打不开。
"""
import concurrent.futures
import os

import requests
from flask import Flask, render_template, request

app = Flask(__name__)

# 队列 → 显示名。和 platform/iam/groups.yaml 里的组同名(ADR-064 决定
# 队列直接按已有的组切,不另发明一套组织结构)。
QUEUE_LABELS = {
    "platform-team": "平台组",
    "data-analysts": "数据分析",
    "algorithm-team": "算法组",
}

# 每一项:分类、显示名、一句话说明、外部访问地址(浏览器打开用)、
# 内部探测地址(server 端探活用,不需要认证,能连上就算"在"——不管
# 探活请求本身有没有被应用要求登录挡下来,能建立 HTTP 连接就说明这个
# 组件确实起着,不是在探测"我有没有权限进去")。
TOOLS = [
    {
        "category": "数据",
        "name": "Trino",
        "description": "交互式 SQL / 联邦查询,连 Iceberg 湖仓",
        # Trino 走 HTTPS(apps/trino-tls/ 手写的 Ingress,不是 http),之前
        # 这里写的 scheme 就是错的,2026-08-16 才发现——不是环境差异,是
        # 单纯写错了。
        "url": "https://trino.local-lite.test",
        "probe": "https://trino.trino.svc.cluster.local:8443/v1/info",
        "probe_verify": False,
    },
    {
        "category": "数据",
        "name": "Airflow",
        "description": "任务调度(dbt/SeaTunnel/Feast 物化等 DAG)",
        "url": "http://airflow.local-lite.test",
        "probe": "http://airflow-api-server.airflow.svc.cluster.local:8080/api/v2/monitor/health",
    },
    {
        "category": "数据",
        "name": "Superset",
        "description": "BI 看板 / 出图",
        "url": "http://superset.local-lite.test",
        "probe": "http://superset.superset.svc.cluster.local:8088/health",
    },
    {
        "category": "数据",
        "name": "OpenMetadata",
        "description": "数据目录 / 血缘 / 表安全等级标注",
        "url": "http://openmetadata.local-lite.test",
        "probe": "http://openmetadata.openmetadata.svc.cluster.local:8585/api/v1/system/health",
    },
    {
        "category": "AI/ML",
        "name": "JupyterHub",
        "description": "多用户 Notebook",
        "url": "http://jupyterhub.local-lite.test",
        "probe": "http://hub.jupyterhub.svc.cluster.local:8081/hub/health",
    },
    {
        "category": "AI/ML",
        "name": "MLflow",
        "description": "实验跟踪 / 模型注册",
        "url": "http://mlflow.local-lite.test",
        "probe": "http://mlflow.mlflow.svc.cluster.local:5000/health",
    },
    {
        "category": "AI/ML",
        "name": "Argo Workflows",
        "description": "训练流水线编排",
        "url": "http://argo-workflows.local-lite.test",
        "probe": "http://argo-workflows-server.argo-workflows.svc.cluster.local:2746/",
    },
    {
        "category": "AI/ML",
        "name": "Spark History Server",
        "description": "Spark 作业历史 / 日志",
        "url": "http://spark-history.local-lite.test",
        "probe": "http://spark-history-server.spark-operator.svc.cluster.local:18080/",
    },
    {
        "category": "治理",
        "name": "权限申请门户",
        "description": "组权限申请 / 表访问分级审批 / 权限交接 / 审计",
        "url": "http://permission-request.local-lite.test",
        "probe": "http://permission-request-app.permission-request-app.svc.cluster.local:8080/healthz",
    },
    {
        "category": "治理",
        "name": "建表注册工具",
        "description": "建表 + 回写负责人/安全等级进 OpenMetadata",
        "url": "http://table-registration.local-lite.test",
        "probe": "http://table-registration-app.table-registration-app.svc.cluster.local:8080/healthz",
    },
    {
        "category": "运维",
        "name": "ArgoCD",
        "description": "GitOps 持续部署,谁在跑什么、状态是否正常",
        "url": "http://argocd.local-lite.test",
        "probe": "http://argocd-server.argocd.svc.cluster.local/",
        "probe_verify": False,
    },
    {
        "category": "运维",
        "name": "Grafana",
        "description": "监控看板 / 指标",
        "url": "http://grafana.local-lite.test",
        "probe": "http://kube-prometheus-stack-grafana.monitoring.svc.cluster.local/api/health",
    },
    {
        "category": "身份",
        "name": "Keycloak",
        "description": "统一身份 / SSO,这里的账号密码所有工具通用",
        "url": "http://keycloak.local-lite.test",
        "probe": "http://keycloak-keycloakx-http.keycloak.svc.cluster.local/auth/realms/platform",
    },
]

# 2026-08-16 cloud-full 真实故障(用户实测:门户上点哪个链接都 404)——
# 上面 13 个 url 全部写死不带端口,local-lite 靠 colima 自动转发 80/443
# 不需要端口,cloud-full 的 ingress-nginx 是 NodePort(http 32460,trino
# 单独用 https 32535),不带端口点开必然 404。之前修 SSO 那几个组件时
# 是直接把端口写死进配置,这次改成读环境变量——按 zhenghe 的要求"预留好
# 配置参数,以后接真实域名+80/443 时改配置就行,不用再动代码":两个
# 都留空就是 local-lite/真实域名+标准端口的形态,配了就是 cloud-full
# 这种 NodePort 形态。
def apply_port_suffix(url: str, http_suffix: str, https_suffix: str) -> str:
    """把配置的端口后缀插进 host 和 path 之间(比如
    "http://trino.local-lite.test" + ":32460" ->
    "http://trino.local-lite.test:32460")。suffix 是空字符串时原样
    返回——local-lite/真实域名+标准端口的形态不需要改任何 url。"""
    if url.startswith("https://") and https_suffix:
        return f"https://{url[len('https://'):]}{https_suffix}"
    if url.startswith("http://") and http_suffix:
        return f"http://{url[len('http://'):]}{http_suffix}"
    return url


_HTTP_PORT_SUFFIX = os.environ.get("PUBLIC_HTTP_PORT_SUFFIX", "")
_HTTPS_PORT_SUFFIX = os.environ.get("PUBLIC_HTTPS_PORT_SUFFIX", "")
for _t in TOOLS:
    _t["url"] = apply_port_suffix(_t["url"], _HTTP_PORT_SUFFIX, _HTTPS_PORT_SUFFIX)


def probe(tool):
    """现场探测一个工具在集群内部能不能连通。能建立连接就算"在"(哪怕
    应用层因为没登录返回 401/403,那也证明进程本身活着),连不上/超时
    才算"不在"——园区网络本身有代理相关的历史坑(见
    docs/operations/troubleshooting.md),这里给了足够短的超时,不会让
    某一个工具卡住拖慢整个门户页面加载。"""
    try:
        requests.get(tool["probe"], timeout=1.5, verify=tool.get("probe_verify", True))
        return True
    except requests.RequestException:
        return False


def probe_all(tools):
    """并发探测所有工具,返回 {工具名: 是否在线}。

    **串行探测是这个页面之前最实在的体验问题**:13 个工具 × 1.5 秒超时,
    只要有几个是 park 状态,页面就要转好几秒;全 park 的极端情况要等
    19.5 秒。并发之后最坏就是一个超时的时间(约 2 秒)。

    线程数给得比工具数多一点,保证一轮跑完;探测是纯 IO 等待,线程多
    并不费 CPU。
    """
    def safe(tool):
        # **必须在这里兜住异常**:线程池的 map 会把任何异常原样抛回主线程,
        # 一个工具探测出意外就是整页 500。probe() 现在只吞 RequestException,
        # 将来谁在里面加一行别的逻辑就可能抛别的——门户不该因为一个状态点
        # 算不出来就打不开。测试里专门有一条锁这个行为。
        try:
            return probe(tool)
        except Exception:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(4, len(tools))) as pool:
        return dict(zip([t["name"] for t in tools], pool.map(safe, tools)))


# --------------------------------------------------------------- 集群取数
#
# 下面这几个函数都遵守同一条规则:**取不到就返回一个带 error 的结构,
# 绝不抛异常**。门户是"哪里都进不去的时候最后还能打开的那个页面",它
# 自己不能因为 Argo 挂了或者 RBAC 没配就打不开。本机开发环境根本没有
# k8s,也走同一条降级路径,所以本地跑这个 app 不需要任何 mock。


def _k8s():
    from kubernetes import client, config as kube_config

    try:
        kube_config.load_incluster_config()
    except Exception:
        kube_config.load_kube_config()
    return client.CustomObjectsApi()


def queue_usage():
    """读 Kueue 的 ClusterQueue,回答"哪个组能用多少、现在用了多少、
    借了多少、还有多少在排队"(ADR-064)。

    这是把 CDH/YARN 里"打开 RM 队列页面看一眼"那个习惯搬过来——**配额
    如果看不见,用户只会感觉"我的任务有时候快有时候慢"**,根本不会意识到
    是配额在起作用。

    这里显示的是**所有队列**,不是只显示当前用户的。两个原因:一是平台
    内部各组用了多少本来就不该互相保密,能看到别人在忙反而解释了自己
    为什么在排队;二是要只显示"我的",得先知道当前用户属于哪个组,那
    需要 oauth2-proxy 透传 groups 头,改那份配置有真实的登录风险(它的
    注释里记着两次血泪史),留到能在集群上验证时再做。
    """
    try:
        api = _k8s()
        items = api.list_cluster_custom_object(
            "kueue.x-k8s.io", "v1beta1", "clusterqueues")["items"]
    except Exception as exc:
        return {"error": f"读不到队列信息({type(exc).__name__})", "rows": []}

    rows = []
    for cq in items:
        name = cq["metadata"]["name"]
        nominal, used, borrowed = {}, {}, {}
        for fl in cq.get("spec", {}).get("resourceGroups", [{}])[0].get("flavors", []):
            for r in fl.get("resources", []):
                nominal[r["name"]] = r.get("nominalQuota")
        for fl in cq.get("status", {}).get("flavorsUsage", []):
            for r in fl.get("resources", []):
                used[r["name"]] = r.get("total")
                borrowed[r["name"]] = r.get("borrowed")
        rows.append({
            "name": name,
            "label": QUEUE_LABELS.get(name, name),
            "cpu_quota": nominal.get("cpu", "-"),
            "cpu_used": used.get("cpu", "0"),
            "cpu_borrowed": borrowed.get("cpu", "0"),
            "mem_quota": nominal.get("memory", "-"),
            "mem_used": used.get("memory", "0"),
            "pending": cq.get("status", {}).get("pendingWorkloads", 0),
        })
    return {"error": None, "rows": sorted(rows, key=lambda r: r["name"])}


def my_jobs(username, limit=8):
    """当前用户最近提交的作业。

    靠 platform_sdk 提交时打的 `platform-sdk/submitted-by` 标签来认人
    ——**不是靠猜 workflow 名字**。没有这个标签的作业(比如平台自己的
    定时任务)不会出现在这里,这是对的:这一栏回答的是"我的东西怎么样
    了",不是"集群里在跑什么"。
    """
    if not username:
        return {"error": "还没识别出当前登录用户", "rows": []}
    try:
        api = _k8s()
        wfs = api.list_namespaced_custom_object(
            "argoproj.io", "v1alpha1", ARGO_NAMESPACE, "workflows",
            label_selector=f"platform-sdk/submitted-by={username}",
        )["items"]
    except Exception as exc:
        return {"error": f"读不到作业列表({type(exc).__name__})", "rows": []}

    wfs.sort(key=lambda w: w["metadata"].get("creationTimestamp", ""), reverse=True)
    rows = [{
        "name": w["metadata"]["name"],
        "phase": w.get("status", {}).get("phase") or "Pending",
        "started": w["metadata"].get("creationTimestamp", ""),
        "queue": w["metadata"].get("labels", {}).get("platform-sdk/job", ""),
    } for w in wfs[:limit]]
    return {"error": None, "rows": rows}


ARGO_NAMESPACE = os.environ.get("PLATFORM_ARGO_NAMESPACE", "argo-workflows")


@app.route("/")
def index():
    username = request.headers.get("X-Forwarded-User", "")
    up = probe_all(TOOLS)
    categories = {}
    for tool in TOOLS:
        t = dict(tool)
        t["up"] = up.get(tool["name"], False)
        categories.setdefault(tool["category"], []).append(t)
    return render_template(
        "index.html",
        username=username,
        categories=categories,
        queues=queue_usage(),
        jobs=my_jobs(username),
    )


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
