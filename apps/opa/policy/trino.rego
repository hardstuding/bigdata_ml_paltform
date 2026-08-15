package trino

# Trino OPA 访问控制策略(ADR-051)。范围边界:这只覆盖"能不能读到某张表的
# 数据"这一层(SelectFromColumns/ShowCreateTable),按 platform/iam/
# table-access-grants.csv 里的授权记录判断;不覆盖行级/列级更细粒度的过滤
# 脱敏(row-filters/column-masking,ADR-028 提到的后续能力,这次没做)。
#
# 默认拒绝(default allow := false)——这是这个项目第一次给 Trino 加访问
# 控制,之前是完全没有限制的(AllowAllAccessControl)。这个策略上线是一次
# 真实的行为收紧,不是"反正本来就这样,加个形式":之前能查的表,上线后
# 没有对应 grant 记录就查不到了,包括 Superset 之类已经在用的数据源表。
# 见 ADR-051"上线前必须先做的事"。

default allow := false

# ---- 建表工具的服务账号:不走审批链约束 ----
# table_registration_service 是 apps/table-registration-app 用来建表/管理
# schema 的专属账号(platform/iam/... 之外单独维护,见
# scripts/00-generate-secrets.sh 的 trino-service-account),不是终端用户,
# 给它完整权限,不受这份策略后面那些针对人类用户的限制约束。
is_service_account if {
	input.context.identity.user == "table_registration_service"
}

allow if {
	is_service_account
}

# ---- 平台管理组:不受表级授权约束,方便排障 ----
# 和 ArgoCD(ADR-028)platform-team -> role:admin 是同一个权限模型,不是
# 这次新发明的口子。
is_platform_admin if {
	"platform-team" in input.context.identity.groups
}

allow if {
	is_platform_admin
}

# ---- 基础浏览操作:任何登录用户都放行 ----
# 能看到 catalog/schema/table/column 存在,不等于能读到里面的数据——这个
# 项目的表目录浏览(建表注册工具 ADR-043、权限申请门户的表访问分级审批
# ADR-046)本来就是"任何登录用户都能看目录、决定要不要申请"的模式,这里
# 保持一致,不在浏览这一层加限制,只在真正读数据这一层(SelectFromColumns/
# ShowCreateTable)卡审批记录。
basic_browse_operations := {
	"ExecuteQuery",
	"AccessCatalog",
	"ShowSchemas",
	"ShowTables",
	"ShowColumns",
	"ShowCreateSchema",
	"FilterCatalogs",
	"FilterSchemas",
	"FilterTables",
	"FilterColumns",
}

allow if {
	input.action.operation in basic_browse_operations
}

# ---- 真正读数据:必须有有效(未过期)的 grant 记录 ----
gated_operations := {"SelectFromColumns", "ShowCreateTable"}

allow if {
	input.action.operation in gated_operations
	table_fqn := sprintf("%s.%s", [input.action.resource.table.schemaName, input.action.resource.table.tableName])
	has_valid_grant(input.context.identity.user, table_fqn)
}

has_valid_grant(user, table_fqn) if {
	some g in data.trino.grants
	g.username == user
	g.table_fqn == table_fqn
	not grant_expired(g)
}

grant_expired(g) if {
	g.expires_at != ""
	time.parse_rfc3339_ns(g.expires_at) < time.now_ns()
}

# 没写明确覆盖的操作(CreateTable/DropTable/InsertIntoTable/DeleteFromTable/
# AlterColumn/CreateView/... 等所有写类操作),默认拒绝——这是有意的设计,
# 不是漏写:这个平台的建表/改表走 apps/table-registration-app 这个专属
# 工具(用上面的 is_service_account 账号),不是让终端用户在 Trino 里直接
# 执行 DDL/DML。如果以后真的需要放开某类写操作给终端用户,应该是一次
# 独立、有讨论的决定,不是在这份策略里悄悄漏放。
