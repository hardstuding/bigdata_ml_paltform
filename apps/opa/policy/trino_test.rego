package trino_test

import data.trino

mock_grants := [
	{"username": "analyst001", "table_fqn": "demo.access_test_l1", "security_level": 1, "granted_at": "2026-08-14T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00"},
	{"username": "analyst001", "table_fqn": "demo.expired_table", "security_level": 1, "granted_at": "2020-01-01T00:00:00+00:00", "expires_at": "2020-06-01T00:00:00+00:00"},
]

test_service_account_allowed_anything if {
	trino.allow with input as {"context": {"identity": {"user": "table_registration_service", "groups": []}}, "action": {"operation": "CreateTable"}}
}

test_superset_service_account_allowed_select_without_grant if {
	trino.allow with input as {
		"context": {"identity": {"user": "superset_service", "groups": []}},
		"action": {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "demo", "tableName": "orders"}}},
	}
}

test_dbt_demo_service_account_allowed_create_table if {
	trino.allow with input as {"context": {"identity": {"user": "dbt_demo_service", "groups": []}}, "action": {"operation": "CreateTable"}}
}

test_platform_sdk_demo_service_account_allowed_select_without_grant if {
	trino.allow with input as {
		"context": {"identity": {"user": "platform_sdk_demo_service", "groups": []}},
		"action": {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "demo", "tableName": "orders"}}},
	}
}

test_platform_admin_allowed_select_without_grant if {
	trino.allow with input as {
		"context": {"identity": {"user": "admin", "groups": ["platform-team"]}},
		"action": {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "demo", "tableName": "no_grant_table"}}},
	}
}

test_anyone_can_browse if {
	trino.allow with input as {
		"context": {"identity": {"user": "analyst001", "groups": ["data-analysts"]}},
		"action": {"operation": "ShowTables", "resource": {"schema": {"schemaName": "demo"}}},
	}
}

test_select_allowed_with_valid_grant if {
	trino.allow with input as {
		"context": {"identity": {"user": "analyst001", "groups": ["data-analysts"]}},
		"action": {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "demo", "tableName": "access_test_l1"}}},
	}
		with data.trino.grants as mock_grants
}

test_select_denied_without_grant if {
	not trino.allow with input as {
		"context": {"identity": {"user": "analyst001", "groups": ["data-analysts"]}},
		"action": {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "demo", "tableName": "some_other_table"}}},
	}
		with data.trino.grants as mock_grants
}

test_select_denied_with_expired_grant if {
	not trino.allow with input as {
		"context": {"identity": {"user": "analyst001", "groups": ["data-analysts"]}},
		"action": {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "demo", "tableName": "expired_table"}}},
	}
		with data.trino.grants as mock_grants
}

test_select_denied_for_different_user if {
	not trino.allow with input as {
		"context": {"identity": {"user": "algo001", "groups": ["algorithm-team"]}},
		"action": {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "demo", "tableName": "access_test_l1"}}},
	}
		with data.trino.grants as mock_grants
}

test_show_create_table_gated_same_as_select if {
	not trino.allow with input as {
		"context": {"identity": {"user": "analyst001", "groups": ["data-analysts"]}},
		"action": {"operation": "ShowCreateTable", "resource": {"table": {"schemaName": "demo", "tableName": "some_other_table"}}},
	}
		with data.trino.grants as mock_grants
}

test_ddl_denied_for_regular_user if {
	not trino.allow with input as {
		"context": {"identity": {"user": "analyst001", "groups": ["data-analysts"]}},
		"action": {"operation": "CreateTable", "resource": {"table": {"schemaName": "demo", "tableName": "new_table"}}},
	}
}

test_insert_denied_for_regular_user_even_with_select_grant if {
	not trino.allow with input as {
		"context": {"identity": {"user": "analyst001", "groups": ["data-analysts"]}},
		"action": {"operation": "InsertIntoTable", "resource": {"table": {"schemaName": "demo", "tableName": "access_test_l1"}}},
	}
		with data.trino.grants as mock_grants
}

