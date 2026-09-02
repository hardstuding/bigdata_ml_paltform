#!/usr/bin/env bash
# 建 Superset 的两个业务角色:「业务查看」和「报表开发」。
#
# **为什么内置角色不够用。** Superset 自带 Admin/Alpha/Gamma/sql_lab,
# 直觉上 Gamma 就是"只读用户" —— 实测不是。2026-09-02 逐条比对:
#
#   Gamma  92 条权限,其中 13 条是写权限,**含 can_write|Chart 和
#          can_write|Dashboard** —— 也就是 Gamma 能建图表、能建看板。
#   Alpha 114 条,比 Gamma 多的主要是 can_write|Dataset、
#          can_write|ReportSchedule(告警)、CssTemplate、Annotation。
#
# 所以"业务方只能看"这件事,**用内置角色是配不出来的**。这正是使用方
# 2026-09-02 抽查时问的:"一般是两个角色,业务角色仅查看,开发角色可以
# 开发报表、查 SQL,现在是怎么个授权法?" —— 当时的答案是 viewers 映射到
# Gamma,而 Gamma 会写,等于没有"仅查看"这一档。
#
# **两个角色怎么定义的**:
#
#   业务查看   = Gamma 减去"能产出内容"的写权限
#   报表开发   = Alpha ∪ sql_lab
#
# **减法要留一批 can_write,这是最容易配错的地方。** 看板上的筛选器、
# "复制链接"、Explore 的临时表单状态,在 Superset 里都是走 REST API 的
# **写**接口存的(DashboardFilterStateRestApi / DashboardPermalinkRestApi /
# ExploreFormDataRestApi / ExplorePermalinkRestApi / CurrentUserRestApi)。
# 一刀切掉所有 can_write 的话,角色看起来配好了,实际表现是**看板筛选器
# 一动就报错** —— 而这个错和权限配置看不出关系,排查会绕很远。
#
# 幂等:角色已存在就按下面的定义重算一遍权限(多的收回、少的补上),
# 不会重复创建,也不会动用户和角色的绑定关系。
#
# 前置:Superset 正在运行。
#
# 用法:
#   ./scripts/54-configure-superset-roles.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/configure-superset-roles.log"
echo "=== configure-superset-roles $(date -u +%FT%TZ) ===" >> "$LOG_FILE"

# **选择器必须带 component=web。** 2026-09-02 打开 Celery worker/beat
# 之后,`-l app.kubernetes.io/name=superset` 会同时匹配到 worker 和 beat,
# 而那两个 pod 里的容器名不是 `superset`,`kubectl exec` 直接报
# "container superset is not valid for pod ..."。加上 Running 过滤是因为
# 滚动更新期间会有 Terminating 的旧 pod 排在前面。
SUPERSET_POD=$(kubectl get pod -n superset \
  -l app.kubernetes.io/name=superset,app.kubernetes.io/component=web \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
if [ -z "${SUPERSET_POD:-}" ]; then
  echo "!! 找不到 Running 的 superset pod" | tee -a "$LOG_FILE"; exit 1
fi
echo "--> 用 pod $SUPERSET_POD" | tee -a "$LOG_FILE"

kubectl exec -i -n superset "$SUPERSET_POD" -c superset -- python - <<'PY' 2>&1 | tee -a "$LOG_FILE"
from superset.app import create_app

# 从 Gamma 拿掉的写权限:这些是"产出内容"的能力,业务查看角色不该有。
VIEWER_DROP = {
    ("can_write", "Chart"),
    ("can_write", "Dashboard"),
    ("can_delete_embedded", "Dashboard"),
    ("can_write", "Tag"),
    ("can_write", "Theme"),
    ("can_add", "UserRegistrationsRestAPI"),
    ("can_edit", "UserRegistrationsRestAPI"),
    ("can_delete", "UserRegistrationsRestAPI"),
}

app = create_app()
with app.app_context():
    from superset import security_manager as sm
    from superset.extensions import db

    def perms_of(name):
        r = sm.find_role(name)
        if r is None:
            raise SystemExit(f"!! 内置角色 {name} 不存在,Superset 初始化没跑完?")
        return set(r.permissions)

    gamma, alpha, sqllab = perms_of("Gamma"), perms_of("Alpha"), perms_of("sql_lab")

    targets = {
        "业务查看": {p for p in gamma
                     if (p.permission.name, p.view_menu.name) not in VIEWER_DROP},
        "报表开发": alpha | sqllab,
    }

    for role_name, want in targets.items():
        role = sm.find_role(role_name) or sm.add_role(role_name)
        have = set(role.permissions)
        added, removed = want - have, have - want
        role.permissions = list(want)
        db.session.commit()
        print(f"[{role_name}] 共 {len(want)} 条权限 (+{len(added)} / -{len(removed)})")
        # 把关键的几条打出来,让人一眼看得出这两个角色的实质差别
        def has(perm, view):
            return any(p.permission.name == perm and p.view_menu.name == view for p in want)
        print(f"    建图表 can_write|Chart      : {has('can_write','Chart')}")
        print(f"    建看板 can_write|Dashboard  : {has('can_write','Dashboard')}")
        print(f"    建告警 can_write|ReportSchedule: {has('can_write','ReportSchedule')}")
        print(f"    SQL Lab menu_access         : {has('menu_access','SQL Lab')}")
        print(f"    看板筛选器 can_write|DashboardFilterStateRestApi: "
              f"{has('can_write','DashboardFilterStateRestApi')}")
PY
echo "--> 完成,日志在 $LOG_FILE"
