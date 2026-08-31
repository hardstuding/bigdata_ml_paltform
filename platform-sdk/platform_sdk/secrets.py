"""读用户自己托管在 OpenBao 里的凭据(ADR-089)。

**为什么这个函数属于 SDK。** ADR-058 给这个包划的边界是"只做两件事:连接
封装、作业提交",而且明确说任何"顺手加个功能"默认拒绝、先记进 roadmap
单独评估 —— ADR-089 就是那次评估。它落在"连接封装"里:一个人要连自己的
MySQL,凭据是连接的一部分,而在这之前他只能写死在代码里(会进 git)或者
每次手动 export(重启就没,定时作业更拿不到)。

**身份从哪来,以及为什么不是 ServiceAccount。** notebook 里读的是**登录
这个 notebook 的人**的凭据,所以用他本人的 OIDC token 去换 OpenBao token。
singleuser pod 的 ServiceAccount 是所有人共用的,OpenBao 只看得到"某个
SA"、分不清是谁 —— 那样"一个人只能读自己的"就没法由 OpenBao 强制,只能
退化成在这个文件里写个 if 判断,而**写在调用方的检查,绕过调用方就没了**
(和 ADR-051 把表级授权交给 OPA 是同一条理由)。
"""
import json
import os
import urllib.error
import urllib.request

from .config import MissingCredential

_TOKEN_ENV = "PLATFORM_OIDC_TOKEN"
_DEFAULT_ADDR = "http://openbao.openbao.svc.cluster.local:8200"
_JWT_ROLE = "platform-user"

# 一次会话里换到的 OpenBao token 缓存起来,不用每次 secret() 都登录一遍。
# **不做过期处理** —— token 过期时下一次读会 403,那时候重新登录一次就好,
# 而自己算过期时间等于把 OpenBao 的 TTL 逻辑抄一份到这里,抄错了不会报错。
_cached_token = None


def _addr():
    return os.environ.get("OPENBAO_ADDR", _DEFAULT_ADDR).rstrip("/")


def _post(path, payload, token=None):
    req = urllib.request.Request(
        f"{_addr()}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if token:
        req.add_header("X-Vault-Token", token)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def _get(path, token):
    req = urllib.request.Request(f"{_addr()}{path}", method="GET")
    req.add_header("X-Vault-Token", token)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def _login():
    """拿用户的 OIDC token 换一个 OpenBao token。"""
    global _cached_token
    if _cached_token:
        return _cached_token
    jwt = os.environ.get(_TOKEN_ENV, "").strip()
    if not jwt:
        raise MissingCredential(
            f"读不到 {_TOKEN_ENV} —— 这个环境变量由 JupyterHub 在 spawn "
            f"notebook 时注入(见 apps/components/jupyterhub.yaml 的 "
            f"03-inject-identity)。\n"
            f"  常见原因:\n"
            f"    1. 不在 notebook 里跑(比如本机 IDE)—— 本机开发用 "
            f"PLATFORM_SECRET_<名字大写> 环境变量代替,见下面 secret() 的说明\n"
            f"    2. notebook 是在这个功能上线之前起的 —— 重启一次 server\n"
            f"    3. JupyterHub 的 auth_state 没生效(hub 日志里会有)")
    try:
        data = _post("/v1/auth/jwt/login", {"role": _JWT_ROLE, "jwt": jwt})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:300]
        if exc.code in (400, 403) and "audience" in body:
            raise MissingCredential(
                f"OpenBao 拒绝了这个 token:{body}\n"
                f"  **这是 audience 对不上,不是权限不够** —— notebook 里拿到的 "
                f"id_token 是 jupyterhub 那个 client 签的,而 OpenBao 的 jwt "
                f"role 要 bound_audiences 里列了它才认。\n"
                f"  查:scripts/50-configure-openbao-auth.sh 里 "
                f"auth/jwt/role/platform-user 的 bound_audiences") from exc
        if exc.code in (400, 403):
            raise MissingCredential(
                f"OpenBao 拒绝了这个 token(HTTP {exc.code}):{body}\n"
                f"  最常见的原因是 **token 过期了** —— Keycloak 默认 5 分钟。\n"
                f"  重启一次 notebook 内核(Kernel → Restart)就会拿到新的。\n"
                f"  自动续期需要把 refresh token 也放进 notebook,那会真正扩大"
                f"暴露面,所以有意不做(ADR-089)。") from exc
        raise
    _cached_token = data["auth"]["client_token"]
    return _cached_token


