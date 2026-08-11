#!/usr/bin/env python3
"""
把 platform/iam/{roles.yaml,groups.yaml,memberships.csv} 这三份数据同步进
Keycloak(realm role、group、group 的 role-mapping、group 成员)。

这是**声明式同步**,不是只加不减:group 的 role-mapping 和 group 的成员都会
做完整对账——Keycloak 里有、但这三个文件里已经没有的,会被移除。这点很重要:
访问控制系统如果只加不减,人离职/调组之后权限会一直留着,是真实的安全问题,
不能只图脚本写得简单。role 本身(roles.yaml 里定义的)不做自动删除——删掉一
个还在被引用的角色定义,影响面不看代码看不出来,这一步留给人手动做。

命令式操作(调 kcadm.sh),不在 GitOps 管理范围内——和
scripts/03-configure-keycloak.sh 是同一个原因(见 docs/decisions/009):
Keycloak 没有官方支持的"用 YAML/CSV 声明式管理 realm 内容"方案,这个脚本
就是那个缺失的声明式层,手动实现"git 里的文件 = Keycloak 里的状态"。

用法:
    python3 scripts/12-sync-iam.py                    # 人手动跑,新用户会自动建号
    python3 scripts/12-sync-iam.py --no-create-users   # 无人值守场景用(见 apps/iam-sync/),
                                                        # 遇到 Keycloak 里还不存在的用户只报警不新建

前置条件:Keycloak 在跑、scripts/00-generate-secrets.sh 已经生成了
keycloak-admin 这个 Secret、scripts/03-configure-keycloak.sh 已经建好了
platform realm。

memberships.csv 里如果有 Keycloak 还没有的用户,默认会自动建号(密码写进
secrets/generated-credentials.txt,和 03 建初始用户是同一个模式)——
`--no-create-users` 模式下不会,只会打印警告跳过,见 ADR-031:自动化场景
(apps/iam-sync/ 那个 CronJob)生成的密码没人能看到,等于建了个永久登不进
去的账号,新用户必须由人手动跑一次(不带这个参数)才能拿到看得见的密码。
"""
import argparse
import base64
import csv
import json
import secrets
import string
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
IAM_DIR = REPO_ROOT / "platform" / "iam"
NO_CREATE_USERS = False
KC_NS = "keycloak"
KC_POD = "keycloak-keycloakx-0"
REALM = "platform"
CRED_FILE = REPO_ROOT / "secrets" / "generated-credentials.txt"


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"!! 命令失败: {' '.join(cmd)}\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def kcadm(*args: str) -> str:
    return run(["kubectl", "-n", KC_NS, "exec", KC_POD, "--", "/opt/keycloak/bin/kcadm.sh", *args])


def kcadm_json(*args: str):
    out = kcadm(*args)
    return json.loads(out) if out.strip() else None


def get_secret_value(namespace: str, name: str, key: str) -> str:
    out = run(["kubectl", "get", "secret", "-n", namespace, name, "-o", f"jsonpath={{.data.{key}}}"])
    return base64.b64decode(out).decode()


