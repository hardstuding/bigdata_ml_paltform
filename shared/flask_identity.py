"""从 oauth2-proxy 传下来的头里解出"当前登录的人是谁、在哪些组"。

**这份是权威源。** 三个自建 Flask 应用(platform-portal /
permission-request-app / table-registration-app)各有一份逐字节相同的副本
在自己的 `src/identity.py` 里,由 `scripts/check-duplicated-sources.py` 在
CI 里保证不漂移。之所以是"复制 + 检查"而不是做成一个 Python 包:三个应用
是三个独立镜像、各自 `pip install` 自己的依赖,为了 60 行代码引入一个内部
包 + 发布流程,复杂度远大于收益(ADR-083 那套内部包机制是给**用户的**包
用的,不是给平台自己这三个小应用用的)。

改这个文件之后,跑 `python3 scripts/check-duplicated-sources.py --fix`。

---

**这个模块存在的真正理由,是一个踩了三次的坑。**

`groups == []` 有两种截然不同的含义:

  a) 这个人真的不在任何组 —— 正常状态
  b) groups 这个 claim 压根没被传过来 —— 配置问题

而代码里它们**长得一模一样**,于是"按组判断"的分支永远走 else,不报错、
没有任何信号。这个项目栽过三次:

- ADR-078:Trino 没配 group provider,传给 OPA 的 groups 永远是空,
  `is_platform_admin` 从来没触发过 —— 而 `opa test` 全过,因为测试的
  input 是手写的、带着 groups。
- 2026-08-29 Superset:Keycloak client 没挂 groups scope,只能用
  `AUTH_USER_REGISTRATION_ROLE="Admin"` 兜底,结果**任何能登录的人都是
  管理员**。
- 2026-08-29 permission-request-app:同样没挂 groups scope,
  `is_approver` 永远是 False,组权限申请的批准/审计/交接页对所有人 403。

所以这里把 (a) 和 (b) 分开,并且给出**能直接照着做的**修复提示。
"""
from __future__ import annotations

import base64
import json


def parse_identity(headers):
    """返回 (用户名, 组列表, 来源)。

    `来源` 是给 `diagnose()` 用的,取值:
      - `claim_present`   —— token 里有 groups 字段,内容可信(空就是真的没组)
      - `claim_missing`   —— token 解开了,但没有 groups 字段(配置问题)
      - `no_token`        —— 压根没拿到 access token(oauth2-proxy 没开
                             pass_access_token)
      - `token_unparseable` —— 有 token 但解不开

    **不校验签名**,也不需要:请求已经过了 oauth2-proxy,而应用只从
    oauth2-proxy 可达(靠 NetworkPolicy 保证)。在这里再验一次签名要引入
    JWKS 拉取和缓存,换不到实际的安全收益。**但这句话依赖那条
    NetworkPolicy**,去掉它这里就是可伪造的。
    """
    username = headers.get("X-Forwarded-User", "")
    token = headers.get("X-Forwarded-Access-Token", "")
    if not token or token.count(".") != 2:
        return username, [], "no_token"
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return username, [], "token_unparseable"
    username = claims.get("preferred_username", username)
    if "groups" not in claims:
        return username, [], "claim_missing"
    return username, (claims.get("groups") or []), "claim_present"


def diagnose(source, client_id):
    """按组判断的功能能不能生效;不能的话,返回一句能照着做的话。

    返回 None 表示一切正常(包括"这个人确实不在任何组"这种正常情况 ——
    那不该吓唬人)。
    """
    if source == "claim_missing":
        return (f"这次登录拿到的令牌里没有 groups 字段,所以按组判断的功能都不会"
                f"生效。这是配置问题不是权限问题:需要给 Keycloak 的 {client_id} "
                f"client 挂上 groups 这个 default client scope —— 跑一次 "
                f"scripts/03-configure-keycloak.sh,然后重新登录。")
    if source == "no_token":
        return ("这次请求里没有访问令牌,按组判断的功能不会生效。"
                "检查 oauth2-proxy 的 pass_access_token 是不是开着。")
    if source == "token_unparseable":
        return "访问令牌解不开,按组判断的功能不会生效。"
    return None
