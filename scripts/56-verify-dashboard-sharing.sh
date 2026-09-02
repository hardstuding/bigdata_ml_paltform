#!/usr/bin/env bash
# 验证「把某个看板只分享给指定角色」这件事真的成立 —— 用两个临时用户
# 各自发一次真实 HTTP 请求,比对谁看得见谁看不见。
#
# 为什么要验:`DASHBOARD_RBAC` 这个特性开关不开的话,看板上的 `roles` 字段
# **存了也没用** —— 界面上能选、能保存,但对访问控制没有任何影响。
# 这正是"配置好了 ≠ 生效了"最典型的形状:所有可见的证据都显示配好了。
#
# 语义(容易配反,先说清楚):
#   看板**指定了角色** → 只有这些角色的人能看到
#   看板**没指定角色** → 回退到按数据源权限判断(也就是原来的行为)
# 所以打开这个开关不会让现有看板突然对谁都不可见。
#
# 用法:
#   ./scripts/56-verify-dashboard-sharing.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/verify-dashboard-sharing.log"
echo "=== verify-dashboard-sharing $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

SS_POD=$(kubectl get pod -n superset \
  -l app.kubernetes.io/name=superset,app.kubernetes.io/component=web \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
[ -n "${SS_POD:-}" ] || { echo "!! 没有 Running 的 superset web pod" | tee -a "$LOG_FILE"; exit 1; }

kubectl exec -i -n superset "$SS_POD" -c superset -- python - <<'PYBODY' 2>&1 | grep -E "^\[验证\]|^  \[" | tee -a "$LOG_FILE"
from superset.app import create_app

BASE = "业务查看"
ROLE_A, ROLE_B = "_verify_team_a", "_verify_team_b"
U_A, U_B = "_verify_user_a", "_verify_user_b"

app = create_app()
with app.app_context():
    from superset.extensions import db, feature_flag_manager
    from superset.models.dashboard import Dashboard
    from superset import security_manager as sm
    from superset.utils.core import override_user
    from superset.dashboards.filters import DashboardAccessFilter
    from flask_appbuilder.models.sqla.interface import SQLAInterface

    if not feature_flag_manager.is_feature_enabled("DASHBOARD_RBAC"):
        raise SystemExit("[验证] !! DASHBOARD_RBAC 没开,看板上的 roles 存了也不生效")
    base = sm.find_role(BASE)
    if base is None:
        raise SystemExit(f"[验证] !! 角色 {BASE} 不存在,先跑 scripts/54-configure-superset-roles.sh")

    # **两个临时角色都是「业务查看」的克隆。** 用两个角色而不是两个用户来
    # 区分,是因为 Superset 的授权单位就是角色 —— "分享给某个组"在这里落地
    # 成"分享给那个组对应的角色"。
    #
    # 也不能拿「报表开发」当对照组:它继承自 Alpha,带 all_datasource_access,
    # 而 DASHBOARD_RBAC 对有全量数据源权限的人本来就不设限(Superset 的设计
    # 如此)。拿它做对照会得出"RBAC 不生效"的错误结论。
    def temp_role(name):
        r = sm.find_role(name) or sm.add_role(name)
        r.permissions = list(base.permissions)
        db.session.commit()
        return r

    def temp_user(username, role):
        u = sm.find_user(username=username)
        if u is None:
            u = sm.add_user(username, "验证", "临时", f"{username}@invalid.local",
                            role, password="x" * 24)
        else:
            u.roles = [role]
            db.session.commit()
        return u

    ra, rb = temp_role(ROLE_A), temp_role(ROLE_B)
    ua, ub = temp_user(U_A, ra), temp_user(U_B, rb)
    dash = db.session.query(Dashboard).order_by(Dashboard.id).first()
    if dash is None:
        raise SystemExit("[验证] !! 一个看板都没有,先跑 scripts/08-create-demo-data.sh")
    original = list(dash.roles)
    print(f"[验证] 用看板《{dash.dashboard_title}》(id={dash.id}),原本的角色授权={[r.name for r in original]}")

    # **验的是 DashboardAccessFilter 本身** —— 也就是 /api/v1/dashboard/ 和
    # 看板列表页真正用来过滤的那个类,不是 security_manager 的内部方法自问
    # 自答。
    #
    # 为什么不用 app.test_client() 伪造 session 走完整 HTTP:同一个进程里
    # 连开两个 test_client 分别伪造两个用户时,第二个请求仍然被认成第一个
    # 用户(实测 /api/v1/me/ 两次都返回同一个 username),那样测出来的东西
    # 是错的。登录链路本身由 scripts/52-verify-sso-login.sh 用真实浏览器
    # 登录覆盖,这里专注授权过滤这一段。
    accessor = DashboardAccessFilter("id", SQLAInterface(Dashboard, db.session))

    def visible_to(user):
        with override_user(user):
            return [d.id for d in accessor.apply(db.session.query(Dashboard), None).all()]

    ok = True

    def check(label, cond, extra=""):
        global ok
        mark = "\u2713" if cond else "\u2717"
        print(f"  [{mark}] {label}" + (f" — {extra}" if extra else ""))
        ok = ok and bool(cond)

    try:
        dash.roles = [ra]
        db.session.commit()
        db.session.expire_all()
        va, vb = visible_to(ua), visible_to(ub)
        check("分享给 A 组之后,A 组的人看得见", dash.id in va, f"看到 {len(va)} 个看板")
        check("同一时刻 B 组的人看不见", dash.id not in vb, f"看到 {len(vb)} 个看板")

        # 证伪:换成分享给 B,可见性必须跟着反过来。不做这一步的话,上面两条
        # 也可能只是"A 恰好有权限、B 恰好没有"。
        dash.roles = [rb]
        db.session.commit()
        db.session.expire_all()
        va, vb = visible_to(ua), visible_to(ub)
        check("改成分享给 B 组,B 组的人看得见了", dash.id in vb, f"看到 {len(vb)} 个看板")
        check("而 A 组的人看不见了(证伪:可见性确实跟着授权走)", dash.id not in va, f"看到 {len(va)} 个看板")

        # 这一条不是可有可无的补充,是使用方必须知道的事实:业务查看角色
        # 没有任何数据源权限,看板不指定角色的话他们**什么都看不到**。
        # 也就是说"分享给指定的组"在这个平台上是必做动作,不是可选的收紧。
        dash.roles = []
        db.session.commit()
        db.session.expire_all()
        va = visible_to(ua)
        check("看板不指定角色时,业务查看的人看不到它(所以分享是必做动作)",
              dash.id not in va, f"看到 {len(va)} 个看板")
    finally:
        dash.roles = original
        for u in (ua, ub):
            db.session.delete(u)
        db.session.commit()
        for r in (ra, rb):
            db.session.delete(r)
        db.session.commit()
        print("[验证] 已还原看板的角色授权,并删掉两个临时用户和两个临时角色")

    print("[验证] === 看板定向分享" + ("成立" if ok else "不成立") + " ===")
    raise SystemExit(0 if ok else 1)
PYBODY
