package main

import (
	"bytes"
	"encoding/json"
	"testing"

	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestOperationalStatusJSONKeepsUnsupportedMetricsUnknown(t *testing.T) {
	for _, schema := range []int{57, 58} {
		var output bytes.Buffer
		if err := writeOperationalStatusJSON(&output, duckstore.OperationalStatus{SchemaVersion: schema,
			LatestSchemaVersion: 58, ValidationFailures: 12}, "test", "db.duckdb"); err != nil {
			t.Fatal(err)
		}
		var payload map[string]any
		if err := json.Unmarshal(output.Bytes(), &payload); err != nil {
			t.Fatal(err)
		}
		if payload["schema_supported"] != (schema == 58) {
			t.Fatal(payload)
		}
		_, measured := payload["validation_failures"]
		if measured != (schema == 58) {
			t.Fatal("unknown schema reported measured metrics", payload)
		}
		if measured && (payload["validation_failures"] != float64(12) || len(payload["recent_runs"].([]any)) != 0) {
			t.Fatal(payload)
		}
	}
}