# ---------------------------------------------------------------- 审计表
# 审计表记着每个人查过什么、导出过什么,是一份"谁对什么感兴趣"的完整
# 画像,泄露危害比业务表大。下面几条锁住"服务账号那条无条件放行的口子
# 对审计表不生效",别在以后重构 allow 规则时被顺手改回去。

test_service_account_denied_on_audit_schema if {
	not trino.allow with input as {
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {"catalogName": "iceberg", "schemaName": "audit", "tableName": "query_events"}},
		},
		"context": {"identity": {"user": "superset_service", "groups": []}},
	}
}

test_service_account_still_allowed_on_normal_table if {
	trino.allow with input as {
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {"catalogName": "iceberg", "schemaName": "demo", "tableName": "orders"}},
		},
		"context": {"identity": {"user": "superset_service", "groups": []}},
	}
}

test_platform_admin_allowed_on_audit_schema if {
	trino.allow with input as {
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {"catalogName": "iceberg", "schemaName": "audit", "tableName": "query_events"}},
		},
		"context": {"identity": {"user": "zhenghe", "groups": ["platform-team"]}},
	}
}

test_regular_user_denied_on_audit_schema if {
	not trino.allow with input as {
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {"catalogName": "iceberg", "schemaName": "audit", "tableName": "query_events"}},
		},
		"context": {"identity": {"user": "analyst", "groups": ["data-analysts"]}},
	}
}

test_new_audit_table_protected_without_policy_change if {
	not trino.allow with input as {
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {"catalogName": "iceberg", "schemaName": "audit", "tableName": "query_table_access"}},
		},
		"context": {"identity": {"user": "dbt_demo_service", "groups": []}},
	}
}

test_openmetadata_service_allowed_on_audit_schema_metadata_only if {
	# 唯一被放行到审计表的服务账号:它采的是元数据不是数据(采集配置里
	# 没开 profiler/sample data)。挡住它的后果是审计表永远不出现在数据
	# 目录里,数据治理角色连该查什么都不知道。
	trino.allow with input as {
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {"catalogName": "iceberg", "schemaName": "audit", "tableName": "query_events"}},
		},
		"context": {"identity": {"user": "openmetadata_service", "groups": []}},
	}
}

test_other_service_accounts_still_denied_on_audit_schema if {
	# 上面那个口子是**只给 openmetadata_service 一个**,别顺手扩大成
	# "所有服务账号都行"。
	not trino.allow with input as {
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {"catalogName": "iceberg", "schemaName": "audit", "tableName": "query_events"}},
		},
		"context": {"identity": {"user": "platform_sdk_demo_service", "groups": []}},
	}
}

# ---------------------------------------------------------- 身份代理
# 2026-08-26 之前 Superset 用共享服务账号连 Trino,导致"任何能在 Superset
# 建查询的人都能读任何表、而且不脱敏"。打开 impersonation 之后 Trino 看到
# 的是真实的人。下面几条锁住这个开关的边界。

test_superset_service_can_impersonate if {
	trino.allow with input as {
		"action": {"operation": "ImpersonateUser", "resource": {"user": {"user": "analyst001"}}},
		"context": {"identity": {"user": "superset_service", "groups": []}},
	}
}

test_other_service_accounts_cannot_impersonate if {
	# 代理别人是很强的权限,只给真正需要的那一个账号。
	not trino.allow with input as {
		"action": {"operation": "ImpersonateUser", "resource": {"user": {"user": "analyst001"}}},
		"context": {"identity": {"user": "dbt_demo_service", "groups": []}},
	}
}

test_regular_user_cannot_impersonate if {
	not trino.allow with input as {
		"action": {"operation": "ImpersonateUser", "resource": {"user": {"user": "zhenghe"}}},
		"context": {"identity": {"user": "analyst001", "groups": ["data-analysts"]}},
	}
}

test_impersonated_user_still_gets_masked if {
	# 代理生效之后,Trino 看到的是真实用户,列级脱敏就该对他生效了——
	# 这正是打开 impersonation 的意义。
	mask := trino.columnMask with input as {
		"action": {
			"operation": "GetColumnMask",
			"resource": {"column": {"catalogName": "iceberg", "schemaName": "demo", "tableName": "access_test_l1", "columnName": "phone", "columnType": "varchar"}},
		},
		"context": {"identity": {"user": "analyst001", "groups": ["data-analysts"]}},
	}
	mask.expression != ""
}

