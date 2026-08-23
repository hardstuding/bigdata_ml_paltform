package trino_test

# 覆盖 ADR-063 新增的列级脱敏(columnMask)/行级过滤(rowFilters)规则。
# 和 trino_test.rego(表级 allow)分开一个文件,职责不同,不是同一批改动。

import data.trino

mock_grants_masking := [
	# analyst001 对 demo.customers 只有 level 1 的 grant——够 SELECT,不够看敏感列明文。
	{"username": "analyst001", "table_fqn": "demo.customers", "security_level": 1, "granted_at": "2026-08-14T00:00:00+00:00", "expires_at": ""},
	# manager001 对同一张表有 level 2 的 grant——够看手机号/邮箱明文,身份证还不够(要 level 3)。
	{"username": "manager001", "table_fqn": "demo.customers", "security_level": 2, "granted_at": "2026-08-14T00:00:00+00:00", "expires_at": ""},
	# auditor001 有 level 3——什么都能看明文。
	{"username": "auditor001", "table_fqn": "demo.customers", "security_level": 3, "granted_at": "2026-08-14T00:00:00+00:00", "expires_at": ""},
]

mock_departments := [
	{"username": "sales001", "department": "sales"},
	{"username": "sales002", "department": "sales"},
	{"username": "ops001", "department": "ops"},
]

# ---- 列级脱敏 ----

test_low_level_grant_masks_phone if {
	trino.columnMask == {"expression": "regexp_replace(CAST(\"phone_number\" AS varchar), '(\\d{3})\\d+(\\d{4})', '$1****$2')"}
		with input as {
			"context": {"identity": {"user": "analyst001", "groups": []}},
			"action": {"operation": "GetColumnMask", "resource": {"column": {"schemaName": "demo", "tableName": "customers", "columnName": "phone_number", "columnType": "varchar"}}},
		}
		with data.trino.grants as mock_grants_masking
}

test_sufficient_level_grant_sees_phone_plaintext if {
	not trino.columnMask
		with input as {
			"context": {"identity": {"user": "manager001", "groups": []}},
			"action": {"operation": "GetColumnMask", "resource": {"column": {"schemaName": "demo", "tableName": "customers", "columnName": "phone_number", "columnType": "varchar"}}},
		}
		with data.trino.grants as mock_grants_masking
}

test_level2_grant_still_masks_id_card if {
	trino.columnMask == {"expression": "'***MASKED***'"}
		with input as {
			"context": {"identity": {"user": "manager001", "groups": []}},
			"action": {"operation": "GetColumnMask", "resource": {"column": {"schemaName": "demo", "tableName": "customers", "columnName": "id_card_no", "columnType": "varchar"}}},
		}
		with data.trino.grants as mock_grants_masking
}

test_level3_grant_sees_id_card_plaintext if {
	not trino.columnMask
		with input as {
			"context": {"identity": {"user": "auditor001", "groups": []}},
			"action": {"operation": "GetColumnMask", "resource": {"column": {"schemaName": "demo", "tableName": "customers", "columnName": "id_card_no", "columnType": "varchar"}}},
		}
		with data.trino.grants as mock_grants_masking
}

test_non_sensitive_column_never_masked if {
	not trino.columnMask
		with input as {
			"context": {"identity": {"user": "analyst001", "groups": []}},
			"action": {"operation": "GetColumnMask", "resource": {"column": {"schemaName": "demo", "tableName": "customers", "columnName": "order_count", "columnType": "bigint"}}},
		}
		with data.trino.grants as mock_grants_masking
}

test_email_partial_mask if {
	trino.columnMask == {"expression": "regexp_replace(CAST(\"email\" AS varchar), '(^.{2}).*(@.*$)', '$1***$2')"}
		with input as {
			"context": {"identity": {"user": "analyst001", "groups": []}},
			"action": {"operation": "GetColumnMask", "resource": {"column": {"schemaName": "demo", "tableName": "customers", "columnName": "email", "columnType": "varchar"}}},
		}
		with data.trino.grants as mock_grants_masking
}

