"""
权限自助申请门户——见 docs/decisions/032-permission-request-app.md 和
docs/decisions/044-tiered-approval-workflow.md(表访问分级审批,ADR-040
需求2 Phase 2)。

单文件 Flask app,故意不用前端框架(轻量级申请是这个门户的定位,不是要做
一个成熟的开发者门户,那条路线评估过 Backstage 之类的工具,判断不划算,
见同一份 ADR)。

身份来自 oauth2-proxy 挡在前面之后传进来的请求头:
- X-Forwarded-User / X-Forwarded-Email:oauth2-proxy 默认就传(legacy
  config 格式下不需要额外配置)。
- X-Forwarded-Access-Token:需要在 oauth2-proxy 配置里显式开
  `pass_access_token = true` 才会有——这个 app 自己把这个 JWT 的 payload
  解出来读 `groups` claim,不校验签名(信任边界是"这个 app 的 Service
  只应该被 oauth2-proxy 代理到",和这个项目其他信任反向代理注入的请求头
  是同一个模型,没有引入新的信任假设)。之所以要自己解 JWT 而不是让
  oauth2-proxy 直接传一个 X-Forwarded-Groups 请求头,是因为 legacy config
  格式没有这个能力(只有更啰嗦的 alpha config 格式支持自定义请求头映射,
  ADR-019 当初决定 legacy 格式够用,不想为了这一个组件推翻那个决定,所以
  绕过去自己解 token)。

表访问分级审批这部分范围边界(ADR-044 详细记录):这里只做"决策与留痕"
——按 ADR-040 原文规则算出谁要审批、记录每一步的批准/拒绝,最终写一份
`table-access-grants.csv` 进 git。

**这份 grants 数据是真的被消费的**:Trino 的访问控制走 OPA(ADR-051),
`opa-grants-sync` 每 5 分钟把这个 csv 推给 OPA,没有 grant 的表查询会被
`PERMISSION_DENIED` 拒掉;行级过滤和列级脱敏(ADR-063)也按同一份数据的
`security_level` 生效。这个模块顶部原本写着"不做真正的拦截、没有任何执行
引擎消费这份数据",那是 ADR-051 之前的状态,**2026-08-29 更正**。
"""
import base64
import csv
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from flask import Flask, abort, g, redirect, render_template_string, request, url_for

import identity

app = Flask(__name__)

# 模板里用 {{ x|zh }} 把状态英文枚举转成中文;{{ t|localtime }} 把 UTC
# 时间戳转成一个带 data-utc 的 <time>,由页面底部那段 JS 按**浏览器自己的
# 时区**渲染 —— 服务端不知道用户在哪个时区,猜不如让浏览器算。
app.jinja_env.filters["zh"] = lambda v: status_label(v)


def _localtime_html(value):
    if not value:
        return ""
    from markupsafe import Markup, escape
    return Markup(f'<time class="lt" datetime="{escape(value)}">{escape(value)}</time>')


app.jinja_env.filters["localtime"] = _localtime_html


DB_PATH = os.environ.get("DB_PATH", "/data/requests.db")
REPO_URL = os.environ.get("REPO_URL", "https://github.com/hardstuding/bigdata_ml_paltform.git")
GIT_TOKEN = os.environ.get("GIT_TOKEN", "")

# platform-team 不放进来:自助申请不能让人给自己批平台管理员权限,这个组
# 只能走 platform/iam/groups.yaml + memberships.csv 手动改 + PR review。
AVAILABLE_GROUPS = ["data-analysts", "algorithm-team", "viewers"]
APPROVER_GROUP = "platform-team"

# 表访问分级审批用到的组织架构数据(虚拟占位,见 platform/iam/employees.csv
# 文件头注释和 ADR-044——以后接公司真实 HR 数据,只要保持列名一致直接换
# 文件内容,这里的代码不用改)。挂载方式和 app.py 本身一样,是同一个
# ConfigMap 里的另一个 key,见 manifests/app-configmap.yaml。
EMPLOYEES_PATH = os.environ.get("EMPLOYEES_PATH", "/src/employees.csv")

# L3(安全等级3)的"指定负责人"不是从组织架构推导出来的——ADR-040 原文
# 这条本来就是"指定的人",不是职级+N 这种可以从汇报线算出来的角色。
DESIGNATED_ADMIN = os.environ.get("DESIGNATED_ADMIN", "admin")

# 【可插拔基础设施,见 ADR-030】和 table-registration-app 读同一个
# OpenMetadata 实例,查表的 SecurityLevel Tag 和 Owner 用。
OPENMETADATA_URL = os.environ.get("OPENMETADATA_URL", "http://openmetadata.openmetadata.svc.cluster.local:8585")
OPENMETADATA_TOKEN = os.environ.get("OPENMETADATA_TOKEN", "")

# ADR-045:企业微信群机器人 webhook,官方标准格式,未配置时 notify_wecom()
# 静默跳过——和 GIT_TOKEN/OPENMETADATA_TOKEN 同一个"敏感凭据不自动生成"
# 的降级模式,不是必须品。
WECOM_WEBHOOK_URL = os.environ.get("WECOM_WEBHOOK_URL", "")

# 一个审批步骤等这么多小时没人处理,先提醒;等到 2 倍时长还没处理,才真正
# 升级换人审(不是自动通过,见 ADR-045)。
ESCALATION_HOURS = float(os.environ.get("ESCALATION_HOURS", "48"))

# /internal/escalation-check 这个内部端点给 CronJob 调,不走 oauth2-proxy/
# 人类登录那一套,靠这个共享密钥防止集群内其他东西误触发。没配置时这个
# 端点直接拒绝所有请求(拒绝比误开安全)。
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "")

# ADR-045:可插拔审批后端。"local"(默认)就是现在这套自己收批准/拒绝
# 点击;"webhook" 模式下,一个 step 轮到时改成 POST 到
# EXTERNAL_OA_WEBHOOK_URL 出去,不在这个页面等人点,靠外部系统回调
# /table-access/step/<id>/external-callback 报告结果。这次没有真实对接
# 目标,只交付这个协议本身,见 ADR-045 的范围边界说明。
# 三档(ADR-086):
#   local(**默认**)—— 平台自己收批准点击。测试阶段用这个。
#   oa    —— **接公司 OA 时用这一档。** 整张申请单一次 POST 给 OA,OA 走
#            它自己的审批链,审批结束一次回调最终结果。
#   webhook —— ADR-045 那套"按步 POST、每步带 approver_username"。
#            **形状是错的,不推荐**:那等于平台自己算出该谁批、再让 OA 去
#            执行,而 OA 才是有组织架构和审批规则的那一方。保留只是为了
#            不破坏可能已经存在的对接,见 ADR-086。
APPROVAL_BACKEND = os.environ.get("APPROVAL_BACKEND", "local")

# oa 模式下:整单 POST 到这里,OA 审完回调
# /table-access/request/<id>/oa-callback。
EXTERNAL_OA_REQUEST_URL = os.environ.get("EXTERNAL_OA_REQUEST_URL", "")
# 平台自己的对外地址,拼 callback_url 用 —— OA 得知道往哪回调。
PLATFORM_PUBLIC_URL = os.environ.get("PLATFORM_PUBLIC_URL", "")
EXTERNAL_OA_WEBHOOK_URL = os.environ.get("EXTERNAL_OA_WEBHOOK_URL", "")
EXTERNAL_OA_CALLBACK_TOKEN = os.environ.get("EXTERNAL_OA_CALLBACK_TOKEN", "")

# ADR-050:表访问授权到期回收。table-access-grants.csv 之前 expires_at
# 这一列一直是空的(从来没被写过),等于权限永久有效、永远不回收——这是
# ADR-040 原本就要求、但一直没落地的一个真实缺口。默认 180 天
# (半年,常见的企业访问复审周期),可以整体调,不做"按安全等级不同过期
# 时长"这种更细的策略(现在还没有真实数据支撑该怎么分级定时长,做了也是
# 瞎猜)。
GRANT_EXPIRY_DAYS = int(os.environ.get("GRANT_EXPIRY_DAYS", "180"))

# 到期前多少天开始提醒。7 天是按"够走一轮审批链"选的:L3 的链有四级,
# 提前一天说等于没说。
EXPIRY_WARN_DAYS = int(os.environ.get("EXPIRY_WARN_DAYS", "7"))

APPROVAL_ROLE_LABELS = {
    "manager": "直属上级",
    "manager_manager": "上级的上级",
    "table_owner": "表负责人",
    "designated_admin": "指定管理员(L3)",
}

# 状态在页面上一直是直接印英文枚举值(`pending` / `approved_pending_apply`)。
# 对申请人来说 `approved_pending_apply` 尤其糟:它字面像"批了",实际是
# "批了但还没落到 git,权限还没生效",这两件事对使用者的意义完全不同。
STATUS_LABELS = {
    "pending": "等待审批",
    "pending_external": "已转外部 OA,等待回复",
    "approved": "已通过,权限已生效",
    "approved_pending_apply": "已通过,但授权还没写进 git(权限尚未生效)",
    "applied": "已生效",
    "rejected": "已拒绝",
    "escalated": "已超时升级",
    "skipped": "无需审批(前序已拒绝)",
}


def status_label(value):
    """英文枚举 → 中文。认不出来的原样返回,不吞掉信息。"""
    return STATUS_LABELS.get(value, value)