# ---- notebook_service 的代理权限(2026-08-29 加)----
#
# 这几条锁住的是一个真实的洞:在这之前 SDK 让人用 platform_sdk_demo_service
# 连 Trino,而那个账号在 service_accounts 里(无条件放行),等于**行列级
# 权限对 notebook 完全不生效**。现在换成 notebook_service —— 它只能代理,
# 自己什么都查不了。
#
# **最重要的是下面第二条**:写错了的后果不是报错,是安静地放行。

test_notebook_service_can_impersonate_real_user if {
	trino.allow with input as {
		"context": {"identity": {"user": "notebook_service", "groups": []}},
		"action": {
			"operation": "ImpersonateUser",
			"resource": {"user": {"user": "analyst001"}},
		},
	}
}

test_notebook_service_cannot_select_as_itself if {
	# 它**不在** service_accounts 里,所以没有"服务账号无条件放行"这条兜底。
	# 这条如果挂了,说明有人把 notebook_service 加进了 service_accounts,
	# 那个洞就又回来了。
	not trino.allow with input as {
		"context": {"identity": {"user": "notebook_service", "groups": []}},
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {
				"catalogName": "iceberg",
				"schemaName": "demo",
				"tableName": "orders",
				"columns": ["order_id"],
			}},
		},
	}
}

test_unlisted_account_cannot_impersonate if {
	# 能代理别人是一项特权,不该因为"是个服务账号"就自动拥有。
	not trino.allow with input as {
		"context": {"identity": {"user": "dbt_demo_service", "groups": []}},
		"action": {
			"operation": "ImpersonateUser",
			"resource": {"user": {"user": "analyst001"}},
		},
	}
}

# ---- 推理留痕表和审计表受同一套保护(ADR-085)----
#
# **这几条是回归测试,不是新功能的测试**:2026-08-30 把 `audit_schema`
# 那个字符串改成 `sensitive_schemas` 集合时,行为必须一字不差地保持,
# 只是多认一个 schema。
#
# 测试名用 ASCII —— Rego 的标识符不接受中文(实测 rego_parse_error)。

# 服务账号读不到推理留痕表
test_service_account_denied_on_ml_schema if {
	not trino.allow with input as {
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {"catalogName": "iceberg", "schemaName": "ml", "tableName": "inference_log"}},
		},
		"context": {"identity": {"user": "superset_service", "groups": []}},
	}
}

# 平台管理组读得到
test_platform_admin_allowed_on_ml_schema if {
	trino.allow with input as {
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {"catalogName": "iceberg", "schemaName": "ml", "tableName": "inference_log"}},
		},
		"context": {"identity": {"user": "someone", "groups": ["platform-team"]}},
	}
}

# openmetadata 采元数据仍然能碰(它只采表名/字段名,不采数据行)
test_openmetadata_still_allowed_on_ml_schema if {
	trino.allow with input as {
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {"catalogName": "iceberg", "schemaName": "ml", "tableName": "inference_log"}},
		},
		"context": {"identity": {"user": "openmetadata_service", "groups": []}},
	}
}

# 普通业务表不受这条限制(确认没有误伤)
test_normal_schema_not_restricted if {
	trino.allow with input as {
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {"catalogName": "iceberg", "schemaName": "demo", "tableName": "orders"}},
		},
		"context": {"identity": {"user": "superset_service", "groups": []}},
	}
}

# 维护账号要同时满足两条才行(2026-08-30 实机撞到:只加了第二条)
test_maintenance_account_allowed_on_normal_schema if {
	trino.allow with input as {
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {"catalogName": "iceberg", "schemaName": "demo", "tableName": "orders"}},
		},
		"context": {"identity": {"user": "iceberg_maintenance_service", "groups": []}},
	}
}

test_maintenance_account_allowed_on_sensitive_schema if {
	trino.allow with input as {
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {"catalogName": "iceberg", "schemaName": "audit", "tableName": "query_events"}},
		},
		"context": {"identity": {"user": "iceberg_maintenance_service", "groups": []}},
	}
}