test_no_grant_at_all_still_masks_sensitive_column if {
	# 纵深防御:即使正常情况下 allow 早就该拒绝了(没有 grant 连 SELECT 都过不了),
	# 脱敏判断自己也要 fail-safe,不依赖外面一定先挡住。
	trino.columnMask == {"expression": "'***MASKED***'"}
		with input as {
			"context": {"identity": {"user": "nobody", "groups": []}},
			"action": {"operation": "GetColumnMask", "resource": {"column": {"schemaName": "demo", "tableName": "customers", "columnName": "id_card_no", "columnType": "varchar"}}},
		}
		with data.trino.grants as mock_grants_masking
}

test_service_account_exempt_from_masking if {
	not trino.columnMask
		with input as {
			"context": {"identity": {"user": "superset_service", "groups": []}},
			"action": {"operation": "GetColumnMask", "resource": {"column": {"schemaName": "demo", "tableName": "customers", "columnName": "id_card_no", "columnType": "varchar"}}},
		}
		with data.trino.grants as mock_grants_masking
}

test_platform_admin_exempt_from_masking if {
	not trino.columnMask
		with input as {
			"context": {"identity": {"user": "admin", "groups": ["platform-team"]}},
			"action": {"operation": "GetColumnMask", "resource": {"column": {"schemaName": "demo", "tableName": "customers", "columnName": "id_card_no", "columnType": "varchar"}}},
		}
		with data.trino.grants as mock_grants_masking
}

# ---- 行级过滤 ----

test_row_filter_scopes_to_own_department if {
	trino.rowFilters == [{"expression": "department = 'sales'"}]
		with input as {
			"context": {"identity": {"user": "sales001", "groups": []}},
			"action": {"operation": "GetRowFilters", "resource": {"table": {"schemaName": "demo", "tableName": "regional_sales"}}},
		}
		with data.trino.user_departments as mock_departments
}

test_row_filter_different_department_gets_different_filter if {
	trino.rowFilters == [{"expression": "department = 'ops'"}]
		with input as {
			"context": {"identity": {"user": "ops001", "groups": []}},
			"action": {"operation": "GetRowFilters", "resource": {"table": {"schemaName": "demo", "tableName": "regional_sales"}}},
		}
		with data.trino.user_departments as mock_departments
}

test_row_filter_unknown_user_denied_all_rows if {
	# 边界情况:用户不在 employees.csv 里(数据没同步/账号刚建还没维护部门)——
	# 按"宁可漏权限报错,不要漏配置导致意外授权"原则,过滤成空结果集,不是
	# 默认不过滤看到全部。
	trino.rowFilters == [{"expression": "1 = 0"}]
		with input as {
			"context": {"identity": {"user": "ghost001", "groups": []}},
			"action": {"operation": "GetRowFilters", "resource": {"table": {"schemaName": "demo", "tableName": "regional_sales"}}},
		}
		with data.trino.user_departments as mock_departments
}

test_row_filter_not_applied_to_unlisted_table if {
	# demo.customers 不在 row_level_filtered_tables 白名单里,不应该被过滤。
	not trino.rowFilters
		with input as {
			"context": {"identity": {"user": "sales001", "groups": []}},
			"action": {"operation": "GetRowFilters", "resource": {"table": {"schemaName": "demo", "tableName": "customers"}}},
		}
		with data.trino.user_departments as mock_departments
}

test_service_account_exempt_from_row_filter if {
	not trino.rowFilters
		with input as {
			"context": {"identity": {"user": "superset_service", "groups": []}},
			"action": {"operation": "GetRowFilters", "resource": {"table": {"schemaName": "demo", "tableName": "regional_sales"}}},
		}
		with data.trino.user_departments as mock_departments
}

test_platform_admin_exempt_from_row_filter if {
	not trino.rowFilters
		with input as {
			"context": {"identity": {"user": "admin", "groups": ["platform-team"]}},
			"action": {"operation": "GetRowFilters", "resource": {"table": {"schemaName": "demo", "tableName": "regional_sales"}}},
		}
		with data.trino.user_departments as mock_departments
}