# 申请理由按安全等级决定是否必填。
#
# **为什么不是一律必填**:1 级表(公开/低敏)强制写理由,只会逼出"查数"
# 这种没有信息量的占位文字,反而稀释了真正需要理由的场合。2 级起必填,
# 因为那时审批人真的要靠理由做判断 —— 他批的是"这个人为什么需要看这些"。
REASON_REQUIRED_FROM_LEVEL = int(os.environ.get("REASON_REQUIRED_FROM_LEVEL", "2"))
MIN_REASON_LENGTH = 10


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            group_name TEXT NOT NULL,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_at TEXT NOT NULL,
            decided_by TEXT,
            decided_at TEXT,
            note TEXT
        )
        """
    )
    # 表访问申请:主表只存申请本身的信息,谁要审批、审到哪一步全部在
    # approval_steps 里,不是简单的一对一状态,是"分级链"。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS table_access_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            table_fqn TEXT NOT NULL,
            security_level INTEGER NOT NULL,
            table_owner TEXT,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_at TEXT NOT NULL,
            decided_at TEXT,
            note TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES table_access_requests(id),
            step_order INTEGER NOT NULL,
            approver_role TEXT NOT NULL,
            approver_username TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            decided_at TEXT,
            UNIQUE(request_id, step_order, approver_username)
        )
        """
    )
    # ADR-045 加的字段,用 ALTER TABLE 做轻量迁移(这个 app 一直是"单文件
    # SQLite,没有专门的迁移工具"这个量级,CREATE TABLE IF NOT EXISTS 对
    # 已存在的表不会补新列,所以要单独处理;已经加过就会报错,忽略即可,
    # 是幂等的)。
    for stmt in (
        "ALTER TABLE approval_steps ADD COLUMN activated_at TEXT",
        # 审批意见。批准时可选,**拒绝时必填** —— 一条没有理由的拒绝,
        # 申请人除了重新申请一遍之外无事可做,而重新申请多半会被再拒一次。
        "ALTER TABLE approval_steps ADD COLUMN comment TEXT",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    for stmt in (
        # 催办:记上次催的时间,用来限频。不限频的话催办按钮就是一个
        # 给审批人刷屏的工具,最后的结果是所有通知都被无视。
        "ALTER TABLE table_access_requests ADD COLUMN last_nudged_at TEXT",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def get_current_user():
    """返回 (用户名, 组列表)。

    副作用:把"token 里到底有没有 groups 这个 claim"记进 `g.groups_source`,
    给 `groups_diagnosis()` 用。解析逻辑和另外两个 Flask 应用共用一份
    (`shared/flask_identity.py`,CI 有防漂移检查)—— 那个文件顶部写了这
    件事为什么值得单独拆出来。
    """
    username, groups, source = identity.parse_identity(request.headers)
    g.groups_source = source
    return username, groups


def groups_diagnosis():
    """按组判断的功能能不能生效;不能的话给一句能照着做的话。

    **"配置没配对"和"这个人真的不在任何组"在代码里长得一模一样
    (groups == []),后果却完全不同** —— 这个项目已经因为分不开它们栽过
    三次,清单在 shared/flask_identity.py 顶部。
    """
    return identity.diagnose(getattr(g, "groups_source", None), "permission-request-app")


def is_approver(groups):
    return APPROVER_GROUP in groups


def notify_wecom(text: str):
    """企业微信群机器人标准 webhook 格式(官方文档就是这个 body 结构,
    不是猜的)。WECOM_WEBHOOK_URL 没配就静默跳过;发送失败也不抛出去
    影响主流程——通知是锦上添花,不能因为通知失败把审批操作本身搞挂。"""
    if not WECOM_WEBHOOK_URL:
        return
    try:
        requests.post(
            WECOM_WEBHOOK_URL,
            json={"msgtype": "text", "text": {"content": text}},
            timeout=5,
        )
    except requests.RequestException:
        pass


def apply_to_git(username: str, group_name: str):
    """把这条申请写进 platform/iam/memberships.csv 并 push。没配 GIT_TOKEN
    (还没让用户去 GitHub 建一个 fine-grained PAT 塞进 Secret 之前的状态)
    就不硬失败,退化成"告诉你要手动加哪一行",申请记录仍然是"批准"状态,
    只是应用这一步需要人补一刀——好过用户点了批准却完全没反应、以为是
    bug。见 ADR-032。"""
    new_line = f"{username},{group_name}"
    if not GIT_TOKEN:
        return False, f"没有配置 git 写权限(GIT_TOKEN 未设置),请手动把这行加进 platform/iam/memberships.csv 并 push:{new_line}"

    tmpdir = tempfile.mkdtemp()
    try:
        auth_url = REPO_URL.replace("https://", f"https://{GIT_TOKEN}@")
        subprocess.run(
            ["git", "clone", "--depth", "1", auth_url, tmpdir],
            check=True, capture_output=True, text=True, timeout=60,
        )
        csv_path = Path(tmpdir) / "platform" / "iam" / "memberships.csv"
        lines = [l for l in csv_path.read_text().splitlines() if l.strip()]
        if new_line in lines:
            return True, "已经在 memberships.csv 里了,不用重复加"
        lines.append(new_line)
        csv_path.write_text("\n".join(lines) + "\n")

        subprocess.run(["git", "-C", tmpdir, "config", "user.email", "permission-request-app@platform.local"], check=True)
        subprocess.run(["git", "-C", tmpdir, "config", "user.name", "Permission Request App"], check=True)
        subprocess.run(["git", "-C", tmpdir, "add", "platform/iam/memberships.csv"], check=True)
        subprocess.run(
            ["git", "-C", tmpdir, "commit", "-m", f"iam: {username} 加入 {group_name}(自助申请门户批准)"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(["git", "-C", tmpdir, "push"], check=True, capture_output=True, text=True, timeout=60)
        return True, "已提交进 git,几分钟内 iam-sync CronJob(ADR-031)会同步进 Keycloak"
    except subprocess.CalledProcessError as e:
        stderr = e.stderr if hasattr(e, "stderr") else str(e)
        return False, f"git 操作失败,请人工处理:{stderr}\n应该加的这行:{new_line}"
    except subprocess.TimeoutExpired:
        return False, f"git 操作超时(可能是网络问题),请人工处理,应该加的这行:{new_line}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def apply_grant_to_git(username: str, table_fqn: str, security_level: int):
    """审批全部通过后,把这条访问授权记录写进
    platform/iam/table-access-grants.csv——和 apply_to_git() 同一个
    clone/改文件/commit/push 模式,复用同一套 GIT_TOKEN。

    **这份文件是真的被消费的**:`opa-grants-sync` 每 5 分钟把它推给 OPA
    (ADR-051),所以这一行写进去之后,那个人**最多 5 分钟内**就真的能查
    那张表了。这段注释原本写着"只是决策留痕,不会被任何东西读去真正拦截
    Trino 查询",那是 ADR-051 之前的状态,**2026-08-29 更正** —— 这是同一
    句过期描述在这个文件里的第三处(模块顶部、reclaim_expired、这里)。
    """
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    expires_at = (now_dt + timedelta(days=GRANT_EXPIRY_DAYS)).isoformat()
    new_line = f"{username},{table_fqn},{security_level},{now},{expires_at}"
    if not GIT_TOKEN:
        return False, f"没有配置 git 写权限(GIT_TOKEN 未设置),请手动把这行加进 platform/iam/table-access-grants.csv 并 push:{new_line}"

    tmpdir = tempfile.mkdtemp()
    try:
        auth_url = REPO_URL.replace("https://", f"https://{GIT_TOKEN}@")
        subprocess.run(
            ["git", "clone", "--depth", "1", auth_url, tmpdir],
            check=True, capture_output=True, text=True, timeout=60,
        )
        csv_path = Path(tmpdir) / "platform" / "iam" / "table-access-grants.csv"
        header = "username,table_fqn,security_level,granted_at,expires_at"
        if csv_path.exists():
            lines = [l for l in csv_path.read_text().splitlines() if l.strip()]
        else:
            lines = [header]
        lines.append(new_line)
        csv_path.write_text("\n".join(lines) + "\n")

        subprocess.run(["git", "-C", tmpdir, "config", "user.email", "permission-request-app@platform.local"], check=True)
        subprocess.run(["git", "-C", tmpdir, "config", "user.name", "Permission Request App"], check=True)
        subprocess.run(["git", "-C", tmpdir, "add", "platform/iam/table-access-grants.csv"], check=True)
        subprocess.run(
            ["git", "-C", tmpdir, "commit", "-m", f"iam: {username} 获批访问 {table_fqn}(分级审批通过)"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(["git", "-C", tmpdir, "push"], check=True, capture_output=True, text=True, timeout=60)
        return True, "已提交进 git(platform/iam/table-access-grants.csv)"
    except subprocess.CalledProcessError as e:
        stderr = e.stderr if hasattr(e, "stderr") else str(e)
        return False, f"git 操作失败,请人工处理:{stderr}\n应该加的这行:{new_line}"
    except subprocess.TimeoutExpired:
        return False, f"git 操作超时(可能是网络问题),请人工处理,应该加的这行:{new_line}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def load_employees():
    """读 employees.csv,返回 {username: {"name":..., "manager_username":...}}。
    manager_id 是自关联外键,这里解成直接可用的 manager_username,调用方
    不用自己再去查一次 employee_id -> username 的映射。"""
    result = {}
    if not Path(EMPLOYEES_PATH).exists():
        return result
    with open(EMPLOYEES_PATH, newline="") as f:
        rows = list(csv.DictReader(f))
    by_id = {r["employee_id"]: r for r in rows}
    for r in rows:
        manager_username = None
        manager_id = (r.get("manager_id") or "").strip()
        if manager_id and manager_id in by_id:
            manager_username = by_id[manager_id]["username"]
        result[r["username"]] = {"name": r.get("name", ""), "manager_username": manager_username}
    return result


def get_manager_chain(username: str, levels: int = 2):
    """返回 [直属上级, 上级的上级, ...],最多 levels 个,遇到断链(没有上级
    数据,比如顶层或者这个用户压根不在 employees.csv 里)就提前结束——
    不是报错,是"这一级审批人是空的",路由逻辑那边会跳过。"""
    employees = load_employees()
    chain = []
    current = username
    for _ in range(levels):
        info = employees.get(current)
        if not info or not info["manager_username"]:
            break
        chain.append(info["manager_username"])
        current = info["manager_username"]
    return chain


def om_request(method: str, path: str):
    resp = requests.request(
        method, f"{OPENMETADATA_URL}{path}",
        headers={"Authorization": f"Bearer {OPENMETADATA_TOKEN}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else None


# OpenMetadata 里那个 Trino DatabaseService 的名字(scripts/29 建的)。
# 表在 OM 里的完整 FQN 是 `<服务名>.<catalog>.<schema>.<表>`,而调用方手里
# 的表名通常来自 Trino,是不带服务名的三段式 —— lookup_table_governance()
# 两种都认,靠的就是这个常量。
OM_DATABASE_SERVICE = os.environ.get("OPENMETADATA_DATABASE_SERVICE", "trino")


def lookup_table_governance(table_fqn: str):
    """查 OpenMetadata 里这张表的安全等级和负责人。table_fqn 是完整 FQN
    (比如 trino.iceberg.demo.orders,和 table-registration-app 建表时
    用的 databaseSchema 前缀拼法一致)。查不到/没配 token 时返回
    (None, None),调用方要处理这种情况,不能假设一定查得到。"""
    if not OPENMETADATA_TOKEN:
        return None, None

    # **两种写法都收:带不带 OM 服务名前缀。**
    #
    # 2026-08-30 实测踩到:这个函数要的是 OM 的完整 FQN
    # (`trino.iceberg.demo.orders`),而 /api/table-governance 自己的参数
    # 说明和 400 报错里写的例子是 `iceberg.demo.orders`(Trino 里的写法)。
    # 也就是说**外部系统按这个接口自己说的格式调,永远只会拿到 404** ——
    # 而 404 的措辞是"数据目录里查不到这张表",看起来完全像是数据问题,
    # 不像是参数格式问题。这是最坏的一类接口 bug:它不报错,它撒谎。
    #
    # 与其去统一措辞(哪一种才"对"其实没有绝对答案:调用方手里拿到的
    # 表名来自 Trino,那边就是不带服务名前缀的),不如两种都认。
    candidates = [table_fqn]
    if not table_fqn.startswith(f"{OM_DATABASE_SERVICE}."):
        candidates.append(f"{OM_DATABASE_SERVICE}.{table_fqn}")

    data = None
    for fqn in candidates:
        try:
            data = om_request(
                "GET", f"/api/v1/tables/name/{fqn}?fields=owners,tags,extension")
            break
        except requests.HTTPError:
            continue
    if data is None:
        return None, None
    security_level = None
    for tag in data.get("tags", []) or []:
        tag_fqn = tag.get("tagFQN", "")
        if tag_fqn.startswith("SecurityLevel.Level"):
            try:
                security_level = int(tag_fqn.rsplit("Level", 1)[1])
            except ValueError:
                pass
    owner_username = None
    for o in (data.get("owners") or []):
        if o.get("type") == "user":
            owner_username = o.get("name")
            break
    # register_table_in_openmetadata() 里,owner 从没登录过 OM 时会降级
    # 写进 extension 字段而不是 owners 关联(见 table-registration-app),
    # 这里同样兜底读一次,不然那种表永远走不出"查不到负责人"的死路。
    ext = data.get("extension") or {}
    if security_level is None:
        security_level = ext.get("securityLevel")
    if owner_username is None:
        owner_username = ext.get("registeredOwner")
    return security_level, owner_username


def list_catalog_tables(q: str = "", schema_filter: str = "", security_level_filter: str = ""):
    """给"浏览目录勾选要申请的表"这个界面用(见 ADR-046)——列出 OpenMetadata
    里已经登记过的表(建表注册工具回写的那些),带上安全等级/负责人,支持
    按关键字/schema/安全等级筛选。这不是分页浏览一整个大目录的通用实现
    (`limit=200` 封顶,够这个项目当前的表规模用),表多到需要真分页时
    再改。查不到/没配 token 时返回空列表,不报错——调用方(catalog 页面)
    要能处理"目录暂时看不到"这种情况,不能整个页面挂掉。"""
    if not OPENMETADATA_TOKEN:
        return []
    try:
        # extension 要显式声明才会返回(实测确认,不加这个字段负责人的
        # extension 降级兜底值就读不到,列表页会显示"—"),和
        # lookup_table_governance() 单表查询那边同一个坑。
        data = om_request("GET", "/api/v1/tables?fields=owners,tags,extension&limit=200")
    except requests.RequestException:
        return []
    results = []
    for entity in data.get("data", []) or []:
        fqn = entity.get("fullyQualifiedName", "")
        parts = fqn.split(".")
        schema = parts[-2] if len(parts) >= 2 else ""
        security_level = None
        for tag in entity.get("tags", []) or []:
            tag_fqn = tag.get("tagFQN", "")
            if tag_fqn.startswith("SecurityLevel.Level"):
                try:
                    security_level = int(tag_fqn.rsplit("Level", 1)[1])
                except ValueError:
                    pass
        ext = entity.get("extension") or {}
        if security_level is None:
            security_level = ext.get("securityLevel")
        owner_username = None
        for o in (entity.get("owners") or []):
            if o.get("type") == "user":
                owner_username = o.get("name")
                break
        if owner_username is None:
            owner_username = ext.get("registeredOwner")
        columns = [c.get("name") for c in (entity.get("columns") or [])]

        if q and q.lower() not in fqn.lower():
            continue
        if schema_filter and schema != schema_filter:
            continue
        if security_level_filter and str(security_level) != security_level_filter:
            continue
        results.append({
            "fqn": fqn, "schema": schema, "security_level": security_level,
            "owner": owner_username, "columns": columns,
        })
    results.sort(key=lambda r: r["fqn"])
    return results


def build_approval_steps(applicant_username: str, table_owner: str, security_level: int):
    """按 ADR-040 原文规则(L2 在 L1 基础上叠加,不是只要 +2)算出这次申请
    需要哪几级、每级谁来审。返回 [(step_order, role, username), ...]。

    "叠加"指的是**总共需要哪些人批准**,不是"L1 的人到 L2 要重新批一遍"
    ——状态机本来就是按 step_order 顺序推进(前一级全部批完才解锁下一级),
    L1 的人在 L1 就已经批完了,没有理由在 L2 再点一次。每一级只放"这一级
    新增的人",用一个贯穿全程的 `already_required` 集合去重:同一个人不管
    是在同一级身兼多角色、还是在更高级别里又被算到过一次,都只需要批准
    一次(2026-08-14 实测过第一版实现漏了这个,L2/L3 会让 L1 的人重复批,
    这是修过的真实 bug,不是从一开始就设计对的)。"""
    chain = get_manager_chain(applicant_username, levels=2)
    manager = chain[0] if len(chain) >= 1 else None
    manager_manager = chain[1] if len(chain) >= 2 else None

    levels = [[]]
    if manager:
        levels[0].append(("manager", manager))
    if table_owner:
        levels[0].append(("table_owner", table_owner))

    if security_level >= 2:
        levels.append([("manager_manager", manager_manager)] if manager_manager else [])

    if security_level >= 3:
        levels.append([("designated_admin", DESIGNATED_ADMIN)])

    result = []
    already_required = set()
    for step_order, approvers in enumerate(levels, start=1):
        for role, uname in approvers:
            if uname in already_required:
                continue
            # **申请人永远不能是自己的审批人。**(2026-08-29 加)
            #
            # 这不是理论问题,有一条真实可走的路径:建表注册工具的 owner
            # 是一个自由填写的表单字段,谁都能把自己填成某张表的负责人;
            # 而 table_owner 在这里是第一级审批人。于是"自己建表填自己 →
            # 之后申请这张表的权限 → 自己批自己"就成立了。组织架构里查不到
            # 上级的人(manager 为 None),这条链上甚至只有他一个人,等于
            # 完全自助授权。
            #
            # 建表那边的 owner 校验会另外收紧,但这一层是**兜底**:不管
            # owner 是怎么变成他自己的,他都不该出现在自己的审批链里。
            if uname == applicant_username:
                continue
            already_required.add(uname)
            result.append((step_order, role, uname))
    return result


def current_step_order(conn, request_id: int):
    """这个申请现在卡在第几级——第一个还有未解决行(本地待审或者已经
    转出去等外部 OA 回调)的 step_order。全部批完了返回 None。"""
    row = conn.execute(
        "SELECT MIN(step_order) AS s FROM approval_steps WHERE request_id=? AND status IN ('pending', 'pending_external')",
        (request_id,),
    ).fetchone()
    return row["s"]


def approval_policy(security_level: int, table_owner: str | None) -> dict:
    """这张表**需要什么强度的审批** —— 平台该告诉 OA 的就是这个。

    **注意这里没有 approver_username。** 谁是直属上级、请假了谁代理、会签
    还是或签,全是 OA 的事;平台知道的是"这张表几级、负责人是谁",那是
    数据治理的知识,OA 不可能知道(ADR-086)。
    """
    levels = 1
    if security_level >= 2:
        levels = 2
    if security_level >= 3:
        levels = 3
    parts = ["L1:直属上级 + 表负责人"]
    if levels >= 2:
        parts.append("L2:上级的上级")
    if levels >= 3:
        parts.append("L3:指定管理员")
    return {
        "levels": levels,
        "policy": ";".join(parts),
        "table_owner": table_owner or "",
        "security_level": security_level,
    }


def dispatch_to_oa(conn, request_id: int, req_row) -> bool:
    """整张申请单一次 POST 给 OA(ADR-086 的 `oa` 模式)。

    返回是否成功交出去。**失败要退化成本地审批** —— 不能让 OA 抽风导致
    申请卡死在没人能处理的状态,这一点和 ADR-045 的判断一样。
    """
    if not EXTERNAL_OA_REQUEST_URL:
        return False
    callback = (f"{PLATFORM_PUBLIC_URL.rstrip('/')}/table-access/request/{request_id}/oa-callback"
                if PLATFORM_PUBLIC_URL else "")
    body = {
        "request_id": request_id,
        "applicant": req_row["username"],
        "table_fqn": req_row["table_fqn"],
        "security_level": req_row["security_level"],
        "reason": req_row["reason"],
        "required_approval": approval_policy(req_row["security_level"], req_row["table_owner"]),
        "callback_url": callback,
    }
    try:
        requests.post(EXTERNAL_OA_REQUEST_URL, json=body, timeout=5).raise_for_status()
    except Exception:   # noqa: BLE001 —— 交不出去就退化成本地审批
        return False
    # 整单交出去了:所有 step 一起标成 pending_external。**不逐级派发** ——
    # OA 眼里这是一张单子,不是 N 张。
    conn.execute(
        "UPDATE approval_steps SET status='pending_external', activated_at=? WHERE request_id=?",
        (datetime.now(timezone.utc).isoformat(), request_id))
    return True


def dispatch_step(conn, step_row):
    """一行 approval_steps 变成"轮到它"时调用(提交时的第一级,或者前一级
    刚批完解锁下一级)。按 APPROVAL_BACKEND 决定接下来怎么办:
    - local(默认):留在 status='pending',发一条企微通知给这个人。
    - webhook:POST 到外部 OA,状态改成 pending_external(不再出现在
      "待我审批"列表里,责任已经转移出去了,见 ADR-045)。POST 失败就
      留在 pending,退化成本地审批,不能让外部系统抽风导致这一步卡死
      没人能处理。"""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE approval_steps SET activated_at=? WHERE id=? AND activated_at IS NULL",
        (now, step_row["id"]),
    )
    req = conn.execute(
        "SELECT * FROM table_access_requests WHERE id=?", (step_row["request_id"],)
    ).fetchone()
    if APPROVAL_BACKEND == "webhook" and EXTERNAL_OA_WEBHOOK_URL:
        try:
            requests.post(
                EXTERNAL_OA_WEBHOOK_URL,
                json={
                    "request_id": step_row["request_id"], "step_id": step_row["id"],
                    "table_fqn": req["table_fqn"], "security_level": req["security_level"],
                    "approver_username": step_row["approver_username"],
                    "approver_role": step_row["approver_role"],
                    "applicant": req["username"], "reason": req["reason"],
                },
                timeout=5,
            )
            conn.execute("UPDATE approval_steps SET status='pending_external' WHERE id=?", (step_row["id"],))
            return
        except requests.RequestException:
            pass  # 退化成本地审批,不吞掉这一步
    notify_wecom(
        f"你有一条表访问申请待审批:{req['username']} 申请 {req['table_fqn']}"
        f"(安全等级 {req['security_level']}),你的角色是"
        f"{APPROVAL_ROLE_LABELS.get(step_row['approver_role'], step_row['approver_role'])}。"
    )


def activate_next_step(conn, request_id: int):
    """把当前最早的 pending step_order 下所有 pending 行标成"已激活"并
    分发出去(见 dispatch_step)。用在:①提交申请时激活第一级;②某一级
    全部批完、状态机推进到下一级时。"""
    step_order = current_step_order(conn, request_id)
    if step_order is None:
        return
    rows = conn.execute(
        "SELECT * FROM approval_steps WHERE request_id=? AND step_order=? AND status='pending' AND activated_at IS NULL",
        (request_id, step_order),
    ).fetchall()
    for row in rows:
        dispatch_step(conn, row)


def finalize_table_request_if_done(conn, request_id: int):
    """一个 approval_steps 行状态变化后调用:检查这个申请是不是该终态了
    (全部批准,或者有任何一步被拒),不是就推进到下一级。"escalated" 状态
    的行是"已经换人审"的旧行,不计入"是否全部批准"的判断(见 ADR-045 的
    升级机制),"skipped" 是申请已经因为别的原因被拒之后的收尾状态,同样
    不计入。"""
    rows = conn.execute(
        "SELECT status FROM approval_steps WHERE request_id=?", (request_id,)
    ).fetchall()
    statuses = [r["status"] for r in rows]
    if "rejected" in statuses:
        conn.execute(
            "UPDATE table_access_requests SET status='rejected', decided_at=? WHERE id=? AND status='pending'",
            (datetime.now(timezone.utc).isoformat(), request_id),
        )
        # 已经被拒了,其余还没审的步骤没有意义了,标成 skipped 避免审批人
        # 列表里一直挂着一条其实已经作废的待办。
        conn.execute(
            "UPDATE approval_steps SET status='skipped' WHERE request_id=? AND status IN ('pending', 'pending_external')",
            (request_id,),
        )
        req = conn.execute("SELECT * FROM table_access_requests WHERE id=?", (request_id,)).fetchone()
        notify_wecom(f"你的表访问申请被拒绝:{req['table_fqn']}(安全等级 {req['security_level']})")
        return
    blocking = [s for s in statuses if s not in ("escalated", "skipped")]
    if blocking and all(s == "approved" for s in blocking):
        req = conn.execute("SELECT * FROM table_access_requests WHERE id=?", (request_id,)).fetchone()
        ok, note = apply_grant_to_git(req["username"], req["table_fqn"], req["security_level"])
        # **必须看 ok。** 2026-08-29 之前这里接收了 ok 却从来不用,状态无条件
        # 写成 approved,还给用户发"已全部批准"——**授权根本没写进 git 也照样
        # 报成功**。而 OPA 读的就是那个 csv,所以用户会拿到一个"批准了但查不了"
        # 的结果,而系统显示一切正常。在权限系统里这是最不能有的一类 bug:
        # 它同时骗了用户和审计。
        #
        # 决策(审批人点了同意)和执行(授权落到 git)是两件事,失败时要能
        # 区分开:决策不该因为执行失败而丢掉,否则得让所有审批人重审一遍。
        # 所以引入 approved_pending_apply 这个中间态——决策已定、执行待重试。
        status = "approved" if ok else "approved_pending_apply"
        conn.execute(
            "UPDATE table_access_requests SET status=?, decided_at=?, note=? WHERE id=?",
            (status, datetime.now(timezone.utc).isoformat(), note, request_id),
        )
        if ok:
            notify_wecom(f"你的表访问申请已全部批准:{req['table_fqn']}(安全等级 {req['security_level']})")
        else:
            # 通知里**不要说"已批准"** —— 用户据此去查数会失败,然后来问
            # "为什么批了还查不了"。说清楚现在是什么状态、要等什么。
            notify_wecom(
                f"表访问申请已通过审批,但授权写入失败,还不能用:{req['table_fqn']}"
                f"(安全等级 {req['security_level']})。原因:{note}。"
                f"平台会自动重试;急的话联系平台组。"
            )
        return
    # 还没到终态,可能前一级刚批完,看看要不要激活下一级。
    activate_next_step(conn, request_id)


TEMPLATE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>平台权限申请</title>
<style>
  body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 980px; margin: 40px auto; padding: 0 20px; color: #222; }
  h1 { font-size: 1.4em; } h2 { font-size: 1.1em; margin-top: 2em; border-bottom: 1px solid #eee; padding-bottom: 6px; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 0.92em; }
  th, td { border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }
  th { background: #fafafa; }
  .status-pending, .status-pending_external { color: #b8860b; } .status-applied, .status-approved { color: #228b22; }
  .status-rejected { color: #b22222; } .status-approved_pending_apply { color: #ff8c00; }
  .status-escalated, .status-skipped { color: #888; }
  .badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 0.82em; }
  .badge-pending, .badge-pending_external { background: #fff3cd; color: #7a5c00; }
  .badge-approved, .badge-applied { background: #d4edda; color: #1e6b2e; }
  .badge-rejected { background: #f8d7da; color: #92242f; }
  .badge-escalated, .badge-skipped { background: #eee; color: #666; }
  form.inline { display: inline; margin-right: 4px; }
  button { cursor: pointer; padding: 4px 10px; }
  .hint { color: #888; font-size: 0.85em; }
  .warn-box { background: #fff8e1; border: 1px solid #ffd54f; border-radius: 6px;
              padding: 10px 13px; font-size: 0.9em; line-height: 1.6; }
  select, input[type=text] { padding: 5px; margin-right: 6px; }
  .steps { margin: 0; padding-left: 18px; font-size: 0.9em; }
  .steps li.done { color: #228b22; } .steps li.rejected { color: #b22222; } .steps li.waiting { color: #b8860b; }
  .steps li.future { color: #aaa; } .steps li.escalated, .steps li.skipped { color: #888; }
  nav { margin-bottom: 20px; padding-bottom: 10px; border-bottom: 2px solid #eee; }
  nav a { margin-right: 16px; color: #555; text-decoration: none; font-size: 0.95em; }
  nav a.current { color: #222; font-weight: bold; }
</style>
</head>
<body>
<nav>
  <a class="current" href="{{ url_for('index') }}">申请 / 待审批</a>
  <a href="{{ url_for('table_access_catalog') }}">浏览目录申请</a>
  {% if is_approver %}<a href="{{ url_for('audit') }}">审计</a><a href="{{ url_for('transfer') }}">权限交接</a>{% endif %}
  <a href="{{ url_for('table_access_help') }}">怎么用</a>
</nav>
<h1>平台权限申请</h1>
<p>当前登录:<b>{{ username }}</b>{% if is_approver %} <span class="hint">(你在 platform-team,可以审批组权限申请)</span>{% endif %}</p>
{% if groups_warning %}
{# "配置没配对"和"你不在任何组"在代码里长得一模一样(groups == []),
   后果却完全不同。这个项目因为分不开它们栽过三次,所以这里直接说出来。 #}
<p class="warn-box">⚠ {{ groups_warning }}</p>
{% endif %}

<h2>提交新申请:加入某个组</h2>
<form method="post" action="{{ url_for('submit_request') }}">
  <select name="group_name" required>
    {% for g in available_groups %}<option value="{{ g }}">{{ g }}</option>{% endfor %}
  </select>
  <input type="text" name="reason" placeholder="申请理由(可选)" size="40">
  <button type="submit">提交申请</button>
</form>
<p class="hint">platform-team(平台管理员)不能自助申请,需要走 git PR 人工 review,见 platform/iam/groups.yaml。</p>

<h2>我的申请记录</h2>
<table>
<tr><th>ID</th><th>申请的组</th><th>理由</th><th>状态</th><th>申请时间</th></tr>
{% for r in my_requests %}
<tr>
<td>{{ r.id }}</td><td>{{ r.group_name }}</td><td>{{ r.reason or '' }}</td>
<td class="status-{{ r.status }}">{{ r.status|zh }}{% if r.note %}<br><span class="hint">{{ r.note }}</span>{% endif %}</td>
<td>{{ r.requested_at|localtime }}</td>
</tr>
{% else %}
<tr><td colspan="5" class="hint">还没提交过申请</td></tr>
{% endfor %}
</table>

{% if is_approver %}
<h2>待审批:组权限申请({{ pending|length }} 条)</h2>
<table>
<tr><th>ID</th><th>申请人</th><th>申请的组</th><th>理由</th><th>时间</th><th>操作</th></tr>
{% for r in pending %}
<tr>
<td>{{ r.id }}</td><td>{{ r.username }}</td><td>{{ r.group_name }}</td><td>{{ r.reason or '' }}</td><td>{{ r.requested_at|localtime }}</td>
<td>
<form class="inline" method="post" action="{{ url_for('approve', req_id=r.id) }}"><button type="submit">批准</button></form>
<form class="inline" method="post" action="{{ url_for('reject', req_id=r.id) }}"><button type="submit">拒绝</button></form>
</td>
</tr>
{% else %}
<tr><td colspan="6" class="hint">没有待审批的申请</td></tr>
{% endfor %}
</table>
{% endif %}

<h2>提交新申请:表访问(分级审批)</h2>
<p class="hint">不知道确切表名?去<a href="{{ url_for('table_access_catalog') }}"><b>浏览目录申请</b></a>页,能搜索/按安全等级筛选、勾选多张表一起申请。这里是给已经知道确切表名的人用的快捷入口。</p>
<form method="post" action="{{ url_for('submit_table_access') }}">
  <input type="text" name="table_fqn" placeholder="完整表名,比如 trino.iceberg.demo.orders" size="45" required>
  <input type="text" name="reason" placeholder="申请理由(可选)" size="30">
  <button type="submit">提交申请</button>
</form>
<p class="hint">安全等级和负责人从 OpenMetadata 里这张表的登记信息读取(建表注册工具回写的,见 ADR-043);审批链按 ADR-040 规则算,见 <a href="{{ url_for('table_access_help') }}">怎么用这个流程</a>。</p>

<h2>我的表访问申请</h2>
<table>
<tr><th>ID</th><th>表名</th><th>安全等级</th><th>状态</th><th>审批进度</th><th>时间</th></tr>
{% for r in my_table_requests %}
<tr>
<td>{{ r.id }}</td><td>{{ r.table_fqn }}</td><td>L{{ r.security_level }}</td>
<td class="status-{{ r.status }}">{{ r.status|zh }}{% if r.note %}<br><span class="hint">{{ r.note }}</span>{% endif %}</td>
<td>
<ol class="steps">
{% for s in r.steps %}
<li class="{{ 'done' if s.status == 'approved' else ('rejected' if s.status == 'rejected' else (s.status if s.status in ('escalated', 'skipped') else ('waiting' if s.step_order == r.current_step else 'future'))) }}">
第{{ s.step_order }}级 · {{ role_labels[s.approver_role] }}({{ s.approver_username }}):{{ s.status|zh }}{% if s.comment %}<br><span class="hint">意见:{{ s.comment }}</span>{% endif %}
</li>
{% endfor %}
</ol>
</td>
<td>{{ r.requested_at|localtime }}
{% if r.status == 'pending' %}
<form class="inline" method="post" action="{{ url_for('nudge_request', req_id=r.id) }}">
  <button type="submit" title="提醒当前这一级的审批人。{{ nudge_cooldown }} 小时内只能催一次">催办</button>
</form>
{% endif %}
</td>
</tr>
{% else %}
<tr><td colspan="6" class="hint">还没提交过表访问申请</td></tr>
{% endfor %}
</table>

<h2>待我审批:表访问({{ my_actionable|length }} 条)</h2>
<table>
<tr><th>申请ID</th><th>申请人</th><th>表名</th><th>安全等级</th><th>我的角色</th><th>理由</th><th>操作</th></tr>
{% for s in my_actionable %}
<tr>
<td>{{ s.request_id }}</td><td>{{ s.username }}</td><td>{{ s.table_fqn }}</td><td>L{{ s.security_level }}</td>
<td>{{ role_labels[s.approver_role] }}</td><td>{{ s.reason or '' }}</td>
<td>
{# 意见框两个按钮共用一个 —— 批准时可选,拒绝时必填(required 只是前端
   提示,服务端也校验,直接 POST 绕不过去)。 #}
<form class="inline" method="post" action="{{ url_for('approve_table_step', step_id=s.step_id) }}">
  <input type="hidden" name="comment" class="linked-comment" value="">
  <button type="submit">批准</button>
</form>
<form class="inline reject-form" method="post" action="{{ url_for('reject_table_step', step_id=s.step_id) }}">
  <input type="text" name="comment" class="comment-box" placeholder="意见(拒绝必填)" maxlength="500">
  <button type="submit">拒绝</button>
</form>
</td>
</tr>
{% else %}
<tr><td colspan="7" class="hint">没有轮到你审批的表访问申请</td></tr>
{% endfor %}
</table>
<script>
// 时间一律按**浏览器自己的时区**渲染。服务端存的是 UTC ISO 串,直接印出来
// 对人是不可读的("2026-08-29T11:48:52+00:00" 要心算时差),而服务端并不
// 知道用户在哪个时区 —— 猜不如让浏览器算。
// 超过 3 天的显示绝对时间(那时"几天前"已经没有意义),3 天内显示相对时间
// ("2 小时前"),因为审批场景里"等了多久"比"哪一天提的"更要紧。
// 审批意见:一个输入框服务两个按钮。批准时把它带上(可选),拒绝时
// 空着就先在前端拦一下 —— 服务端同样会拒(400),这里只是省一次往返。
document.querySelectorAll('tr').forEach(function (tr) {
  var box = tr.querySelector('.comment-box');
  if (!box) { return; }
  var hidden = tr.querySelector('.linked-comment');
  if (hidden) { box.addEventListener('input', function () { hidden.value = box.value; }); }
  var rejectForm = tr.querySelector('.reject-form');
  if (rejectForm) {
    rejectForm.addEventListener('submit', function (e) {
      if (!box.value.trim()) {
        e.preventDefault();
        box.focus();
        box.placeholder = '拒绝必须写原因——申请人要知道该怎么改';
      }
    });
  }
});
document.querySelectorAll('time.lt').forEach(function (el) {
  var raw = el.getAttribute('datetime');
  var d = new Date(raw);
  if (isNaN(d)) { return; }   // 解析不了就保持原样,不要变成 "Invalid Date"
  var diffMin = (Date.now() - d.getTime()) / 60000;
  var abs = d.toLocaleString();
  if (diffMin >= 0 && diffMin < 60 * 24 * 3) {
    var rel = diffMin < 1 ? '刚刚'
            : diffMin < 60 ? Math.floor(diffMin) + ' 分钟前'
            : diffMin < 60 * 24 ? Math.floor(diffMin / 60) + ' 小时前'
            : Math.floor(diffMin / 1440) + ' 天前';
    el.textContent = rel;
  } else {
    el.textContent = abs;
  }
  el.title = abs + '(UTC ' + raw + ')';   // 悬停仍能看到精确值
});
</script>
</body>
</html>
"""

HELP_TEMPLATE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>怎么用表访问审批流程</title>
<style>body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 760px; margin: 40px auto; padding: 0 20px; color: #222; line-height: 1.7; }
h1 { font-size: 1.3em; } code { background: #f5f5f5; padding: 1px 5px; border-radius: 3px; }</style>
</head><body>
<h1>怎么申请/审批表访问权限</h1>
<p><a href="{{ url_for('index') }}">&larr; 返回申请页</a></p>

<h2>申请人</h2>
<ol>
<li>先确认要访问的表已经通过建表注册工具登记过(有 owner 和安全等级),没登记过的表这里查不到,申请会失败。</li>
<li>填完整表名(比如 <code>trino.iceberg.demo.orders</code>)提交,系统会自动算出这张表要走几级审批、分别是谁审。</li>
<li>在"我的表访问申请"里能看到每一级的实时进度,轮到谁审、审完没有一目了然。</li>
</ol>

<h2>审批规则(见 ADR-040/044)</h2>
<ul>
<li><b>安全等级 1</b>:你的直属上级 + 表负责人,两个人都批准才算过。</li>
<li><b>安全等级 2</b>:在等级 1 的基础上,再加你上级的上级——是叠加,不是只要多一个人审。</li>
<li><b>安全等级 3</b>:在等级 2 的基础上,再加平台指定的管理员。</li>
</ul>
<p class="hint">上下级关系来自 <code>platform/iam/employees.csv</code>(目前是占位的虚拟组织架构,公司真实 HR 数据接入后会替换,规则和这里描述的一样)。</p>

<h2>审批人</h2>
<p>首页"待我审批"只会显示<b>轮到你</b>的申请——比如你是某张表的二级审批人,一级还没审完之前你在这里看不到这条申请,不用自己判断"是不是该我审了"。</p>

<h2>范围边界(如实说明,别误解)</h2>
<p>审批通过后,授权会写进 <code>platform/iam/table-access-grants.csv</code>,
Trino 的访问控制(OPA)读的就是这份数据 —— <b>批准之后才查得到,没批准会被拒绝</b>,
不是只做个记录。行级过滤和列级脱敏也按同一份授权生效。</p>
<p>授权同步有几十秒延迟(每 5 分钟一轮同步,通常更快)。刚批完立刻查还被拒的话,
稍等一下再试。</p>
<script>
// 时间一律按**浏览器自己的时区**渲染。服务端存的是 UTC ISO 串,直接印出来
// 对人是不可读的("2026-08-29T11:48:52+00:00" 要心算时差),而服务端并不
// 知道用户在哪个时区 —— 猜不如让浏览器算。
// 超过 3 天的显示绝对时间(那时"几天前"已经没有意义),3 天内显示相对时间
// ("2 小时前"),因为审批场景里"等了多久"比"哪一天提的"更要紧。
document.querySelectorAll('time.lt').forEach(function (el) {
  var raw = el.getAttribute('datetime');
  var d = new Date(raw);
  if (isNaN(d)) { return; }   // 解析不了就保持原样,不要变成 "Invalid Date"
  var diffMin = (Date.now() - d.getTime()) / 60000;
  var abs = d.toLocaleString();
  if (diffMin >= 0 && diffMin < 60 * 24 * 3) {
    var rel = diffMin < 1 ? '刚刚'
            : diffMin < 60 ? Math.floor(diffMin) + ' 分钟前'
            : diffMin < 60 * 24 ? Math.floor(diffMin / 60) + ' 小时前'
            : Math.floor(diffMin / 1440) + ' 天前';
    el.textContent = rel;
  } else {
    el.textContent = abs;
  }
  el.title = abs + '(UTC ' + raw + ')';   // 悬停仍能看到精确值
});
</script>
</body></html>
"""

AUDIT_TEMPLATE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>审计</title>
<style>
  body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 1100px; margin: 40px auto; padding: 0 20px; color: #222; }
  h1 { font-size: 1.4em; } h2 { font-size: 1.1em; margin-top: 2em; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 0.88em; }
  th, td { border: 1px solid #ddd; padding: 6px 8px; text-align: left; vertical-align: top; }
  th { background: #fafafa; }
  .badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 0.82em; }
  .badge-pending, .badge-pending_external { background: #fff3cd; color: #7a5c00; }
  .badge-approved, .badge-applied { background: #d4edda; color: #1e6b2e; }
  .badge-rejected { background: #f8d7da; color: #92242f; }
  .badge-escalated, .badge-skipped { background: #eee; color: #666; }
  nav { margin-bottom: 20px; padding-bottom: 10px; border-bottom: 2px solid #eee; }
  nav a { margin-right: 16px; color: #555; text-decoration: none; }
  .hint { color: #888; font-size: 0.85em; }
</style></head><body>
<nav><a href="{{ url_for('index') }}">申请 / 待审批</a><a class="current" href="{{ url_for('audit') }}">审计</a><a href="{{ url_for('transfer') }}">权限交接</a></nav>
<h1>审计</h1>
<p class="hint">只读,platform-team 可见。数据不会被删除,只会追加/改状态,这里能看到完整历史(见 ADR-045)。</p>

<h2>表访问申请({{ table_requests|length }} 条,最近200条)</h2>
<table>
<tr><th>ID</th><th>申请人</th><th>表名</th><th>等级</th><th>状态</th><th>提交时间</th><th>审批链</th></tr>
{% for r in table_requests %}
<tr>
<td>{{ r.id }}</td><td>{{ r.username }}</td><td>{{ r.table_fqn }}</td><td>L{{ r.security_level }}</td>
<td><span class="badge badge-{{ r.status }}">{{ r.status|zh }}</span>{% if r.note %}<br><span class="hint">{{ r.note }}</span>{% endif %}</td>
<td>{{ r.requested_at|localtime }}</td>
<td>{% for s in r.steps %}第{{ s.step_order }}级 {{ role_labels[s.approver_role] }}({{ s.approver_username }}):<span class="badge badge-{{ s.status }}">{{ s.status|zh }}</span>{% if s.decided_at %} @{{ s.decided_at|localtime }}{% endif %}{% if s.comment %} <span class="hint">「{{ s.comment }}」</span>{% endif %}<br>{% endfor %}</td>
</tr>
{% else %}
<tr><td colspan="7" class="hint">没有记录</td></tr>
{% endfor %}
</table>

<h2>组权限申请({{ group_requests|length }} 条,最近200条)</h2>
<table>
<tr><th>ID</th><th>申请人</th><th>组</th><th>状态</th><th>审批人</th><th>提交时间</th></tr>
{% for r in group_requests %}
<tr>
<td>{{ r.id }}</td><td>{{ r.username }}</td><td>{{ r.group_name }}</td>
<td><span class="badge badge-{{ r.status }}">{{ r.status|zh }}</span></td>
<td>{{ r.decided_by or '' }}</td><td>{{ r.requested_at|localtime }}</td>
</tr>
{% else %}
<tr><td colspan="6" class="hint">没有记录</td></tr>
{% endfor %}
</table>
<script>
// 时间一律按**浏览器自己的时区**渲染。服务端存的是 UTC ISO 串,直接印出来
// 对人是不可读的("2026-08-29T11:48:52+00:00" 要心算时差),而服务端并不
// 知道用户在哪个时区 —— 猜不如让浏览器算。
// 超过 3 天的显示绝对时间(那时"几天前"已经没有意义),3 天内显示相对时间
// ("2 小时前"),因为审批场景里"等了多久"比"哪一天提的"更要紧。
document.querySelectorAll('time.lt').forEach(function (el) {
  var raw = el.getAttribute('datetime');
  var d = new Date(raw);
  if (isNaN(d)) { return; }   // 解析不了就保持原样,不要变成 "Invalid Date"
  var diffMin = (Date.now() - d.getTime()) / 60000;
  var abs = d.toLocaleString();
  if (diffMin >= 0 && diffMin < 60 * 24 * 3) {
    var rel = diffMin < 1 ? '刚刚'
            : diffMin < 60 ? Math.floor(diffMin) + ' 分钟前'
            : diffMin < 60 * 24 ? Math.floor(diffMin / 60) + ' 小时前'
            : Math.floor(diffMin / 1440) + ' 天前';
    el.textContent = rel;
  } else {
    el.textContent = abs;
  }
  el.title = abs + '(UTC ' + raw + ')';   // 悬停仍能看到精确值
});
</script>
</body></html>
"""

TRANSFER_TEMPLATE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>权限交接</title>
<style>
  body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 700px; margin: 40px auto; padding: 0 20px; color: #222; }
  nav { margin-bottom: 20px; padding-bottom: 10px; border-bottom: 2px solid #eee; }
  nav a { margin-right: 16px; color: #555; text-decoration: none; }
  input[type=text] { padding: 6px; margin: 4px 0; width: 200px; }
  .field { margin-bottom: 12px; }
  label { display: block; font-weight: bold; margin-bottom: 2px; }
  .hint { color: #888; font-size: 0.85em; }
  .result { background: #f5f5f5; padding: 12px; border-radius: 4px; margin-top: 16px; }
</style></head><body>
<nav><a href="{{ url_for('index') }}">申请 / 待审批</a><a href="{{ url_for('audit') }}">审计</a><a class="current" href="{{ url_for('transfer') }}">权限交接</a></nav>
<h1>权限交接</h1>
<p class="hint">离职/转岗时用。参考公司现有 OA 的交接模式:一次操作把待处理的审批事项和组成员关系都转给接手人,并通知对方(见 ADR-045)。</p>
<form method="post">
  <div class="field"><label>移交人(username)</label><input type="text" name="from_user" required></div>
  <div class="field"><label>接手人(username)</label><input type="text" name="to_user" required></div>
  <button type="submit">执行交接</button>
</form>
{% if result %}<div class="result">{{ result }}</div>{% endif %}
<script>
// 时间一律按**浏览器自己的时区**渲染。服务端存的是 UTC ISO 串,直接印出来
// 对人是不可读的("2026-08-29T11:48:52+00:00" 要心算时差),而服务端并不
// 知道用户在哪个时区 —— 猜不如让浏览器算。
// 超过 3 天的显示绝对时间(那时"几天前"已经没有意义),3 天内显示相对时间
// ("2 小时前"),因为审批场景里"等了多久"比"哪一天提的"更要紧。
document.querySelectorAll('time.lt').forEach(function (el) {
  var raw = el.getAttribute('datetime');
  var d = new Date(raw);
  if (isNaN(d)) { return; }   // 解析不了就保持原样,不要变成 "Invalid Date"
  var diffMin = (Date.now() - d.getTime()) / 60000;
  var abs = d.toLocaleString();
  if (diffMin >= 0 && diffMin < 60 * 24 * 3) {
    var rel = diffMin < 1 ? '刚刚'
            : diffMin < 60 ? Math.floor(diffMin) + ' 分钟前'
            : diffMin < 60 * 24 ? Math.floor(diffMin / 60) + ' 小时前'
            : Math.floor(diffMin / 1440) + ' 天前';
    el.textContent = rel;
  } else {
    el.textContent = abs;
  }
  el.title = abs + '(UTC ' + raw + ')';   // 悬停仍能看到精确值
});
</script>
</body></html>
"""

CATALOG_TEMPLATE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>浏览目录申请表访问</title>
<style>
  body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 1100px; margin: 40px auto; padding: 0 20px; color: #222; }
  h1 { font-size: 1.4em; } h2 { font-size: 1.05em; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 0.9em; }
  th, td { border: 1px solid #ddd; padding: 7px 8px; text-align: left; vertical-align: top; }
  th { background: #fafafa; }
  nav { margin-bottom: 20px; padding-bottom: 10px; border-bottom: 2px solid #eee; }
  nav a { margin-right: 16px; color: #555; text-decoration: none; }
  nav a.current { color: #222; font-weight: bold; }
  .filters { display: flex; gap: 10px; align-items: center; margin: 12px 0; flex-wrap: wrap; }
  .filters input[type=text], .filters select { padding: 6px; }
  .lvl-badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 0.82em; }
  .lvl-1 { background: #d4edda; color: #1e6b2e; }
  .lvl-2 { background: #fff3cd; color: #7a5c00; }
  .lvl-3 { background: #f8d7da; color: #92242f; }
  .cols { color: #888; font-size: 0.82em; }
  .hint { color: #888; font-size: 0.85em; }
  .warn { background: #fff3cd; border: 1px solid #f0d585; padding: 10px 14px; border-radius: 6px; color: #7a5c00; font-size: 0.9em; }
  button { cursor: pointer; padding: 6px 16px; }
  input[type=text] { padding: 6px; }
</style></head><body>
<nav>
  <a href="{{ url_for('index') }}">申请 / 待审批</a>
  <a class="current" href="{{ url_for('table_access_catalog') }}">浏览目录申请</a>
  <a href="{{ url_for('table_access_help') }}">怎么用</a>
</nav>
<h1>浏览目录,勾选要申请的表</h1>
<p>当前登录:<b>{{ username }}</b></p>

{% if not catalog_available %}
<div class="warn">目录暂时看不到(OPENMETADATA_TOKEN 没配置,或者 OpenMetadata 连不上)。可以回<a href="{{ url_for('index') }}">申请页</a>手打完整表名提交,安全等级/负责人还是会在提交时真实校验。</div>
{% else %}
<form method="get" class="filters">
  <input type="text" name="q" value="{{ q }}" placeholder="搜表名(模糊匹配)">
  <select name="schema">
    <option value="">全部 schema</option>
    {% for s in schemas %}<option value="{{ s }}" {{ 'selected' if s == schema_filter }}>{{ s }}</option>{% endfor %}
  </select>
  <select name="security_level">
    <option value="">全部安全等级</option>
    <option value="1" {{ 'selected' if security_level_filter == '1' }}>L1</option>
    <option value="2" {{ 'selected' if security_level_filter == '2' }}>L2</option>
    <option value="3" {{ 'selected' if security_level_filter == '3' }}>L3</option>
  </select>
  <button type="submit">筛选</button>
</form>

<form method="post" action="{{ url_for('submit_table_access_batch') }}">
<table>
<tr><th></th><th>表名</th><th>Schema</th><th>安全等级</th><th>负责人</th><th>列</th></tr>
{% for t in tables %}
<tr>
<td><input type="checkbox" name="table_fqn" value="{{ t.fqn }}"></td>
<td>{{ t.fqn }}</td>
<td>{{ t.schema }}</td>
<td>{% if t.security_level %}<span class="lvl-badge lvl-{{ t.security_level }}">L{{ t.security_level }}</span>{% else %}<span class="hint">未标注</span>{% endif %}</td>
<td>{{ t.owner or '—' }}</td>
<td class="cols">{{ t.columns[:6]|join(', ') }}{% if t.columns|length > 6 %} …{% endif %}</td>
</tr>
{% else %}
<tr><td colspan="6" class="hint">目录里还没有登记过的表(先用建表注册工具登记),或者筛选条件太窄</td></tr>
{% endfor %}
</table>
<p><input type="text" name="reason" placeholder="申请理由(可选,比如只需要哪几列/哪个时间范围,写清楚方便审批人判断)" size="60"></p>
<button type="submit">申请勾选的表({{ tables|length }} 条候选)</button>
</form>
<p class="hint">安全等级/负责人以提交时 OpenMetadata 里的真实数据为准(这里显示的是查询时刻的快照,如果表刚被改过标注,提交时会重新校验)。行级/列级细粒度权限现在还只是记录申请理由,不是自动强制执行,见 <a href="{{ url_for('table_access_help') }}">怎么用这个流程</a>里的范围边界说明。</p>
{% endif %}
<script>
// 时间一律按**浏览器自己的时区**渲染。服务端存的是 UTC ISO 串,直接印出来
// 对人是不可读的("2026-08-29T11:48:52+00:00" 要心算时差),而服务端并不
// 知道用户在哪个时区 —— 猜不如让浏览器算。
// 超过 3 天的显示绝对时间(那时"几天前"已经没有意义),3 天内显示相对时间
// ("2 小时前"),因为审批场景里"等了多久"比"哪一天提的"更要紧。
document.querySelectorAll('time.lt').forEach(function (el) {
  var raw = el.getAttribute('datetime');
  var d = new Date(raw);
  if (isNaN(d)) { return; }   // 解析不了就保持原样,不要变成 "Invalid Date"
  var diffMin = (Date.now() - d.getTime()) / 60000;
  var abs = d.toLocaleString();
  if (diffMin >= 0 && diffMin < 60 * 24 * 3) {
    var rel = diffMin < 1 ? '刚刚'
            : diffMin < 60 ? Math.floor(diffMin) + ' 分钟前'
            : diffMin < 60 * 24 ? Math.floor(diffMin / 60) + ' 小时前'
            : Math.floor(diffMin / 1440) + ' 天前';
    el.textContent = rel;
  } else {
    el.textContent = abs;
  }
  el.title = abs + '(UTC ' + raw + ')';   // 悬停仍能看到精确值
});
</script>
</body></html>
"""


@app.route("/")
def index():
    username, groups = get_current_user()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    my_requests = conn.execute(
        "SELECT * FROM requests WHERE username=? ORDER BY id DESC", (username,)
    ).fetchall()
    pending = []
    approver = is_approver(groups)
    if approver:
        pending = conn.execute("SELECT * FROM requests WHERE status='pending' ORDER BY id").fetchall()

    my_table_requests_raw = conn.execute(
        "SELECT * FROM table_access_requests WHERE username=? ORDER BY id DESC", (username,)
    ).fetchall()
    my_table_requests = []
    for r in my_table_requests_raw:
        steps = conn.execute(
            "SELECT * FROM approval_steps WHERE request_id=? ORDER BY step_order, id", (r["id"],)
        ).fetchall()
        row = dict(r)
        row["steps"] = steps
        row["current_step"] = current_step_order(conn, r["id"])
        my_table_requests.append(row)

    my_actionable = conn.execute(
        """
        SELECT s.id AS step_id, s.approver_role, s.request_id, s.step_order,
               t.username, t.table_fqn, t.security_level, t.reason
        FROM approval_steps s
        JOIN table_access_requests t ON t.id = s.request_id
        WHERE s.approver_username = ?
          AND s.status = 'pending'
          AND s.step_order = (
              SELECT MIN(step_order) FROM approval_steps
              WHERE request_id = s.request_id AND status = 'pending'
          )
        ORDER BY s.request_id
        """,
        (username,),
    ).fetchall()
    conn.close()
    return render_template_string(
        TEMPLATE, username=username, available_groups=AVAILABLE_GROUPS,
        my_requests=my_requests, pending=pending, is_approver=approver,
        groups_warning=groups_diagnosis(),
        my_table_requests=my_table_requests, my_actionable=my_actionable,
        role_labels=APPROVAL_ROLE_LABELS, nudge_cooldown=NUDGE_COOLDOWN_HOURS,
    )


@app.route("/table-access/help")
def table_access_help():
    return render_template_string(HELP_TEMPLATE)


@app.route("/request", methods=["POST"])
def submit_request():
    username, _ = get_current_user()
    if not username:
        abort(401)
    group_name = request.form.get("group_name", "")
    if group_name not in AVAILABLE_GROUPS:
        abort(400)
    reason = request.form.get("reason", "")[:500]
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO requests (username, group_name, reason, requested_at) VALUES (?,?,?,?)",
        (username, group_name, reason, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/requests/<int:req_id>/approve", methods=["POST"])
def approve(req_id):
    username, groups = get_current_user()
    if not is_approver(groups):
        abort(403)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
    if not row or row["status"] != "pending":
        conn.close()
        abort(404)
    ok, note = apply_to_git(row["username"], row["group_name"])
    status = "applied" if ok else "approved_pending_apply"
    conn.execute(
        "UPDATE requests SET status=?, decided_by=?, decided_at=?, note=? WHERE id=?",
        (status, username, datetime.now(timezone.utc).isoformat(), note, req_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/requests/<int:req_id>/reject", methods=["POST"])
def reject(req_id):
    username, groups = get_current_user()
    if not is_approver(groups):
        abort(403)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE requests SET status='rejected', decided_by=?, decided_at=? WHERE id=? AND status='pending'",
        (username, datetime.now(timezone.utc).isoformat(), req_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


def create_table_access_request(conn, username: str, table_fqn: str, reason: str):
    """建一条表访问申请 + 算出并插入它的审批链,`submit_table_access`(单张,
    手打表名)和 `submit_table_access_batch`(目录页勾选多张,见 ADR-046)
    共用这一段逻辑,不重复写两遍。调用方负责 `conn.commit()`——这个函数
    只管一条申请内部的原子性(申请行 + 它的 steps 行要么都插,不存在只
    插了一半的情况),不管跨多条申请的提交时机。"""
    security_level, table_owner = lookup_table_governance(table_fqn)
    # 理由是否必填,要等查到安全等级之后才知道 —— 所以这个校验放在这里,
    # 不在路由层。返回 (ok, 提示) 让调用方决定怎么呈现:单张申请直接报错,
    # 批量申请要能说清楚"哪几张需要补理由"。
    if (security_level is not None
            and security_level >= REASON_REQUIRED_FROM_LEVEL
            and len(reason.strip()) < MIN_REASON_LENGTH):
        return (False, f"{table_fqn} 是 {security_level} 级表,必须写明申请理由"
                       f"(至少 {MIN_REASON_LENGTH} 个字)——审批人要靠它判断"
                       f"你为什么需要看这些数据")
    if security_level is None:
        conn.execute(
            "INSERT INTO table_access_requests (username, table_fqn, security_level, table_owner, reason, status, requested_at, note) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (username, table_fqn, 0, table_owner, reason, "rejected", datetime.now(timezone.utc).isoformat(),
             "在 OpenMetadata 里查不到这张表的安全等级(没登记过,或者 OPENMETADATA_TOKEN 没配置),请先用建表注册工具登记这张表"),
        )
        return (True, None)

    cur = conn.execute(
        "INSERT INTO table_access_requests (username, table_fqn, security_level, table_owner, reason, requested_at) "
        "VALUES (?,?,?,?,?,?)",
        (username, table_fqn, security_level, table_owner, reason, datetime.now(timezone.utc).isoformat()),
    )
    request_id = cur.lastrowid
    steps = build_approval_steps(username, table_owner, security_level)
    for step_order, role, approver_username in steps:
        conn.execute(
            "INSERT INTO approval_steps (request_id, step_order, approver_role, approver_username) VALUES (?,?,?,?)",
            (request_id, step_order, role, approver_username),
        )
    if not steps:
        # 组织架构里查不到申请人的任何上级、这张表也没有 owner——理论上
        # 不该发生(至少 table_owner 应该有),但真出现时不能让申请卡死
        # 在没有任何审批人的状态,直接拒绝并说明原因。
        conn.execute(
            "UPDATE table_access_requests SET status='rejected', note=? WHERE id=?",
            ("算不出任何审批人 —— 可能是申请人不在组织架构里、这张表没有负责人,"
             "或者负责人就是申请人自己(自己不能批自己)。请联系平台管理员手动处理", request_id),
        )
    elif APPROVAL_BACKEND == "oa":
        # **整单交给 OA,不逐级派发**(ADR-086)。审批链仍然算出来了,但
        # 它在这一档里只用来表达"需要几级",不用来指定谁批 —— 谁批是 OA
        # 的事。交不出去就退化成本地审批,不能让申请卡死。
        req_row = conn.execute(
            "SELECT * FROM table_access_requests WHERE id=?", (request_id,)).fetchone()
        if not dispatch_to_oa(conn, request_id, req_row):
            activate_next_step(conn, request_id)
    else:
        activate_next_step(conn, request_id)
    return (True, None)


@app.route("/table-access/request", methods=["POST"])
def submit_table_access():
    username, _ = get_current_user()
    if not username:
        abort(401)
    table_fqn = request.form.get("table_fqn", "").strip()[:300]
    reason = request.form.get("reason", "")[:500]
    if not table_fqn:
        abort(400)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ok, err = create_table_access_request(conn, username, table_fqn, reason)
    conn.commit()
    conn.close()
    if not ok:
        return {"error": err}, 400
    return redirect(url_for("index"))


@app.route("/table-access/catalog")
def table_access_catalog():
    """浏览 OpenMetadata 里已登记的表,勾选要申请哪些(ADR-046)——原来的
    "手打完整表名"那个输入框对不熟悉表名的人不友好,这个页面替代它成为
    主要入口,手打输入还留着给知道确切表名的人用,不强制走这条路。"""
    username, _ = get_current_user()
    if not username:
        abort(401)
    q = request.args.get("q", "").strip()
    schema_filter = request.args.get("schema", "").strip()
    security_level_filter = request.args.get("security_level", "").strip()
    tables = list_catalog_tables(q, schema_filter, security_level_filter)
    all_tables_unfiltered = list_catalog_tables() if (q or schema_filter or security_level_filter) else tables
    schemas = sorted({t["schema"] for t in all_tables_unfiltered if t["schema"]})
    return render_template_string(
        CATALOG_TEMPLATE, username=username, tables=tables, schemas=schemas,
        q=q, schema_filter=schema_filter, security_level_filter=security_level_filter,
        catalog_available=bool(OPENMETADATA_TOKEN),
    )


@app.route("/table-access/request-batch", methods=["POST"])
def submit_table_access_batch():
    username, _ = get_current_user()
    if not username:
        abort(401)
    table_fqns = [f.strip()[:300] for f in request.form.getlist("table_fqn") if f.strip()]
    reason = request.form.get("reason", "")[:500]
    if not table_fqns:
        return redirect(url_for("table_access_catalog"))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    errors = []
    for fqn in table_fqns:
        ok, err = create_table_access_request(conn, username, fqn, reason)
        if not ok:
            errors.append(err)
    conn.commit()
    conn.close()
    if errors:
        # **已经建成的那些照常提交**,只把需要补理由的挑出来告诉用户 ——
        # 勾了 20 张表因为其中 2 张要理由就全部作废,是很气人的设计。
        return {"error": "以下申请没有提交,需要补充理由:", "details": errors}, 400
    return redirect(url_for("index"))


@app.route("/table-access/step/<int:step_id>/approve", methods=["POST"])
def approve_table_step(step_id):
    username, _ = get_current_user()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    step = conn.execute("SELECT * FROM approval_steps WHERE id=?", (step_id,)).fetchone()
    if not step or step["status"] != "pending" or step["approver_username"] != username:
        conn.close()
        abort(403)
    if current_step_order(conn, step["request_id"]) != step["step_order"]:
        # 前面还有没审完的级别,不该轮到这一级——正常 UI 不会出现这个按钮,
        # 但直接 POST 绕过前端的话要兜底拒绝,不能让后面的级别插队先批。
        conn.close()
        abort(409)
    conn.execute(
        "UPDATE approval_steps SET status='approved', decided_at=?, comment=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(),
         request.form.get("comment", "").strip()[:500] or None, step_id),
    )
    finalize_table_request_if_done(conn, step["request_id"])
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/table-access/step/<int:step_id>/reject", methods=["POST"])
def reject_table_step(step_id):
    username, _ = get_current_user()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    step = conn.execute("SELECT * FROM approval_steps WHERE id=?", (step_id,)).fetchone()
    if not step or step["status"] != "pending" or step["approver_username"] != username:
        conn.close()
        abort(403)
    comment = request.form.get("comment", "").strip()[:500]
    if not comment:
        # **拒绝必须写原因。** 没有原因的拒绝对申请人是一堵墙:他不知道是
        # 表选错了、理由不够、还是本来就不该有这个权限,唯一能做的是原样
        # 再申请一次,然后再被拒一次。这个校验放在服务端而不是只做前端
        # required,是因为直接 POST 能绕过前端。
        conn.close()
        return {"error": "拒绝必须填写原因"}, 400
    conn.execute(
        "UPDATE approval_steps SET status='rejected', decided_at=?, comment=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), comment, step_id),
    )
    finalize_table_request_if_done(conn, step["request_id"])
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


# 催办:申请人主动提醒当前这一级的审批人(roadmap P1.5「审批体验」)。
#
# 和 ADR-045 的**超时升级**是两件事,不要混:升级是系统在人不管的时候
# 越过他往上找,催办是申请人在还没到升级阈值时说一声"我还在等"。没有催办
# 的话,申请人在这段时间里唯一能做的就是干等,或者线下去戳人 —— 而线下
# 戳人这个动作是不留痕的,后面复盘"这条为什么拖了五天"就查不到。
NUDGE_COOLDOWN_HOURS = int(os.environ.get("NUDGE_COOLDOWN_HOURS", "24"))


@app.route("/table-access/renew", methods=["POST"])
def renew_grant():
    """续期一条快到期的表权限。

    **续期不是"直接把到期时间往后推"。** 那样做等于把 180 天复审变成一次性
    的形式 —— 授权设期限的意义就在于"过一段时间要有人重新看一眼这个人还
    需不需要"。所以续期走的是**和第一次申请完全相同的审批链**。

    那它比"重新申请一遍"好在哪?好在**不用等到查不到数据才想起来**:门户
    首页会把快到期的排在最前面并标黄,点一下就发起,理由从上次那条带过来。
    到期这件事对用户本来是完全看不见的(悄悄失效、OPA 5 分钟内生效、第二天
    上班发现查不到还以为平台坏了),这个按钮解决的是**这个**问题,不是
    "少一道审批"。
    """
    username, _ = get_current_user()
    if not username:
        abort(401)
    table_fqn = request.form.get("table_fqn", "").strip()[:300]
    if not table_fqn:
        abort(400)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # 已经有一条在审的续期/申请就不要再发一条 —— 否则一个人点两下,审批人
    # 那边就出现两条一模一样的待办。
    existing = conn.execute(
        "SELECT id FROM table_access_requests WHERE username=? AND table_fqn=? "
        "AND status='pending'", (username, table_fqn)).fetchone()
    if existing:
        conn.close()
        return {"error": f"这张表已经有一条在审的申请(#{existing['id']}),不用重复提交"}, 409

    prev = conn.execute(
        "SELECT reason FROM table_access_requests WHERE username=? AND table_fqn=? "
        "ORDER BY id DESC LIMIT 1", (username, table_fqn)).fetchone()
    reason = (request.form.get("reason", "").strip()
              or (prev["reason"] if prev and prev["reason"] else "")
              or "续期:此前已获批,业务仍在使用")
    ok, err = create_table_access_request(conn, username, table_fqn, f"[续期] {reason}")
    conn.commit()
    conn.close()
    if not ok:
        return {"error": err}, 400
    return redirect(url_for("index"))


@app.route("/table-access/request/<int:req_id>/nudge", methods=["POST"])
def nudge_request(req_id):
    username, _ = get_current_user()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    req = conn.execute("SELECT * FROM table_access_requests WHERE id=?", (req_id,)).fetchone()
    if not req or req["username"] != username:
        conn.close()
        abort(403)          # 只能催自己的申请
    if req["status"] != "pending":
        conn.close()
        return {"error": "这条申请已经有结果了,不需要催办"}, 400

    now = datetime.now(timezone.utc)
    last = req["last_nudged_at"]
    if last:
        try:
            hours = (now - datetime.fromisoformat(last)).total_seconds() / 3600
            if hours < NUDGE_COOLDOWN_HOURS:
                conn.close()
                # 限频不是为了给申请人添堵,是因为一个能无限催的按钮,最后
                # 的结果是所有通知都被审批人无视 —— 那对谁都没好处。
                return {"error": f"{NUDGE_COOLDOWN_HOURS} 小时内只能催办一次,"
                                 f"还差 {NUDGE_COOLDOWN_HOURS - int(hours)} 小时"}, 429
        except ValueError:
            pass

    order = current_step_order(conn, req_id)
    step = conn.execute(
        "SELECT * FROM approval_steps WHERE request_id=? AND step_order=? "
        "AND status IN ('pending','pending_external')", (req_id, order)).fetchone()
    if not step:
        conn.close()
        return {"error": "找不到当前待审批的步骤"}, 400

    waited = ""
    if step["activated_at"]:
        try:
            h = int((now - datetime.fromisoformat(step["activated_at"])).total_seconds() // 3600)
            waited = f",已经等了 {h // 24} 天 {h % 24} 小时" if h >= 24 else f",已经等了 {h} 小时"
        except ValueError:
            pass
    notify_wecom(
        f"【催办】{req['username']} 申请 {req['table_fqn']}(L{req['security_level']})"
        f"的访问权限,等你作为{APPROVAL_ROLE_LABELS.get(step['approver_role'], step['approver_role'])}"
        f"审批{waited}。审批人:{step['approver_username']}"
    )
    conn.execute("UPDATE table_access_requests SET last_nudged_at=? WHERE id=?",
                 (now.isoformat(), req_id))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/table-access/request/<int:req_id>/oa-callback", methods=["POST"])
def oa_callback(req_id):
    """OA 审批结束,一次回调最终结果(ADR-086 的 `oa` 模式)。

    **和按步的 external-callback 的区别**:那个是"第 N 级批了",这个是
    "这张单子结束了"。OA 眼里本来就只有一张单子 —— 它内部走几级、谁批的、
    有没有代理人,平台不需要知道,也不该假装知道。

    `approvers` 是 OA 告诉平台"最终是谁批的",只用于留痕。**平台不校验
    这些人是谁** —— 校验的前提是平台有一份可信的组织架构,而那正是我们
    没有、也不该维护的东西。
    """
    if (not EXTERNAL_OA_CALLBACK_TOKEN or request.json is None
            or request.json.get("token") != EXTERNAL_OA_CALLBACK_TOKEN):
        abort(403)
    status = request.json.get("status")
    if status not in ("approved", "rejected"):
        abort(400)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    req = conn.execute("SELECT * FROM table_access_requests WHERE id=?", (req_id,)).fetchone()
    if not req or req["status"] != "pending":
        conn.close()
        abort(404)

    now = datetime.now(timezone.utc).isoformat()
    approvers = request.json.get("approvers") or []
    note = request.json.get("note") or ""
    # 把 OA 报回来的结果落到所有 step 上 —— 留痕要能看出"这单是 OA 批的、
    # 谁批的",而不是凭空变成 approved。
    conn.execute(
        "UPDATE approval_steps SET status=?, decided_at=? WHERE request_id=?",
        (status, now, req_id))
    who = ("、".join(str(a) for a in approvers)) if approvers else "OA 未提供审批人"
    oa_note = f"[OA] {who} {'批准' if status == 'approved' else '拒绝'}。{note}".strip()

    # **先 finalize 再写 note,而且是追加不是覆盖。**
    # finalize_table_request_if_done() 会把 note 设成落地结果(写 git 成功
    # 与否)。先写 OA 的归属再 finalize,那条归属会被覆盖掉 —— 2026-08-30
    # 写测试时就是这么发现的。两条信息都要留:
    #   - 谁批的(合规要回答"这个权限是谁批的")
    #   - 授权有没有真的生效(approved 和 approved_pending_apply 的区别)
    finalize_table_request_if_done(conn, req_id)
    final = conn.execute("SELECT note FROM table_access_requests WHERE id=?", (req_id,)).fetchone()
    merged = f"{oa_note} | {final['note']}" if final and final["note"] else oa_note
    conn.execute("UPDATE table_access_requests SET note=? WHERE id=?", (merged, req_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.route("/table-access/step/<int:step_id>/external-callback", methods=["POST"])
def external_callback(step_id):
    """外部 OA 系统审批完,回调这里报告结果(ADR-045 的可插拔审批后端)。
    没有真实对接目标,这次只交付协议本身——token 对不上直接拒绝,不接受
    任何声称自己是外部系统的请求。"""
    if not EXTERNAL_OA_CALLBACK_TOKEN or request.json is None or request.json.get("token") != EXTERNAL_OA_CALLBACK_TOKEN:
        abort(403)
    new_status = request.json.get("status")
    if new_status not in ("approved", "rejected"):
        abort(400)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    step = conn.execute("SELECT * FROM approval_steps WHERE id=?", (step_id,)).fetchone()
    if not step or step["status"] != "pending_external":
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE approval_steps SET status=?, decided_at=? WHERE id=?",
        (new_status, datetime.now(timezone.utc).isoformat(), step_id),
    )
    finalize_table_request_if_done(conn, step["request_id"])
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.route("/internal/escalation-check", methods=["POST"])
def escalation_check():
    """给 CronJob 调的内部端点(ADR-045),不走 oauth2-proxy/人类登录。查
    所有等了太久没人处理的 step:先提醒,等到 2 倍时长才真正升级换人审
    (不是自动通过,见 dispatch_step/ADR-045 的说明)。"""
    if not INTERNAL_TOKEN or request.headers.get("X-Internal-Token") != INTERNAL_TOKEN:
        abort(403)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    reminded, escalated = 0, 0
    rows = conn.execute(
        "SELECT * FROM approval_steps WHERE status IN ('pending', 'pending_external') AND activated_at IS NOT NULL"
    ).fetchall()
    for row in rows:
        activated = datetime.fromisoformat(row["activated_at"])
        waited_hours = (now - activated).total_seconds() / 3600
        req = conn.execute("SELECT * FROM table_access_requests WHERE id=?", (row["request_id"],)).fetchone()
        if waited_hours >= ESCALATION_HOURS * 2:
            chain = get_manager_chain(row["approver_username"], levels=1)
            escalate_to = chain[0] if chain else DESIGNATED_ADMIN
            conn.execute("UPDATE approval_steps SET status='escalated' WHERE id=?", (row["id"],))
            exists = conn.execute(
                "SELECT 1 FROM approval_steps WHERE request_id=? AND step_order=? AND approver_username=?",
                (row["request_id"], row["step_order"], escalate_to),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO approval_steps (request_id, step_order, approver_role, approver_username, activated_at) "
                    "VALUES (?,?,?,?,?)",
                    (row["request_id"], row["step_order"], "escalated", escalate_to, now.isoformat()),
                )
                notify_wecom(
                    f"审批升级:{row['approver_username']} 超过 {ESCALATION_HOURS * 2:.0f} 小时未处理"
                    f"{req['table_fqn']} 的访问申请,已转给你(原审批人的上级)处理。"
                )
            escalated += 1
        elif waited_hours >= ESCALATION_HOURS:
            notify_wecom(
                f"提醒:你有一条表访问申请等待审批已超过 {ESCALATION_HOURS:.0f} 小时:"
                f"{req['username']} 申请 {req['table_fqn']}。"
            )
            reminded += 1
    conn.commit()
    conn.close()
    return {"reminded": reminded, "escalated": escalated}


@app.route("/internal/retry-pending-applies", methods=["POST"])
def retry_pending_applies():
    """把卡在 `approved_pending_apply` 的申请重新写一次 git。

    **为什么必须有这个端点**:2026-08-29 修"审批假成功"时引入了
    `approved_pending_apply` 这个中间态(决策已定、授权写入失败)。如果只
    加状态不加重试,结果是把一个"假成功"换成一个"永远卡住",而且通知里
    还写着"平台会自动重试"——那就成了另一句谎话。

    幂等:重试成功才改状态;失败就留在原状态、更新 note,下一轮再试。
    重复跑不会重复写(apply_grant_to_git 内部是"读 csv → 没有才追加")。
    """
    if not INTERNAL_TOKEN or request.headers.get("X-Internal-Token") != INTERNAL_TOKEN:
        abort(403)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    fixed, still_failing = 0, 0

    # 表访问授权
    for row in conn.execute(
        "SELECT * FROM table_access_requests WHERE status='approved_pending_apply'"
    ).fetchall():
        ok, note = apply_grant_to_git(row["username"], row["table_fqn"], row["security_level"])
        if ok:
            conn.execute("UPDATE table_access_requests SET status='approved', note=? WHERE id=?",
                         (note, row["id"]))
            notify_wecom(f"表访问授权已补写成功,现在可以用了:{row['table_fqn']}")
            fixed += 1
        else:
            conn.execute("UPDATE table_access_requests SET note=? WHERE id=?", (note, row["id"]))
            still_failing += 1

    # 加组申请(同一个状态,同一个原因)
    for row in conn.execute(
        "SELECT * FROM requests WHERE status='approved_pending_apply'"
    ).fetchall():
        ok, note = apply_to_git(row["username"], row["group_name"])
        if ok:
            conn.execute("UPDATE requests SET status='applied', note=? WHERE id=?", (note, row["id"]))
            fixed += 1
        else:
            conn.execute("UPDATE requests SET note=? WHERE id=?", (note, row["id"]))
            still_failing += 1

    conn.commit()
    conn.close()
    return {"retried_ok": fixed, "still_failing": still_failing}


@app.route("/internal/reclaim-expired", methods=["POST"])
def reclaim_expired():
    """给 CronJob 调的内部端点(ADR-050),不走 oauth2-proxy/人类登录,同一个
    X-Internal-Token 共享密钥模式(和 /internal/escalation-check 一样)。
    扫 table-access-grants.csv,把 expires_at 已经过去的行摘掉、commit+push,
    对每个被回收的人发一条企微通知。

    **回收是真的会生效的**:`opa-grants-sync` 每 5 分钟把这个 csv 推给 OPA
    (ADR-051),所以摘掉一行之后,那个人**最多 5 分钟内**就真的查不到那张
    表了。这段注释原本写着"没有任何执行引擎在读、不产生撤销 Trino 实际权限
    的效果",那是 ADR-051 之前的状态,**2026-08-29 更正** —— 和模块顶部
    那段是同一个过期描述,当时漏改了这一处。

    这个差别很重要:它决定了回收是一个**影响线上访问的操作**,不是一次
    记录整理。"""
    if not INTERNAL_TOKEN or request.headers.get("X-Internal-Token") != INTERNAL_TOKEN:
        abort(403)
    if not GIT_TOKEN:
        return {"reclaimed": 0, "skipped": "GIT_TOKEN 未配置,无法读写 grants.csv"}

    now = datetime.now(timezone.utc)
    tmpdir = tempfile.mkdtemp()
    try:
        auth_url = REPO_URL.replace("https://", f"https://{GIT_TOKEN}@")
        subprocess.run(
            ["git", "clone", "--depth", "1", auth_url, tmpdir],
            check=True, capture_output=True, text=True, timeout=60,
        )
        csv_path = Path(tmpdir) / "platform" / "iam" / "table-access-grants.csv"
        if not csv_path.exists():
            return {"reclaimed": 0}
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))

        kept, reclaimed, expiring = [], [], []
        for row in rows:
            expires_at = (row.get("expires_at") or "").strip()
            if expires_at:
                try:
                    exp = datetime.fromisoformat(expires_at)
                    if exp <= now:
                        reclaimed.append(row)
                        continue
                    # **回收之前先提醒。** 到期这件事对用户是完全看不见的:
                    # 授权悄悄失效,OPA 5 分钟内跟着生效,人第二天来上班发现
                    # 查不到数据,第一反应是"平台坏了"而不是"我的权限到期了"。
                    # 提前几天说一声,是让他有机会续期,而不是事后来报故障。
                    # `.days` 是**向下取整**,剩 2.99 天会显示"2 天"。这是
                    # 有意保留的:对截止期限来说,少说比多说安全 —— 说"3 天"
                    # 会让人第 3 天才动手,而那时已经过期了。
                    days_left = (exp - now).days
                    if 0 <= days_left <= EXPIRY_WARN_DAYS:
                        expiring.append((row, days_left))
                except ValueError:
                    pass  # 解析不了的脏数据保守保留,不当成"已过期"误删
            kept.append(row)

        # 提醒和回收是分开的两件事:即使这一轮没有任何东西要回收,快到期的
        # 也照样要提醒 —— 所以这段放在 `if not reclaimed` 之前。
        for row, days_left in expiring:
            notify_wecom(
                f"【权限即将到期】{row.get('username')} 对 "
                f"{row.get('table_fqn')} 的访问权限还有 {days_left} 天到期"
                f"({row.get('expires_at')})。到期会自动回收,需要继续用的话"
                f"请提前重新申请。"
            )

        if not reclaimed:
            # 返回 warned 让调用方(CronJob 日志)看得出提醒真的发了几条,
            # 不然"这一轮什么都没做"和"提醒了 5 个人"在日志里长得一样。
            return {"reclaimed": 0, "warned": len(expiring)}

        header = "username,table_fqn,security_level,granted_at,expires_at"
        lines = [header] + [
            f"{r['username']},{r['table_fqn']},{r['security_level']},{r['granted_at']},{r['expires_at']}"
            for r in kept
        ]
        csv_path.write_text("\n".join(lines) + "\n")

        subprocess.run(["git", "-C", tmpdir, "config", "user.email", "permission-request-app@platform.local"], check=True)
        subprocess.run(["git", "-C", tmpdir, "config", "user.name", "Permission Request App"], check=True)
        subprocess.run(["git", "-C", tmpdir, "add", "platform/iam/table-access-grants.csv"], check=True)
        subprocess.run(
            ["git", "-C", tmpdir, "commit", "-m", f"iam: 自动回收 {len(reclaimed)} 条已过期的表访问授权(ADR-050)"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(["git", "-C", tmpdir, "push"], check=True, capture_output=True, text=True, timeout=60)

        for r in reclaimed:
            notify_wecom(f"访问权限到期回收:{r['username']} 对 {r['table_fqn']} 的访问授权已过期,记录已从 grants.csv 移除,如需继续使用请重新申请。")
        return {"reclaimed": len(reclaimed), "warned": len(expiring),
                "tables": [r["table_fqn"] for r in reclaimed]}
    except subprocess.CalledProcessError as e:
        stderr = e.stderr if hasattr(e, "stderr") else str(e)
        return {"reclaimed": 0, "error": stderr}, 500
    except subprocess.TimeoutExpired:
        return {"reclaimed": 0, "error": "git 操作超时"}, 500
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 只读 API(给门户的角色工作台用,roadmap P1.5)
#
# **为什么是服务端到服务端的接口,不是让门户直接读这个 SQLite**:数据库文件
# 在这个应用自己的 PVC 上,门户碰不到;而且"谁能看到什么"这个判断必须由
# 拥有数据的一方做 —— 门户只负责展示。
#
# **鉴权**:和 /internal/* 那几个端点同一个共享密钥模式(X-Internal-Token),
# 因为调用方是门户后端而不是人,走不了 oauth2-proxy。**调用方必须显式带上
# `user` 参数说明"我在替谁问"** —— 接口不会返回全量数据,只返回那个人自己
# 该看到的部分。这条是刻意的:一个不带用户就返回所有人权限的接口,等于给
# 门户开了一个越权读取的口子。
# ---------------------------------------------------------------------------


def _require_internal_token():
    if not INTERNAL_TOKEN or request.headers.get("X-Internal-Token") != INTERNAL_TOKEN:
        abort(403)


# git clone 一次要几秒,而 /api/my-permissions 是门户首页每次刷新都会调的。
# 60 秒的进程内缓存足够:授权变更本来就要等 opa-grants-sync 那 5 分钟才真正
# 生效,首页上晚一分钟看到没有任何实际差别。
_GRANTS_CACHE = {"rows": None, "at": 0.0}
_GRANTS_CACHE_TTL = 60


# grants.csv 的只读来源。**和 opa-grants-sync 那个 CronJob 用同一个 URL**
# (apps/opa/manifests/grants-sync-cronjob.yaml)—— 仓库是公开的,拉这个文件
# 不需要任何凭据。
#
# 2026-08-30 实测踩到:第一版只有"本地文件 → git clone(要 GIT_TOKEN)"两条
# 路,而 GIT_TOKEN 是要人手工配的、集群上根本没配 —— 于是
# `/api/my-permissions` **永远返回空 grants**,门户上「我的表权限」那一栏
# 永远不显示。而"这个人没有权限"和"读不到数据"返回的是**一模一样的空列表**,
# 从外面完全看不出区别。开机验收脚本第一次跑就抓到了这条。
GRANTS_RAW_URL = os.environ.get(
    "GRANTS_RAW_URL",
    "https://raw.githubusercontent.com/hardstuding/bigdata_ml_paltform/main/"
    "platform/iam/table-access-grants.csv")


def _read_grants_rows():
    """读 grants.csv,返回 (行列表, 来源)。

    来源取值:`local` / `raw` / `git` / `cache` / `unavailable`。
    **`unavailable` 和"读到了但这个人没有 grant"必须能被区分开** —— 这正是
    这个函数第一版栽的地方。

    读不到不抛错:门户上少一块内容,好过整页 500;但**要说出来**。
    """
    local = Path(os.environ.get("GRANTS_CSV_PATH", "/data/table-access-grants.csv"))
    if local.exists():
        with open(local, newline="") as f:
            return list(csv.DictReader(f)), "local"

    now = time.time()
    if _GRANTS_CACHE["rows"] is not None and now - _GRANTS_CACHE["at"] < _GRANTS_CACHE_TTL:
        return _GRANTS_CACHE["rows"], "cache"

    # 公开仓库的 raw 文件,不需要凭据 —— 和 opa-grants-sync 同一条路。
    try:
        req = urllib.request.Request(GRANTS_RAW_URL,
                                     headers={"User-Agent": "permission-request-app"})
        text = urllib.request.urlopen(req, timeout=8).read().decode("utf-8")
        rows = list(csv.DictReader(text.splitlines()))
        _GRANTS_CACHE.update(rows=rows, at=now)
        return rows, "raw"
    except Exception:
        pass

    if not GIT_TOKEN:
        return (_GRANTS_CACHE["rows"] or []), (
            "cache" if _GRANTS_CACHE["rows"] else "unavailable")
    tmpdir = tempfile.mkdtemp()
    try:
        auth_url = REPO_URL.replace("https://", f"https://{GIT_TOKEN}@")
        subprocess.run(["git", "clone", "--depth", "1", auth_url, tmpdir],
                       check=True, capture_output=True, text=True, timeout=60)
        csv_path = Path(tmpdir) / "platform" / "iam" / "table-access-grants.csv"
        if not csv_path.exists():
            return []
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        _GRANTS_CACHE.update(rows=rows, at=now)
        return rows, "git"
    except Exception:
        # 缓存里有旧数据就先用旧的 —— 一次抖动不该让首页上"我的权限"整块消失。
        return (_GRANTS_CACHE["rows"] or []), (
            "cache" if _GRANTS_CACHE["rows"] else "unavailable")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.route("/api/table-governance")
def api_table_governance():
    """一张表的治理属性 —— **给 OA(以及别的外部系统)读的那层隔离**。

    **为什么不让 OA 直接调 OpenMetadata**(ADR-086):OM 的实体结构会随
    版本变,我们 2026-08-26 刚跨过 1.13.3 → 2.0.0 一个大版本。如果 OA 直接
    耦合到 OM 的 API,**每一次 OM 升级都可能打破 OA 的集成** —— 而 OA 通常
    不是我们能随时改的系统,修复周期以周计。

    这个接口的**唯一职责就是隔离**:表名进,治理属性出。契约小到几乎不会
    需要改;OM 怎么变、字段叫什么、认证怎么做,都关在平台这边。

    **不需要 X-Internal-Token** —— 它返回的是"这张表几级、负责人是谁",
    是数据目录里本来就公开的治理元数据,不是数据本身,也不涉及任何人的
    权限。加一道 token 只会让对接方多一件事要配。
    """
    table_fqn = request.args.get("table", "").strip()
    if not table_fqn:
        return {"error": "必须带 table 参数(表名,比如 iceberg.demo.orders 或 trino.iceberg.demo.orders,两种都认)"}, 400
    security_level, table_owner = lookup_table_governance(table_fqn)
    if security_level is None:
        return {
            "table_fqn": table_fqn,
            "known": False,
            # **说清楚是"目录里没有"而不是"这张表不存在"** —— 直接在 Trino
            # 里手写 DDL 建的表就是这种状态,它在 Trino 里是存在的。
            "reason": "数据目录里查不到这张表的安全等级 —— 它可能是直接在 "
                      "Trino 里建的(没走建表注册工具),或者元数据采集还没跑到",
        }, 404
    return {
        "table_fqn": table_fqn,
        "known": True,
        "security_level": security_level,
        "table_owner": table_owner or "",
        "required_approval": approval_policy(security_level, table_owner),
    }


@app.route("/api/my-permissions")
def api_my_permissions():
    """某个人现在有哪些表权限,以及哪些快到期了。

    到期这件事对用户是**看不见的**:授权默认 180 天,过期会被自动回收
    (ADR-050),而 OPA 5 分钟内就跟着生效 —— 也就是说人会在毫无预警的
    情况下突然查不到数据。把"还有几天到期"摆到首页,就是为了这个。
    """
    _require_internal_token()
    user = request.args.get("user", "").strip()
    if not user:
        return {"error": "必须带 user 参数"}, 400
    try:
        soon_days = int(request.args.get("soon_days", "30"))
    except ValueError:
        soon_days = 30

    now = datetime.now(timezone.utc)
    rows, source = _read_grants_rows()
    grants = []
    for row in rows:
        if (row.get("username") or "").strip() != user:
            continue
        expires_at = (row.get("expires_at") or "").strip()
        days_left = None
        if expires_at:
            try:
                days_left = (datetime.fromisoformat(expires_at) - now).days
            except ValueError:
                days_left = None
        grants.append({
            "table": (row.get("table_fqn") or "").strip(),
            "security_level": (row.get("security_level") or "").strip(),
            "expires_at": expires_at,
            "days_left": days_left,
            "expiring_soon": days_left is not None and 0 <= days_left <= soon_days,
        })
    grants.sort(key=lambda g: (g["days_left"] is None, g["days_left"]))
    return {
        "user": user,
        "grants": grants,
        "expiring_soon": [g for g in grants if g["expiring_soon"]],
        # **调用方必须能区分"这个人没有 grant"和"我读不到 grants"** ——
        # 两者返回的 grants 都是空列表,而含义完全相反。
        "source": source,
        "available": source != "unavailable",
    }


@app.route("/api/my-approvals")
def api_my_approvals():
    """等着某个人审批的事项,以及每一条已经等了多久。

    「等了多久」不是装饰:ADR-045 的超时升级机制会把长期没人管的步骤往上
    升级,而被升级之前那段时间,申请人是干等着的。审批人自己看得到"这条
    等了 3 天",比等系统替他升级要好。
    """
    _require_internal_token()
    user = request.args.get("user", "").strip()
    if not user:
        return {"error": "必须带 user 参数"}, 400

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT s.id AS step_id, s.request_id, s.approver_role, s.activated_at,
               r.username AS applicant, r.table_fqn, r.security_level, r.reason
        FROM approval_steps s
        JOIN table_access_requests r ON r.id = s.request_id
        WHERE s.approver_username = ? AND s.status = 'pending'
          AND r.status = 'pending'
        ORDER BY s.id
        """,
        (user,),
    ).fetchall()
    conn.close()

    now = datetime.now(timezone.utc)
    pending = []
    for r in rows:
        waiting_hours = None
        if r["activated_at"]:
            try:
                waiting_hours = int(
                    (now - datetime.fromisoformat(r["activated_at"])).total_seconds() // 3600)
            except ValueError:
                waiting_hours = None
        pending.append({
            "step_id": r["step_id"],
            "request_id": r["request_id"],
            "applicant": r["applicant"],
            "table": r["table_fqn"],
            "security_level": r["security_level"],
            "reason": r["reason"],
            "role": APPROVAL_ROLE_LABELS.get(r["approver_role"], r["approver_role"]),
            "waiting_hours": waiting_hours,
            "overdue": waiting_hours is not None and waiting_hours >= 48,
        })
    return {"user": user, "pending": pending,
            "overdue": [p for p in pending if p["overdue"]]}


@app.route("/audit")
def audit():
    username, groups = get_current_user()
    if not is_approver(groups):
        abort(403)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    group_requests = conn.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 200").fetchall()
    table_requests_raw = conn.execute(
        "SELECT * FROM table_access_requests ORDER BY id DESC LIMIT 200"
    ).fetchall()
    table_requests = []
    for r in table_requests_raw:
        steps = conn.execute(
            "SELECT * FROM approval_steps WHERE request_id=? ORDER BY step_order, id", (r["id"],)
        ).fetchall()
        row = dict(r)
        row["steps"] = steps
        table_requests.append(row)
    conn.close()
    return render_template_string(
        AUDIT_TEMPLATE, username=username, group_requests=group_requests,
        table_requests=table_requests, role_labels=APPROVAL_ROLE_LABELS,
    )


@app.route("/admin/transfer", methods=["GET", "POST"])
def transfer():
    username, groups = get_current_user()
    if not is_approver(groups):
        abort(403)
    if request.method == "GET":
        return render_template_string(TRANSFER_TEMPLATE, username=username, result=None)

    from_user = request.form.get("from_user", "").strip()
    to_user = request.form.get("to_user", "").strip()
    if not from_user or not to_user or from_user == to_user:
        abort(400)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    pending_steps = conn.execute(
        "SELECT id, request_id FROM approval_steps WHERE approver_username=? AND status IN ('pending','pending_external')",
        (from_user,),
    ).fetchall()
    conn.execute(
        "UPDATE approval_steps SET approver_username=? WHERE approver_username=? AND status IN ('pending','pending_external')",
        (to_user, from_user),
    )
    conn.commit()

    memberships_note = "没有配置 GIT_TOKEN,组成员关系没有自动转移,需要人工把 platform/iam/memberships.csv 里的相关行手动处理"
    if GIT_TOKEN:
        tmpdir = tempfile.mkdtemp()
        try:
            auth_url = REPO_URL.replace("https://", f"https://{GIT_TOKEN}@")
            subprocess.run(["git", "clone", "--depth", "1", auth_url, tmpdir], check=True, capture_output=True, text=True, timeout=60)
            csv_path = Path(tmpdir) / "platform" / "iam" / "memberships.csv"
            lines = [l for l in csv_path.read_text().splitlines() if l.strip()]
            header, rows_csv = lines[0], lines[1:]
            from_groups = {l.split(",")[1] for l in rows_csv if l.split(",")[0] == from_user}
            to_groups = {l.split(",")[1] for l in rows_csv if l.split(",")[0] == to_user}
            new_rows = [f"{to_user},{g}" for g in (from_groups - to_groups)]
            if new_rows:
                lines = [header] + rows_csv + new_rows
                csv_path.write_text("\n".join(lines) + "\n")
                subprocess.run(["git", "-C", tmpdir, "config", "user.email", "permission-request-app@platform.local"], check=True)
                subprocess.run(["git", "-C", tmpdir, "config", "user.name", "Permission Request App"], check=True)
                subprocess.run(["git", "-C", tmpdir, "add", "platform/iam/memberships.csv"], check=True)
                subprocess.run(["git", "-C", tmpdir, "commit", "-m", f"iam: {from_user} 权限交接给 {to_user}"], check=True, capture_output=True, text=True)
                subprocess.run(["git", "-C", tmpdir, "push"], check=True, capture_output=True, text=True, timeout=60)
                memberships_note = f"已把 {from_user} 的组成员关系({', '.join(sorted(from_groups - to_groups)) or '无新增'})转移给 {to_user},提交进 git"
            else:
                memberships_note = f"{to_user} 已经拥有 {from_user} 的全部组成员关系,不用改 memberships.csv"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            memberships_note = f"组成员关系转移失败,请人工处理:{e}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    conn.close()
    result = f"已把 {len(pending_steps)} 条待审批表访问申请从 {from_user} 转给 {to_user}。{memberships_note}"
    notify_wecom(f"权限交接:{from_user} 的待处理事项已转交给你(操作人:{username})。{result}")
    return render_template_string(TRANSFER_TEMPLATE, username=username, result=result)


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
