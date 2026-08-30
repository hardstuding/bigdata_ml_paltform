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
import pathlib
import re
import urllib.request
import urllib.parse
import json

import requests
from flask import Flask, abort, redirect, render_template, request, url_for

import identity

import sqllab

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
        "name": "SQL 工作台",
        # 分析师写 SQL 的地方。是 Superset 自带的 SQL Lab(ADR-084),
        # 不是另一个组件——所以 host 和 Superset 同一个,只是路径不同。
        # 能不能进由 Keycloak 的组决定:有 sql_lab 角色才看得到这个模块
        # (data-analysts / algorithm-team 有,其他人没有)。
        "description": "写 SQL、跑查询、看历史、导出结果(Superset SQL Lab)",
        "host": "superset",
        "path": "/sqllab/",
        "logo": "superset",
        "probe": "http://superset.superset.svc.cluster.local:8088/health",
    },
    {
        "category": "数据",
        "name": "Trino",
        # 2026-08-29(ADR-084):这里原来写的是「交互式 SQL」,是错的——
        # Trino 的 Web UI **没有 SQL 编辑器**,进去是写不了 SQL 的,它只能
        # 看正在跑的查询和执行计划。分析师照着这句话点进来会一脸懵。
        # 外部评审点出来的就是这个。要写 SQL 去上面那张「SQL 工作台」。
        "description": "查询引擎本身:看查询在跑什么、执行计划、耗时",
        # Trino 走 HTTPS(apps/trino-tls/ 手写的 Ingress,不是 http),之前
        # 这里写的 scheme 就是错的,2026-08-16 才发现——不是环境差异,是
        # 单纯写错了。
        "host": "trino",
        "logo": "trino",
        "scheme": "https",
        "probe": "https://trino.trino.svc.cluster.local:8443/v1/info",
        "probe_verify": False,
    },
    {
        "category": "数据",
        "name": "Airflow",
        "description": "任务调度(dbt/SeaTunnel/Feast 物化等 DAG)",
        "host": "airflow",
        "logo": "airflow",
        "probe": "http://airflow-api-server.airflow.svc.cluster.local:8080/api/v2/monitor/health",
    },
    {
        "category": "数据",
        "name": "Superset",
        "description": "BI 看板 / 出图",
        "host": "superset",
        "logo": "superset",
        "probe": "http://superset.superset.svc.cluster.local:8088/health",
    },
    {
        "category": "数据",
        "name": "OpenMetadata",
        "description": "数据目录 / 血缘 / 表安全等级标注",
        "host": "openmetadata",
        "probe": "http://openmetadata.openmetadata.svc.cluster.local:8585/api/v1/system/health",
    },
    {
        "category": "AI/ML",
        "name": "JupyterHub",
        "description": "多用户 Notebook",
        "host": "jupyterhub",
        "logo": "jupyterhub",
        "probe": "http://hub.jupyterhub.svc.cluster.local:8081/hub/health",
    },
    {
        "category": "AI/ML",
        "name": "MLflow",
        "description": "实验跟踪 / 模型注册",
        "host": "mlflow",
        "logo": "mlflow",
        "probe": "http://mlflow.mlflow.svc.cluster.local:5000/health",
    },
    {
        "category": "AI/ML",
        "name": "Argo Workflows",
        "description": "训练流水线编排",
        "host": "argo-workflows",
        "logo": "argo-workflows",
        "probe": "http://argo-workflows-server.argo-workflows.svc.cluster.local:2746/",
    },
    {
        "category": "AI/ML",
        "name": "Spark History Server",
        "description": "Spark 作业历史 / 日志",
        "host": "spark-history",
        "logo": "spark-history",
        "probe": "http://spark-history-server.spark-operator.svc.cluster.local:18080/",
    },
    {
        "category": "数据",
        "name": "Schema Registry",
        "description": "Kafka 消息的 schema 契约与兼容性校验(Karapace)",
        "host": "schema-registry",
        "logo": "schema-registry",
        "probe": "http://karapace.kafka.svc.cluster.local:8081/subjects",
    },
    {
        "category": "治理",
        "name": "权限申请门户",
        "description": "组权限申请 / 表访问分级审批 / 权限交接 / 审计",
        "host": "permission-request",
        "probe": "http://permission-request-app.permission-request-app.svc.cluster.local:8080/healthz",
    },
    {
        "category": "治理",
        "name": "建表注册工具",
        "description": "建表 + 回写负责人/安全等级进 OpenMetadata",
        "host": "table-registration",
        "probe": "http://table-registration-app.table-registration-app.svc.cluster.local:8080/healthz",
    },
    {
        "category": "运维",
        "name": "ArgoCD",
        "description": "GitOps 持续部署,谁在跑什么、状态是否正常",
        "host": "argocd",
        "logo": "argocd",
        "probe": "http://argocd-server.argocd.svc.cluster.local/",
        "probe_verify": False,
    },
    {
        "category": "运维",
        "name": "Grafana",
        "description": "监控看板 / 指标",
        "host": "grafana",
        "logo": "grafana",
        "probe": "http://kube-prometheus-stack-grafana.monitoring.svc.cluster.local/api/health",
    },
    {
        "category": "身份",
        "name": "Keycloak",
        "description": "统一身份 / SSO,这里的账号密码所有工具通用",
        "host": "keycloak",
        "logo": "keycloak",
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


# 外部访问地址由环境配置拼出来,**不写死域名**。
#
# 2026-08-29 之前 TOOLS 里每一项的 url 都是 `xxx.local-lite.test` 硬编码。
# 后果是 prod 部署之后门户上每一个链接都指向一个不存在的域名 —— 而门户
# 恰恰是新用户进平台看到的第一个页面。域名/协议/端口后缀现在都从
# deployment.yaml 注入(那份是 templates/ 渲染的,三档环境各不相同)。
#
# 每个工具只声明 `host`(子域名前缀)和可选的 `scheme`(不写默认跟随环境的
# external_scheme;Trino 例外,它自己有一份 TLS Ingress,永远是 https)。
_DOMAIN_SUFFIX = os.environ.get("PUBLIC_DOMAIN_SUFFIX", "local-lite.test")
_DEFAULT_SCHEME = os.environ.get("PUBLIC_SCHEME", "http")
_HTTP_PORT_SUFFIX = os.environ.get("PUBLIC_HTTP_PORT_SUFFIX", "")
_HTTPS_PORT_SUFFIX = os.environ.get("PUBLIC_HTTPS_PORT_SUFFIX", "")


# 按角色显示工具(roadmap P1.5 里「底层组件不再对所有角色一视同仁地暴露」)。
#
# **这不是权限控制,是降噪。** 真正拦得住的是每个组件自己的 SSO 和 OPA
# ——一个分析师就算把 ArgoCD 的地址背下来直接访问,他也进不去。这里做的是
# 别把 14 个入口一股脑摆在一个只需要其中三个的人面前:门户是新人进平台看到
# 的第一个页面,那一屏决定他觉得这套东西"能用"还是"太复杂"。
#
# 规则只有一条:**列在这里的分类,只对列出的组显示;没列的分类对所有人显示。**
# 不做成"每个工具一条规则",那样很快就会变成一张没人维护得动的表。
CATEGORY_AUDIENCE = {
    "运维": {"platform-team"},
    "身份": {"platform-team"},
    "治理": {"platform-team", "data-analysts"},
}


def visible_categories(groups):
    """这个人该看到哪些分类。

    **拿不到组信息时显示全部** —— 宁可多显示几个进不去的入口,也不能因为
    一个配置没配对(groups claim 没传过来)就让所有人看到一个空门户。
    这个取舍要写下来,因为它和"按组判断的分支静默走 else"是同一类风险,
    只是这次刻意选了安全的那个方向。
    """
    gs = set(groups or [])
    if not gs:
        return None            # None = 不过滤
    return {c for c, allowed in CATEGORY_AUDIENCE.items() if gs & allowed} | {
        c for c in _ALL_CATEGORIES if c not in CATEGORY_AUDIENCE}


def build_url(tool, domain=None, scheme=None, http_suffix=None, https_suffix=None):
    """按环境配置拼出一个工具的外部访问地址。

    参数都可以显式传,是为了让测试能一次验三档环境,而不用改进程级的
    环境变量(那种测试互相污染,而且没法并行)。
    """
    domain = domain if domain is not None else _DOMAIN_SUFFIX
    sch = tool.get("scheme") or (scheme if scheme is not None else _DEFAULT_SCHEME)
    http_suffix = http_suffix if http_suffix is not None else _HTTP_PORT_SUFFIX
    https_suffix = https_suffix if https_suffix is not None else _HTTPS_PORT_SUFFIX
    base = apply_port_suffix(f"{sch}://{tool['host']}.{domain}",
                             http_suffix, https_suffix)
    # 端口后缀必须插在 host 后面、path 前面,所以 path 只能在这一步拼上去
    # (先拼 path 再插端口会拼出 http://superset.x/sqllab/:32460 这种废话)。
    return base + tool.get("path", "")


_ALL_CATEGORIES = {t["category"] for t in TOOLS}

for _t in TOOLS:
    _t["url"] = build_url(_t)

# /query/... 跳转要拼绝对地址,复用「SQL 工作台」那张卡算出来的 host 部分
# (去掉 /sqllab/ 这个 path)——不另外读一遍环境变量,免得两处配置漂移。
_SQLLAB_BASE = next(
    t["url"][: -len(t["path"])] for t in TOOLS if t["name"] == "SQL 工作台"
)


# ---- 工具图标 ----
# 启动时一次性读进内存。**内联进页面而不是发 HTTP 请求取**:门户可能跑在
# 没有外网的环境里,而且多 11 个请求换 11 个小图标不划算。
#
# 这些 SVG 是 vendored 在镜像里的静态文件(Simple Icons,CC0),不是用户
# 输入,所以模板里用 `|safe` 直接内联是安全的 —— 如果哪天改成从别处动态
# 取,这个前提就不成立了,要重新考虑。
_LOGO_DIR = pathlib.Path(__file__).resolve().parent / "logos"


def _load_logos():
    out = {}
    if not _LOGO_DIR.exists():
        return out
    for f in _LOGO_DIR.glob("*.svg"):
        svg = f.read_text()
        # 去掉固定的宽高(如果有),让 CSS 控制大小;fill 交给 currentColor,
        # 这样深色模式下自动变亮,不用准备两套图。
        svg = re.sub(r'\s(width|height|fill)="[^"]*"', "", svg, count=3)
        svg = svg.replace("<svg ", '<svg class="logo" fill="currentColor" ', 1)
        out[f.stem] = svg
    return out


LOGOS = _load_logos()


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
        return {"error": f"读不到队列信息({type(exc).__name__})", "rows": [], "pending_total": 0}

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
    rows = sorted(rows, key=lambda r: r["name"])
    pending_total = sum(int(r.get("pending") or 0) for r in rows)
    return {"error": None, "rows": rows, "pending_total": pending_total}


# 角色工作台的第一块(roadmap P1.5「门户升级成角色工作台」):首页不再对
# 所有人显示同样的东西 —— 普通用户看到"我的权限 / 快到期",审批人额外看到
# "待我审批 / 超时的"。
#
# 数据来自 permission-request-app 的只读接口,不是门户自己去读 grants.csv:
# "谁能看到什么"这个判断要由拥有数据的那一方做,门户只负责展示。
PERM_APP_INTERNAL = os.environ.get(
    "PERMISSION_APP_INTERNAL_URL",
    "http://permission-request-app.permission-request-app.svc.cluster.local:8080",
)
PERM_APP_TOKEN = os.environ.get("PERMISSION_APP_INTERNAL_TOKEN", "")


def _perm_api(path, username, timeout=3):
    """调 permission-request-app 的只读接口。

    **任何失败都返回 None,由调用方决定怎么降级** —— 首页上少一块内容,
    好过因为一个附属服务不可用就整页打不开。超时给到 3 秒也是这个理由:
    这是首页的同步渲染路径,不能被它拖住。
    """
    if not PERM_APP_TOKEN or not username:
        return None
    try:
        req = urllib.request.Request(
            f"{PERM_APP_INTERNAL}{path}?user={urllib.parse.quote(username)}",
            headers={"X-Internal-Token": PERM_APP_TOKEN},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except Exception:
        return None


def my_permissions(username):
    """我现在有哪些表权限,哪些快到期了。

    **"快到期"这一栏是这块最有价值的部分。** 授权默认 180 天,过期自动
    回收(ADR-050),而 OPA 5 分钟内跟着生效 —— 也就是说人会在毫无预警的
    情况下突然查不到数据,还会以为是平台坏了。摆在首页就是为了这个。
    """
    data = _perm_api("/api/my-permissions", username)
    if data is None:
        return {"available": False, "grants": [], "expiring_soon": [], "warning": None}
    # 上游明说了它读不到 grants 数据源时,**要说出来**,不能显示成"你没有
    # 任何表权限" —— 两者返回的都是空列表,含义完全相反。
    # 2026-08-30 开机验收当场撞到过一次(GIT_TOKEN 没配,接口永远返回空)。
    if data.get("available") is False:
        return {"available": True, "grants": [], "expiring_soon": [],
                "warning": "读不到表权限数据(权限门户那边取不到 grants 记录),"
                           "这里显示的空白不代表你没有权限。"}
    return {"available": True, "grants": data.get("grants", []),
            "expiring_soon": data.get("expiring_soon", []), "warning": None}


def my_approvals(username):
    """等我审批的事项。不是审批人就是空的,这一栏不显示。"""
    data = _perm_api("/api/my-approvals", username)
    if data is None:
        return {"available": False, "pending": [], "overdue": []}
    return {"available": True, "pending": data.get("pending", []),
            "overdue": data.get("overdue", [])}


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

# --------------------------------------------------------------- 作业详情
#
# roadmap P1.5:「点进某个作业能看日志、参数、镜像、资源、失败原因,并能
# 取消和重跑」。
#
# **安全模型(这一段是这个功能能不能做的前提,不是补充说明)**:
#
# 门户是所有登录用户都能打开的页面,所以它 ServiceAccount 的权限,就是
# "任何一个能登录的人间接能拿到的权限"上限。原来这份 RBAC 只有 get/list,
# 注释里写着"这个页面上没有一个按钮会改集群状态"。加取消/重跑就打破了
# 那句话,所以要用两层把范围收回来:
#
# 1. **RBAC 层给最窄的动词**:取消用 `patch`(设 spec.shutdown=Terminate),
#    不是 `delete`;重跑用 `create`。没有 delete。
# 2. **应用层按归属收口**:每个入口都先确认这个 workflow 的
#    `platform-sdk/submitted-by` 标签等于当前登录用户,不是就 403。
#    **日志也一样** —— 别人作业的日志里可能有他打印出来的敏感数据。
#
# 第 2 层成立的前提是 `X-Forwarded-User` 不可伪造:platform-portal 命名
# 空间的 NetworkPolicy 只放行 oauth2-proxy 连 app 的 8080,集群里其它 pod
# 连不上,伪造不了这个头。**哪天那条 NetworkPolicy 被去掉,这个功能的
# 安全性就没了** —— 所以这句话写在这里,不是写在某个 ADR 里。


def _own_workflow(name, username):
    """取一个 workflow,并确认它确实是这个人提交的。

    返回 (对象, 错误信息)。拿不到或者不是他的,一律返回同一句话 ——
    不区分"不存在"和"不是你的",避免拿这个接口去探测别人的作业名。
    """
    if not username:
        return None, "还没识别出当前登录用户"
    try:
        wf = _k8s().get_namespaced_custom_object(
            "argoproj.io", "v1alpha1", ARGO_NAMESPACE, "workflows", name)
    except Exception:
        return None, "找不到这个作业,或者它不是你提交的"
    if wf.get("metadata", {}).get("labels", {}).get("platform-sdk/submitted-by") != username:
        return None, "找不到这个作业,或者它不是你提交的"
    return wf, None


def _wf_steps(wf):
    """把 workflow 的节点树摊平成"每一步怎么样了"。

    只保留 Pod 类型的节点 —— DAG/Steps 那些是编排容器,它们的失败信息
    是下面某个 Pod 的失败信息的转述,列出来只会让人看到两遍同一件事。
    """
    nodes = (wf.get("status") or {}).get("nodes") or {}
    steps = []
    for node in nodes.values():
        if node.get("type") != "Pod":
            continue
        steps.append({
            "name": node.get("displayName") or node.get("name", ""),
            "pod": node.get("id", ""),
            "phase": node.get("phase", ""),
            "message": node.get("message", ""),
            "started": node.get("startedAt", ""),
            "finished": node.get("finishedAt", ""),
        })
    steps.sort(key=lambda x: x["started"] or "")
    return steps


def _wf_spec_summary(wf):
    """镜像 / 资源 / 参数 —— 排查"为什么这次和上次不一样"最常要的三样。"""
    spec = wf.get("spec") or {}
    templates = spec.get("templates") or []
    images, resources, commands = [], [], []
    for t in templates:
        c = t.get("container") or t.get("script")
        if not c:
            continue
        if c.get("image"):
            images.append(c["image"])
        if c.get("resources"):
            resources.append(c["resources"])
        cmd = " ".join((c.get("command") or []) + (c.get("args") or []))
        if cmd:
            commands.append(cmd)
    params = [(p.get("name"), p.get("value"))
              for p in ((spec.get("arguments") or {}).get("parameters") or [])]
    return {
        "images": images,
        "resources": resources,
        "commands": commands,
        "params": params,
        "service_account": spec.get("serviceAccountName", ""),
        # 队列标签打在 Pod 上不是 Workflow 上(Kueue 的要求,踩过),所以
        # 这里要去 template 的 metadata 里找,不是 workflow 的 labels。
        "queue": next((t.get("metadata", {}).get("labels", {}).get("kueue.x-k8s.io/queue-name")
                       for t in templates
                       if t.get("metadata", {}).get("labels", {}).get("kueue.x-k8s.io/queue-name")), ""),
    }


def _pod_logs(pod_name, tail=200):
    """取一个 pod 的日志。

    Argo 的 pod 里有 main / wait / init 几个容器,只要 main —— wait 容器
    打的是 executor 自己的事,对排查业务失败没用,而且量大。
    """
    if not pod_name:
        return "(这一步还没有产生 Pod)"
    try:
        from kubernetes import client as k8s_client
        # 先走一次 _k8s():kube 配置是全局的,CoreV1Api() 自己不会去加载它。
        # 现在的调用链上 _own_workflow() 已经先调过了,但**依赖调用顺序是
        # 脆的** —— 以后有人换个地方调这个函数就会拿到一个没配置的客户端,
        # 而报错信息会指向认证失败,和真正的原因差着十万八千里。
        _k8s()
        return k8s_client.CoreV1Api().read_namespaced_pod_log(
            pod_name, ARGO_NAMESPACE, container="main", tail_lines=tail) or "(没有输出)"
    except Exception as exc:
        # Pod 被回收之后日志就没了,这是正常的,不是错误 —— 说清楚是哪种
        # 情况,比抛一个 ApiException 给用户看有用。
        return f"(取不到日志:{type(exc).__name__}。Pod 可能已经被清理了)"



# --------------------------------------------------------------- 黄金链路
#
# **门户原来只回答"每个工具在不在线",这一块回答"一件真实的事做不做得成"。**
# 这两个不是一回事——这个平台反复吃的亏就是组件全绿而链路是断的(ADR-079
# 开头列了四个实例)。工具卡片上的绿点是探端口通不通,这里的绿点是探针**真的
# 查了一次数据、真的读了一次目录**之后的结果。
#
# 数据来自 Prometheus 的 `kube_cronjob_status_last_successful_time`
# ——探针每条链路一个 CronJob,kube-state-metrics 天然就有这个指标,门户
# 不需要自己去跑探针,也不需要任何凭据(集群内 Prometheus 免认证)。
PROM = os.environ.get(
    "PROMETHEUS_URL",
    "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090",
)

# 链路名 -> 给人看的说明。key 是 CronJob 名字去掉 goldenpath- 前缀。
GOLDEN_PATHS = {
    "query": ("查数据", "Trino → Iceberg → MinIO/Hive Metastore → OPA"),
    "streaming": ("实时数据", "Kafka → Flink → Iceberg(看的是数据新鲜度)"),
    "catalog": ("数据目录", "Trino 元数据 → OpenMetadata 采集"),
    # 这条和上面几条方向相反:探的是"**该拒的有没有被拒**"。
    # 它红了不代表某个组件坏了,而是**授权可能已经不生效**。
    "authz": ("权限在生效", "拿一张没授权的表去查,查得通才是故障"),
    "model": ("模型可取用", "MLflow 注册表 → 模型有 READY 版本"),
    # 探的是**发一次真实请求拿到预测**,不是 Pod Ready ——
    # 08-28 实测过两种「Pod 绿而服务不可用」的中间态(ADR-080)。
    "inference": ("推理能用", "KServe → 真实请求返回预测"),
}

# 多久没成功算"断了"。和 GoldenPathBroken 告警同一个阈值,**故意保持一致**
# ——门户显红而告警不响(或者反过来)会让人不知道该信哪个。
GOLDEN_PATH_STALE_SEC = 3600


def streams():
    """常驻流作业的状态。

    **为什么不是在工具卡片里放一个 Flink UI 链接**:每个流作业有自己的
    JobManager UI,没有一个"总的 Flink 入口"可以链;而且真正要回答的问题是
    「我的流作业还活着吗」,不是「Flink 的界面在哪」。所以这里直接列
    FlinkDeployment 的状态。
    """
    try:
        api = _k8s()
        items = api.list_namespaced_custom_object(
            "flink.apache.org", "v1beta1", "flink", "flinkdeployments")["items"]
    except Exception as exc:  # noqa: BLE001
        return {"error": f"读不到流作业({type(exc).__name__})", "rows": []}

    rows = []
    for d in items:
        st = d.get("status", {}) or {}
        job = (st.get("jobStatus") or {}).get("state") or "—"
        # jobManagerDeploymentStatus 说的是"容器起来了没有",jobStatus 才是
        # "作业本身在不在跑"。两个都要看:JM 正常而 job 是 FAILED 的组合
        # 恰恰是最容易被忽略的那种坏法。
        jm = st.get("jobManagerDeploymentStatus") or "—"
        rows.append({
            "name": d["metadata"]["name"],
            "job_state": job,
            "jm_state": jm,
            "ok": job == "RUNNING" and jm == "READY",
        })
    return {"error": None, "rows": sorted(rows, key=lambda r: r["name"])}


def golden_paths():
    """每条黄金链路上次做成事是多久以前。取不到就整块降级,不影响别处。"""
    try:
        q = urllib.parse.urlencode({
            "query": 'time() - max by (cronjob) ('
                     'kube_cronjob_status_last_successful_time'
                     '{namespace="monitoring", cronjob=~"goldenpath-.*"})'
        })
        with urllib.request.urlopen(f"{PROM}/api/v1/query?{q}", timeout=4) as r:
            data = json.load(r)
    except Exception:  # noqa: BLE001 - 取不到就降级,门户不能因为它打不开
        return {"error": "连不上 Prometheus,拿不到链路状态",
                "rows": [], "healthy": 0, "total": 0}

    ages = {}
    for item in data.get("data", {}).get("result", []):
        name = item["metric"].get("cronjob", "").removeprefix("goldenpath-")
        try:
            ages[name] = float(item["value"][1])
        except (TypeError, ValueError, IndexError):
            continue

    rows = []
    for key, (label, chain) in GOLDEN_PATHS.items():
        age = ages.get(key)
        rows.append({
            "label": label,
            "chain": chain,
            # 三态而不是两态:"从来没跑过"和"跑过但很久没成功"要分开,
            # 前者多半是刚部署/探针没起来,后者才是链路真的断了。
            "state": "unknown" if age is None
                     else ("ok" if age <= GOLDEN_PATH_STALE_SEC else "broken"),
            "ago": "—" if age is None else _human_ago(age),
        })
    healthy = sum(1 for r in rows if r["state"] == "ok")
    return {"error": None, "rows": rows, "healthy": healthy, "total": len(rows)}


def _human_ago(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 1:
        return "刚刚"
    if m < 60:
        return f"{m} 分钟前"
    return f"{m // 60} 小时 {m % 60} 分钟前"


@app.route("/")
def index():
    username, groups, groups_source = identity.parse_identity(request.headers)
    visible = visible_categories(groups)
    shown = [t for t in TOOLS if visible is None or t["category"] in visible]
    up = probe_all(shown)
    categories = {}
    for tool in shown:
        t = dict(tool)
        t["up"] = up.get(tool["name"], False)
        categories.setdefault(tool["category"], []).append(t)
    return render_template(
        "index.html",
        username=username,
        categories=categories,
        queues=queue_usage(),
        jobs=my_jobs(username),
        golden=golden_paths(),
        streams=streams(),
        permissions=my_permissions(username),
        approvals=my_approvals(username),
        # 续期链接指向权限申请门户。**从 TOOLS 里取,不另拼一遍** —— 门户上
        # 每个链接都必须来自同一份环境配置,2026-08-16 那次"点哪个都 404"
        # 就是各处各拼各的。
        permission_app_url=next(
            (t["url"] for t in TOOLS if t["name"] == "权限申请门户"), "#"),
        logos=LOGOS,
        tool_count=len(shown),
        tool_up=sum(1 for v in up.values() if v),
        groups_warning=identity.diagnose(groups_source, "platform-portal"),
    )


# 「从数据目录里的一张表一键跳到查询」的落脚点(ADR-084)。
#
# 数据目录(OpenMetadata)那边只需要拼一个 portal/query/<catalog>/<schema>/<table>
# 就行,**不用知道 Superset 的任何细节** —— permalink 怎么造、字段是驼峰还是
# 下划线、以后换不换 SQL 工作台,都关在门户这一层里。这是 ADR-084 里
# "退出方案"能成立的原因。
@app.route("/query/<catalog>/<schema>/<table>")
def query_table(catalog, schema, table):
    try:
        path = sqllab.table_query_link(catalog, schema, table)
    except sqllab.SqlLabLinkUnavailable as exc:
        # 降级:深链造不出来就把人送进空的 SQL Lab,别给一个报错页。
        # 少一个预填的编辑器是小事,门户上出现 500 是大事。
        app.logger.warning("SQL Lab 深链降级:%s", exc)
        path = "/sqllab/"
    return redirect(_SQLLAB_BASE + path)


@app.route("/job/<name>")
def job_detail(name):
    username = request.headers.get("X-Forwarded-User", "")
    wf, err = _own_workflow(name, username)
    if err:
        return render_template("job.html", username=username, name=name, error=err), 404
    status = wf.get("status") or {}
    return render_template(
        "job.html", username=username, name=name, error=None,
        phase=status.get("phase", "Pending"),
        # 失败原因摆在最上面。这是打开这个页面最常见的理由,不该让人先
        # 滚过一屏参数才看到。
        message=status.get("message", ""),
        started=status.get("startedAt", ""),
        finished=status.get("finishedAt", ""),
        steps=_wf_steps(wf),
        spec=_wf_spec_summary(wf),
        can_cancel=status.get("phase") in ("Running", "Pending"),
    )


@app.route("/job/<name>/logs/<pod>")
def job_logs(name, pod):
    """某一步的日志。**归属检查和详情页是同一套** —— 别人作业的日志里
    可能有他打印出来的敏感数据,不能因为"只是日志"就放宽。"""
    username = request.headers.get("X-Forwarded-User", "")
    wf, err = _own_workflow(name, username)
    if err:
        abort(404)
    # pod 名必须真的属于这个 workflow,不能拿这个接口当"读任意 pod 日志"用。
    if pod not in {s["pod"] for s in _wf_steps(wf)}:
        abort(404)
    return app.response_class(_pod_logs(pod), mimetype="text/plain; charset=utf-8")


@app.route("/job/<name>/cancel", methods=["POST"])
def job_cancel(name):
    """取消:给 workflow 打 `spec.shutdown=Terminate`,**不是删掉它**。

    删掉会连带丢失这次运行的全部记录(哪一步失败的、日志、参数),而人
    要取消一个作业的时候,恰恰经常是因为它出了问题、接下来要查。
    """
    username = request.headers.get("X-Forwarded-User", "")
    wf, err = _own_workflow(name, username)
    if err:
        abort(404)
    try:
        _k8s().patch_namespaced_custom_object(
            "argoproj.io", "v1alpha1", ARGO_NAMESPACE, "workflows", name,
            {"spec": {"shutdown": "Terminate"}})
    except Exception as exc:
        return {"error": f"取消失败:{type(exc).__name__}"}, 500
    return redirect(url_for("job_detail", name=name))


@app.route("/job/<name>/rerun", methods=["POST"])
def job_rerun(name):
    """重跑:照原样提交一个新的 workflow,**不动原来那个**。

    用 generateName 让 API server 分配新名字,不自己拼 —— 拼名字要处理
    重名、长度上限(k8s 63 字符)这些,而 generateName 本来就是干这个的。
    """
    username = request.headers.get("X-Forwarded-User", "")
    wf, err = _own_workflow(name, username)
    if err:
        abort(404)
    spec = wf.get("spec") or {}
    spec.pop("shutdown", None)      # 原来那个可能是被取消的,别把取消状态也复制过去
    body = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {
            "generateName": f"{name.rsplit('-', 1)[0]}-",
            "namespace": ARGO_NAMESPACE,
            "labels": {
                **(wf.get("metadata", {}).get("labels") or {}),
                # 重跑出来的作业仍然算这个人的,否则它不会出现在他自己的
                # 列表里,也就没人能再管它。
                "platform-sdk/submitted-by": username,
                "platform-portal/rerun-of": name[:63],
            },
        },
        "spec": spec,
    }
    try:
        created = _k8s().create_namespaced_custom_object(
            "argoproj.io", "v1alpha1", ARGO_NAMESPACE, "workflows", body)
    except Exception as exc:
        return {"error": f"重跑失败:{type(exc).__name__}"}, 500
    return redirect(url_for("job_detail", name=created["metadata"]["name"]))


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