def _read(token, path):
    """读一个 KV v2 路径,不存在返回 None(不抛)。"""
    try:
        data = _get(f"/v1/secret/data/{path}", token)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return None
        raise
    return (data.get("data") or {}).get("data")


def secret(name, group=None):
    """取一个凭据的值。

        password = platform_sdk.secret("mysql_crm")

    **查找顺序**(第一个命中的胜出):

    1. 环境变量 ``PLATFORM_SECRET_<名字大写>`` —— 本机开发和单元测试用。
       放在最前面是有意的:本机 IDE 里连不上集群内的 OpenBao,而"想试一段
       代码还得先起个集群"会让人绕开整套机制,直接把密码写回代码里。
    2. ``secret/users/<我>/<name>`` —— 自己托管的
    3. ``secret/shared/<我所在的每个组>/<name>`` —— 组共享的

    ``group`` 显式指定时只查那个组,不查个人路径 —— 用在"我要的就是团队
    那份共享账号"的场景,免得个人路径下同名的一份把它盖掉、而人不知道。

    找不到时抛 ``MissingCredential``,**不返回 None** —— 返回 None 的话
    调用方多半会拿它去连数据库,报出来的错是"认证失败",和"凭据没配"差着
    十万八千里。
    """
    env_key = "PLATFORM_SECRET_" + name.upper().replace("-", "_")
    if os.environ.get(env_key):
        return os.environ[env_key]

    token = _login()
    tried = []

    if group is None:
        me = os.environ.get("PLATFORM_USER") or os.environ.get("JUPYTERHUB_USER")
        if me:
            tried.append(f"users/{me}/{name}")
            found = _read(token, f"users/{me}/{name}")
            if found:
                return _single_value(found, name)

    groups = [group] if group else [
        g.strip() for g in os.environ.get("PLATFORM_GROUPS", "").split(",") if g.strip()]
    for g in groups:
        tried.append(f"shared/{g}/{name}")
        found = _read(token, f"shared/{g}/{name}")
        if found:
            return _single_value(found, name)

    raise MissingCredential(
        f"没找到叫 {name!r} 的凭据。找过这些路径:\n"
        + "".join(f"    secret/{t}\n" for t in tried)
        + f"  在门户的「我的凭据」页面添加,或者本机开发时设 {env_key} 环境变量。\n"
        + ("  **注意:一个组都没读到** —— PLATFORM_GROUPS 是空的,组共享的凭据"
           "查不了。\n    这通常是 notebook 起得太早(在组注入生效之前),重启一次 "
           "server。\n" if not groups else ""))


def _single_value(data, name):
    """KV v2 存的是一个 dict。约定只放一个键 ``value``。

    **兼容只有一个键但不叫 value 的情况**:门户写进去的一定是 ``value``,
    但人可能自己在 UI 里建过别的形状。只有一个键时直接用它,多个键时明确
    报错 —— 猜一个返回会让人拿到错的那份而毫无察觉。
    """
    if "value" in data:
        return data["value"]
    if len(data) == 1:
        return next(iter(data.values()))
    raise MissingCredential(
        f"凭据 {name!r} 里有多个字段 {sorted(data)},不知道该给哪个。\n"
        f"  约定是只放一个叫 value 的键(门户写进去的就是这个形状)。")


def list_secrets():
    """列出我能看到的凭据名字,不返回值。

    门户的「我的凭据」页面用它;人也可以在 notebook 里直接调,回答
    "我到底存过哪些"。
    """
    token = _login()
    out = {"users": [], "shared": {}}
    me = os.environ.get("PLATFORM_USER") or os.environ.get("JUPYTERHUB_USER")
    if me:
        out["users"] = _list(token, f"users/{me}")
    for g in [g.strip() for g in os.environ.get("PLATFORM_GROUPS", "").split(",") if g.strip()]:
        names = _list(token, f"shared/{g}")
        if names:
            out["shared"][g] = names
    return out


def _list(token, path):
    req = urllib.request.Request(f"{_addr()}/v1/secret/metadata/{path}?list=true",
                                 method="GET")
    req.add_header("X-Vault-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return []
        raise
    return (data.get("data") or {}).get("keys") or []
