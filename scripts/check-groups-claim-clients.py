#!/usr/bin/env python3
"""检查:凡是按 Keycloak 组做权限判断的组件,它的 client 必须挂 groups scope。

**为什么需要这个检查。** `03-configure-keycloak.sh` 里有一个硬编码的 client
名单,只有名单里的 client 才会挂上 `groups` 这个 client scope。不在名单里的
client,登录时拿到的 token 里**根本没有 groups 这个字段** —— 而组件那边的
按组授权代码照常运行,只是永远走"拿不到组 → 用默认值"那条分支。

**后果是完全静默的**:登录正常、页面正常、功能正常,只有权限判断是错的。
这个模式在这个仓库里反复出现过四次:

    2026-08-19  Grafana / JupyterHub —— 自称"按 group 收紧已验证",实际
                id_token 里没有 groups,allowed_groups 是摆设
    2026-08-29  Superset —— 不在名单里,于是权限门户的批准/拒绝、审计页
                对所有人 403
    2026-08-29  Trino 的 is_platform_admin 一直是摆设(ADR-078)
    2026-09-02  Airflow —— 有 AUTH_ROLES_MAPPING,但拿不到 groups,于是
                每个登录的人都落到 AUTH_USER_REGISTRATION_ROLE,而那个值
                当时是 "Admin"

每一次都是靠别的事情顺带发现的,没有一次是主动查出来的。这个脚本把它变成
一条 CI 里会红的检查。

用法:
    python3 scripts/check-groups-claim-clients.py
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 组件配置里出现这些,就说明它在按组做权限判断
GROUP_USAGE_PATTERNS = [
    r"AUTH_ROLES_MAPPING",        # Flask-AppBuilder(Superset / Airflow)
    r"allowed_groups",            # oauth2-proxy
    r"role_attribute_path",       # Grafana
    r"groups_claim",
    r'["\']groups["\']\s*:',      # 各种自定义映射
]

# 组件文件名 → 它在 Keycloak 里的 client id(不一致的才需要写在这)。
# `-oauth2-proxy` 后缀是统一去掉的:那是部署形态,不是 client 名。
CLIENT_ID_OF = {
    "spark-history": "spark-history-server",
    "permission-request": "permission-request-app",
    "table-registration": "table-registration-app",
    "portal": "platform-portal",
    "platform-portal": "platform-portal",
}


def client_id_of(component: str) -> str:
    name = component.removesuffix("-oauth2-proxy")
    return CLIENT_ID_OF.get(name, name)

# 不按组做权限判断、因此不需要 groups 的组件
NO_GROUPS_NEEDED = {
    # MinIO 用的是单独的策略 claim,名字对不上 groups,见 ADR-088
    "minio",
}


def clients_in_keycloak_script() -> set[str]:
    src = (ROOT / "templates/scripts/03-configure-keycloak.sh").read_text(encoding="utf-8")
    m = re.search(r"for gc in ((?:[^\n;]|\\\n)+?); do", src)
    if not m:
        sys.exit("!! 没在 03-configure-keycloak.sh 里找到挂 groups scope 的那个 for 循环 —— "
                 "循环写法变了的话,这个检查也要跟着改,否则它会一直是绿的")
    return set(m.group(1).replace("\\\n", " ").split())


def components_using_groups() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted((ROOT / "apps/components").glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        # 只看真正的配置行,注释里提到 groups 不算
        lines = [l for l in text.split("\n") if not l.strip().startswith("#")]
        body = "\n".join(lines)
        hits = [p for p in GROUP_USAGE_PATTERNS if re.search(p, body)]
        if hits:
            name = path.stem
            found[client_id_of(name)] = hits
    return found


def main() -> int:
    listed = clients_in_keycloak_script()
    using = components_using_groups()
    missing = {c: h for c, h in using.items()
               if c not in listed and c not in NO_GROUPS_NEEDED}

    if missing:
        print(f"有 {len(missing)} 个组件在按组做权限判断,但它的 Keycloak client "
              f"没挂 groups scope:")
        for c, hits in sorted(missing.items()):
            print(f"  - {c}:配置里用了 {', '.join(hits)},"
                  f"但不在 03-configure-keycloak.sh 的名单里")
        print()
        print("  后果是静默的:登录正常、功能正常,只有权限判断永远走"
              "「拿不到组 → 用默认值」那条分支。")
        print("  修法:把 client id 加进 templates/scripts/03-configure-keycloak.sh "
              "里 `for gc in ...` 那个名单,重新渲染,再跑一次那个脚本。")
        return 1

    print(f"{len(using)} 个组件按组做权限判断,它们的 client 都挂了 groups scope。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