def gen_password(n: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def login():
    pw = get_secret_value(KC_NS, "keycloak-admin", "password")
    kcadm("config", "credentials", "--server", "http://localhost:8080/auth",
          "--realm", "master", "--user", "admin", "--password", pw)


def find_by_field(endpoint: str, field: str, value: str):
    # Keycloak 的 groups 端点不认 -q 过滤(实测:传了也会原样返回全部
    # group,不是这段代码没写对),所以这里必须客户端自己再过滤一遍,不能
    # 假设 kcadm_json 返回的就已经是过滤后的结果——users/roles 端点的 -q
    # 是真的会过滤,但这个函数两种情况都要处理,统一在这一层做客户端过滤
    # 更安全。
    out = kcadm_json("get", endpoint, "-r", REALM, "-q", f"{field}={value}") or []
    for item in out:
        if item.get(field) == value:
            return item
    return None


def ensure_role(name: str, description: str):
    if find_by_field("roles", "name", name):
        print(f"  role {name} 已存在,跳过")
        return
    kcadm("create", "roles", "-r", REALM, "-s", f"name={name}", "-s", f"description={description}")
    print(f"  建了 role {name}")


def ensure_group(name: str) -> str:
    existing = find_by_field("groups", "name", name)
    if existing:
        return existing["id"]
    kcadm("create", "groups", "-r", REALM, "-s", f"name={name}")
    return find_by_field("groups", "name", name)["id"]


def sync_group_roles(group_id: str, group_name: str, desired_roles: set[str]):
    current = {r["name"] for r in (kcadm_json("get", f"groups/{group_id}/role-mappings/realm", "-r", REALM) or [])}
    for role_name in desired_roles - current:
        kcadm("add-roles", "-r", REALM, "--gid", group_id, "--rolename", role_name)
        print(f"  group {group_name} 加上了 role {role_name}")
    for role_name in current - desired_roles:
        kcadm("remove-roles", "-r", REALM, "--gid", group_id, "--rolename", role_name)
        print(f"  group {group_name} 移除了 role {role_name}(不在 roles.yaml/groups.yaml 里了)")


def ensure_user(username: str) -> str | None:
    existing = find_by_field("users", "username", username)
    if existing:
        return existing["id"]
    if NO_CREATE_USERS:
        # apps/iam-sync/ 那个 CronJob 每 5 分钟无人值守跑一次,这个分支故意
        # 不让它自动建新用户——建号会生成一个随机密码,CronJob 是从 git
        # 现拉的临时 pod,Job 一结束密码就随 pod 一起没了,没人能看到,等于
        # 建了一个永远登不进去的账号。新用户必须由人手动跑这个脚本(不带
        # --no-create-users)创建,密码会打印在这个人自己的终端里,不会
        # 丢。见 ADR-031。
        print(f"  !! {username} 在 Keycloak 里还不存在,自动同步(CronJob)不会新建账号"
              f"——请手动跑 `python3 scripts/12-sync-iam.py` 建号,密码会打印在终端", file=sys.stderr)
        return None
    email = f"{username}@example.com"
    # firstName/lastName 必须填:Keycloak 的 User Profile 校验把这两个字段
    # 标成必填,漏填的账号在 password grant 等非交互式登录场景下会报
    # "Account is not fully set up"(错误信息完全看不出跟这个有关系,是
    # 建 zhenghe 账号时真实踩过的坑,见 docs/decisions/028)。CSV 里没有
    # 单独的姓名字段,先用用户名本身占位,不是理想的真实姓名,但能让账号
    # 立刻可登录;有真实姓名需求的话以后在 memberships.csv 里加列。
    kcadm("create", "users", "-r", REALM, "-s", f"username={username}",
          "-s", f"email={email}", "-s", f"firstName={username}", "-s", f"lastName={username}",
          "-s", "enabled=true", "-s", "emailVerified=true")
    user_id = find_by_field("users", "username", username)["id"]
    pw = gen_password()
    kcadm("set-password", "-r", REALM, "--userid", user_id, "--new-password", pw, "--temporary=false")
    CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CRED_FILE.open("a") as f:
        f.write(f"keycloak-platform-realm {username} / {pw}\n")
    print(f"  建了新用户 {username},密码写进 {CRED_FILE}")
    return user_id


def sync_group_members(group_id: str, group_name: str, desired_usernames: set[str]):
    current_members = {m["username"]: m["id"] for m in (kcadm_json("get", f"groups/{group_id}/members", "-r", REALM) or [])}
    for username in desired_usernames - current_members.keys():
        user_id = ensure_user(username)
        if user_id is None:
            continue
        kcadm("update", f"users/{user_id}/groups/{group_id}", "-r", REALM,
              "-s", f"userId={user_id}", "-s", f"groupId={group_id}", "-n")
        print(f"  {username} 加入了 group {group_name}")
    for username, user_id in current_members.items():
        if username not in desired_usernames:
            kcadm("delete", f"users/{user_id}/groups/{group_id}", "-r", REALM)
            print(f"  {username} 从 group {group_name} 移除了(不在 memberships.csv 里了)")


def main():
    global NO_CREATE_USERS
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-create-users", action="store_true")
    args = parser.parse_args()
    NO_CREATE_USERS = args.no_create_users

    roles = yaml.safe_load((IAM_DIR / "roles.yaml").read_text())["roles"]
    groups = yaml.safe_load((IAM_DIR / "groups.yaml").read_text())["groups"]

    memberships = defaultdict(set)
    with (IAM_DIR / "memberships.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            memberships[row["group"].strip()].add(row["username"].strip())

    print("==> 登录 Keycloak admin CLI")
    login()

    print("==> 同步 roles(只加不减,删角色定义要人手动做)")
    for role_name, role_def in roles.items():
        ensure_role(role_name, role_def.get("description", ""))

    print("==> 同步 groups(角色映射 + 成员都做完整对账,加也会减)")
    for group in groups:
        group_name = group["name"]
        group_id = ensure_group(group_name)
        sync_group_roles(group_id, group_name, set(group.get("roles", [])))
        sync_group_members(group_id, group_name, memberships.get(group_name, set()))

    known_groups = {g["name"] for g in groups}
    unknown = set(memberships) - known_groups
    if unknown:
        print(f"!! memberships.csv 里有 groups.yaml 没定义的组,忽略了: {unknown}", file=sys.stderr)

    print("==> 完成")


if __name__ == "__main__":
    main()
