package ingest

import (
	"encoding/json"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestRealAnkerReceivablesHistory(t *testing.T) {
	ctx := t.Context()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	output := os.Getenv("ALPHALAKE_RECEIVABLE_HISTORY_EXPORT_DIR")
	if output == "" {
		output = filepath.Join(t.TempDir(), "receipt")
	}
	t.Setenv("ALPHALAKE_CASH_HISTORY_BASE_DB", "")
	t.Setenv("ALPHALAKE_CASH_HISTORY_EXPORT_DIR", output)
	if !t.Run("real_history", TestRealAnkerCashHistory) {
		t.Fatal("fixture failed")
	}
	path := filepath.Join(output, "acceptance.duckdb")
	db, err := duckstore.Open(ctx, path)
	check(err)
	defer func() { db.Close() }()
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET valid_from=DATE '2025-01-01' WHERE source='tdx' AND provider_field='FN13'; DELETE FROM meta.schema_version WHERE version=40`)
	check(err)
	_, err = MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	check(duckstore.Apply(ctx, db))
	added, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if added.Inserted != 1 || added.Updated != 0 || added.Removed != 0 {
		t.Fatalf("unexpected scope effect %+v", added)
	}
	var value float64
	var bits uint32
	var basis, unit string
	check(db.QueryRowContext(ctx, `SELECT f.value,p.value_float32_bits,f.period_type,f.unit FROM fundamental.fact f JOIN fundamental.provider_fact p ON p.provider_fact_id=f.provider_fact_id WHERE f.provider_code='300866' AND f.report_period=DATE '2024-12-31' AND f.source_provider_field='FN13'`).Scan(&value, &bits, &basis, &unit))
	if bits != math.Float32bits(float32(126612165.92)) || value != 126612168 || basis != "instant" || unit != "CNY" {
		t.Fatalf("wrong net amount %v %v %s %s", value, bits, basis, unit)
	}
	at := time.Date(2025, 4, 29, 16, 0, 0, 0, time.UTC)
	for _, delta := range []time.Duration{-time.Nanosecond, 0} {
		var n int
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact_asof(?) WHERE provider_code='300866' AND source_provider_field='FN13' AND report_period=DATE '2024-12-31'`, at.Add(delta)).Scan(&n))
		want := 1
		if delta < 0 {
			want = 0
		}
		if n != want {
			t.Fatalf("PIT %s: %d", delta, n)
		}
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET valid_from=DATE '2025-01-01' WHERE source='tdx' AND provider_field='FN13'`)
	check(err)
	removed, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if removed.Removed != 1 {
		t.Fatalf("revoked mapping %+v", removed)
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET valid_from=DATE '2024-12-31' WHERE source='tdx' AND provider_field='FN13'`)
	check(err)
	restored, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if restored.Inserted != 1 {
		t.Fatalf("restore %+v", restored)
	}
	replay, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if replay.Inserted+replay.Updated+replay.Removed != 0 {
		t.Fatalf("replay %+v", replay)
	}
	check(db.Close())
	db, err = duckstore.Open(ctx, path)
	check(err)
	exported, err := duckstore.ExportValuationData(ctx, db, "300866", time.Date(2024, 12, 31, 0, 0, 0, 0, time.UTC), at)
	check(err)
	raw, err := json.MarshalIndent(exported, "", "  ")
	check(err)
	check(os.WriteFile(filepath.Join(output, "receivables-export.json"), raw, 0600))
}
