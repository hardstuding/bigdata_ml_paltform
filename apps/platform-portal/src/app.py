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
"""
import os

import requests
from flask import Flask, render_template_string, request

app = Flask(__name__)

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


TEMPLATE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>平台门户</title>
<style>
  body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 1000px; margin: 40px auto; padding: 0 20px; color: #222; }
  h1 { font-size: 1.5em; margin-bottom: 4px; }
  .sub { color: #888; margin-top: 0; }
  h2 { font-size: 1.05em; margin-top: 2em; color: #555; text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.85em; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
  .card { border: 1px solid #ddd; border-radius: 8px; padding: 14px 16px; text-decoration: none; color: inherit; display: block; transition: box-shadow 0.15s; }
  .card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.08); border-color: #bbb; }
  .card-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
  .card-name { font-weight: 600; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .dot-up { background: #2ea043; } .dot-down { background: #ccc; }
  .card-desc { color: #666; font-size: 0.88em; line-height: 1.4; }
  .status-hint { color: #999; font-size: 0.78em; margin-top: 6px; }
</style></head><body>
<h1>平台门户</h1>
<p class="sub">当前登录:<b>{{ username }}</b> · 统一 SSO(Keycloak / realm: platform),点开任何一个工具都不用重新输密码</p>

{% for category, items in categories.items() %}
<h2>{{ category }}</h2>
<div class="grid">
{% for t in items %}
<a class="card" href="{{ t.url }}" target="_blank">
  <div class="card-top">
    <span class="card-name">{{ t.name }}</span>
    <span class="dot {{ 'dot-up' if t.up else 'dot-down' }}" title="{{ '现在能连上' if t.up else '现在连不上(可能是 park 状态,或者刚好在重启)' }}"></span>
  </div>
  <div class="card-desc">{{ t.description }}</div>
</a>
{% endfor %}
</div>
{% endfor %}

<p class="status-hint">绿点=页面加载时现场探测到能连通;灰点=连不上,不代表永久下线,这台机器按需 park/unpark 组件是常态,过一会再看可能就上了。</p>
</body></html>
"""


@app.route("/")
def index():
    username = request.headers.get("X-Forwarded-User", "")
    categories = {}
    for tool in TOOLS:
        t = dict(tool)
        t["up"] = probe(tool)
        categories.setdefault(tool["category"], []).append(t)
    return render_template_string(TEMPLATE, username=username, categories=categories)


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
