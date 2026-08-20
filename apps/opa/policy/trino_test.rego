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
