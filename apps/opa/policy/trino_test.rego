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
