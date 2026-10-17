package main

import (
	"encoding/json"
	"io"

	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func writeOperationalStatusJSON(w io.Writer, status duckstore.OperationalStatus, version, database string) error {
	supported := status.SchemaVersion == status.LatestSchemaVersion
	payload := map[string]any{"contract_version": "alphalake-operational-status-v1", "program_version": version,
		"database": database, "schema_version": status.SchemaVersion, "latest_schema_version": status.LatestSchemaVersion,
		"schema_supported": supported}
	if supported {
		runs := status.RecentRuns
		if runs == nil {
			runs = []duckstore.RecentIngestRun{}
		}
		payload["validation_failures"] = status.ValidationFailures
		payload["validation_scope"] = "all_retained_runs_not_new_failures"
		payload["checkpoints"] = status.Checkpoints
		payload["recent_runs"] = runs
	}
	return json.NewEncoder(w).Encode(payload)
}
