package ingest

import (
	"database/sql"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"

	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestRealAssetDisposalCash(t *testing.T) {
	ctx := t.Context()
	out := os.Getenv("ALPHALAKE_DISPOSAL_EXPORT_DIR")
	if out == "" {
		out = t.TempDir()
	}
	t.Setenv("ALPHALAKE_VALUATION_EXPORT_DIR", out)
	if !t.Run("existing_chain", TestRealValuationStandardChain) {
		t.Fatal("base chain")
	}
	path := filepath.Join(out, "acceptance.duckdb")
	db, err := duck.Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { db.Close() }()
	check := func(e error) {
		t.Helper()
		if e != nil {
			t.Fatal(e)
		}
	}
	_, err = db.ExecContext(ctx, `DELETE FROM meta.schema_version WHERE version=45`)
	check(err)
	check(duck.Apply(ctx, db))
	_, err = MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	for _, p := range []string{"2025-06-30", "2025-12-31", "2026-06-30"} {
		var bits uint32
		check(db.QueryRowContext(ctx, `SELECT value_float32_bits FROM fundamental.provider_fact WHERE provider_code='300866' AND report_period=CAST(? AS DATE) AND provider_field='FN110'`, p).Scan(&bits))
		want := float32(0)
		if p == "2026-06-30" {
			want = 17350
		}
		if bits != math.Float32bits(want) {
			t.Fatalf("%s source bits %d", p, bits)
		}
		var n int
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact WHERE provider_code='300866' AND report_period=CAST(? AS DATE) AND source_provider_field='FN110'`, p).Scan(&n))
		expected := 0
		if want != 0 {
			expected = 1
		}
		if n != expected {
			t.Fatalf("zero ambiguity lost: %s %d", p, n)
		}
	}
	var value float64
	var basis, unit string
	check(db.QueryRowContext(ctx, `SELECT value,period_type,unit FROM fundamental.fact WHERE provider_code='300866' AND source_provider_field='FN110' AND report_period=DATE '2026-06-30'`).Scan(&value, &basis, &unit))
	if value != 17350 || basis != "H1" || unit != "CNY" {
		t.Fatalf("wrong semantics %v %s %s", value, basis, unit)
	}
	at := time.Date(2026, 8, 31, 16, 0, 0, 0, time.UTC)
	for _, delta := range []time.Duration{-time.Nanosecond, 0} {
		var n int
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact_asof(?) WHERE provider_code='300866' AND source_provider_field='FN110' AND report_period=DATE '2026-06-30'`, at.Add(delta)).Scan(&n))
		if (n == 1) != (delta == 0) {
			t.Fatal("PIT boundary", n)
		}
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=-1 WHERE provider_field='FN110'`)
	check(err)
	_, err = MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	var n int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact WHERE source_provider_field='FN110'`).Scan(&n))
	if n != 0 {
		t.Fatal("invalid multiplier retained facts")
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=1 WHERE provider_field='FN110'`)
	check(err)
	_, err = MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	replay, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if replay.Inserted+replay.Updated+replay.Removed != 0 {
		t.Fatal("non-idempotent", replay)
	}
	check(db.Close())
	db, err = duck.OpenReadOnly(ctx, path)
	check(err)
	data, err := duck.ExportValuationData(ctx, db, "300866", time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC), time.Date(2026, 9, 18, 0, 0, 0, 0, time.UTC))
	check(err)
	raw, err := json.MarshalIndent(data, "", "  ")
	check(err)
	check(os.WriteFile(filepath.Join(out, "disposal-export.json"), raw, 0644))
}

// 旧真实样本的计数验收固定在原字段范围；新增FN110由本文件独立验证。
func keepPreDisposalFieldScope(t *testing.T, db *sql.DB) {
	t.Helper()
	if _, err := db.ExecContext(t.Context(), `DELETE FROM fundamental.provider_field WHERE source='tdx' AND provider_field='FN110'`); err != nil {
		t.Fatal(err)
	}
}
