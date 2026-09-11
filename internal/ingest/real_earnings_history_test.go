package ingest

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"

	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestRealAnkerEarningsHistory(t *testing.T) {
	ctx := t.Context()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("ALPHALAKE_DUCKDB_MEMORY_LIMIT", "1GB")
	t.Setenv("ALPHALAKE_DUCKDB_THREADS", "2")
	base := os.Getenv("ALPHALAKE_EARNINGS_HISTORY_BASE_DB")
	output := os.Getenv("ALPHALAKE_EARNINGS_HISTORY_EXPORT_DIR")
	if output == "" {
		output = filepath.Join(t.TempDir(), "review")
	}
	check(os.Mkdir(output, 0755))
	if base == "" {
		fixture := filepath.Join(t.TempDir(), "cash")
		t.Setenv("ALPHALAKE_CASH_HISTORY_BASE_DB", "")
		t.Setenv("ALPHALAKE_CASH_HISTORY_EXPORT_DIR", fixture)
		if !t.Run("standard_history_fixture", TestRealAnkerCashHistory) {
			t.Fatal("fixture failed")
		}
		base = filepath.Join(fixture, "acceptance.duckdb")
	}
	if stat, err := os.Stat(base + ".wal"); err == nil && stat.Size() != 0 {
		t.Fatal("base has WAL")
	} else if err != nil && !os.IsNotExist(err) {
		check(err)
	}
	src, err := os.Open(base)
	check(err)
	path := filepath.Join(output, "acceptance.duckdb")
	dst, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	check(err)
	hash := sha256.New()
	_, err = io.Copy(io.MultiWriter(dst, hash), src)
	check(err)
	check(dst.Close())
	check(src.Close())
	baseHash := fmt.Sprintf("%x", hash.Sum(nil))
	db, err := duckstore.Open(ctx, path)
	check(err)
	defer func() { _ = db.Close() }()
	const fields = "('FN86','FN305','FN306','FN83','FN82','FN301')"
	// 最新自包含夹具仅回退这六个字段；真实schema38副本本来就处于该边界。
	_, err = db.ExecContext(ctx, "UPDATE fundamental.provider_field SET valid_from=DATE '2025-01-01' WHERE source='tdx' AND provider_field IN "+fields+"; DELETE FROM meta.schema_version WHERE version=39")
	check(err)
	_, err = MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	_, err = db.ExecContext(ctx, "CREATE TEMP TABLE earnings_before AS SELECT * FROM fundamental.fact; CREATE TEMP TABLE earnings_catalog_before AS SELECT * FROM fundamental.provider_field WHERE provider_field NOT IN "+fields)
	check(err)
	currentEnd := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	asof := time.Date(2026, 9, 10, 0, 0, 0, 0, time.UTC)
	before, err := duckstore.ExportValuationData(ctx, db, "300866", currentEnd, asof)
	check(err)
	beforeJSON, err := json.Marshal(before)
	check(err)
	check(duckstore.Apply(ctx, db))
	added, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if added.Inserted != 18 || added.Updated != 0 || added.Removed != 0 {
		t.Fatalf("expected 18 new facts: %+v", added)
	}
	var evidence struct {
		Values []struct {
			Period, Field string
			Bits          uint32 `json:"source_bits"`
		} `json:"source_values"`
	}
	check(json.Unmarshal(readFinancialSample(t, "testdata/anker-cash-history-2024", "earnings-verified.json"), &evidence))
	if len(evidence.Values) != 18 {
		t.Fatal("eighteen source values required")
	}
	for _, r := range evidence.Values {
		var v float64
		var basis, unit string
		check(db.QueryRowContext(ctx, `SELECT CAST(value AS DOUBLE),period_type,unit FROM fundamental.fact WHERE provider_code='300866' AND source_provider_field=? AND report_period=CAST(? AS DATE)`, r.Field, r.Period).Scan(&v, &basis, &unit))
		if v != float64(math.Float32frombits(r.Bits)) || unit != "CNY" || basis != map[string]string{"2024-06-30": "H1", "2024-09-30": "9M", "2024-12-31": "FY"}[r.Period] {
			t.Fatal("standard source differs", r, v, basis, unit)
		}
	}
	for period, available := range map[string]string{"2024-06-30": "2024-08-31T00:00:00+08:00", "2024-09-30": "2024-10-31T00:00:00+08:00", "2024-12-31": "2025-04-30T00:00:00+08:00"} {
		at, err := time.Parse(time.RFC3339, available)
		check(err)
		for _, offset := range []time.Duration{-time.Nanosecond, 0} {
			var n int
			check(db.QueryRowContext(ctx, "SELECT count(*) FROM fundamental.fact_asof(CAST(? AS TIMESTAMPTZ)) WHERE provider_code='300866' AND report_period=CAST(? AS DATE) AND source_provider_field IN "+fields, at.Add(offset), period).Scan(&n))
			want := 0
			if offset == 0 {
				want = 6
			}
			if n != want {
				t.Fatal("PIT boundary", period, n, want)
			}
		}
	}
	_, err = db.ExecContext(ctx, "UPDATE fundamental.provider_field SET valid_from=DATE '2025-01-01' WHERE source='tdx' AND provider_field IN "+fields)
	check(err)
	removed, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if removed.Removed != 18 {
		t.Fatal("withdrawal", removed)
	}
	_, err = db.ExecContext(ctx, "UPDATE fundamental.provider_field SET valid_from=DATE '2024-06-30' WHERE source='tdx' AND provider_field IN "+fields)
	check(err)
	restored, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if restored.Inserted != 18 {
		t.Fatal("restoration", restored)
	}
	var changed int
	check(db.QueryRowContext(ctx, "SELECT count(*) FROM (SELECT * FROM earnings_catalog_before EXCEPT SELECT * FROM fundamental.provider_field WHERE provider_field NOT IN "+fields+")").Scan(&changed))
	if changed != 0 {
		t.Fatal("unrelated mappings changed")
	}
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM (SELECT * FROM earnings_before EXCEPT SELECT * FROM fundamental.fact)`).Scan(&changed))
	if changed != 0 {
		t.Fatal("existing facts changed", changed)
	}
	after, err := duckstore.ExportValuationData(ctx, db, "300866", currentEnd, asof)
	check(err)
	afterJSON, err := json.Marshal(after)
	check(err)
	if !bytes.Equal(beforeJSON, afterJSON) {
		t.Fatal("current inputs changed")
	}
	check(db.Close())
	db, err = duckstore.OpenAndMigrate(ctx, path)
	check(err)
	replayed, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if replayed.Inserted != 0 || replayed.Updated != 0 || replayed.Removed != 0 {
		t.Fatal("reopened replay", replayed)
	}
	for name, period := range map[string]time.Time{"current.json": currentEnd, "historical.json": currentEnd.AddDate(-1, 0, 0)} {
		cutoff := asof
		if name == "historical.json" {
			cutoff = time.Date(2025, 9, 1, 0, 0, 0, 0, time.FixedZone("CST", 8*3600))
		}
		data, err := duckstore.ExportValuationData(ctx, db, "300866", period, cutoff)
		check(err)
		raw, err := json.MarshalIndent(data, "", "  ")
		check(err)
		check(os.WriteFile(filepath.Join(output, name), raw, 0644))
	}
	check(db.Close())
	src, err = os.Open(base)
	check(err)
	hash.Reset()
	_, err = io.Copy(hash, src)
	check(err)
	check(src.Close())
	if fmt.Sprintf("%x", hash.Sum(nil)) != baseHash {
		t.Fatal("base database changed")
	}
	raw, err := json.MarshalIndent(map[string]any{"base_database": base, "base_sha256": baseHash, "database": path, "schema": 39, "added": added, "removed": removed, "restored": restored, "replay": replayed, "current_inputs_unchanged": true, "existing_fact_contents_unchanged": true, "scope": "single_company_history_on_copy_not_main_publication"}, "", "  ")
	check(err)
	check(os.WriteFile(filepath.Join(output, "acceptance.json"), raw, 0644))
}

// 可复用已完成迁移验收的自有副本，不再复制整库；从不修改原主库。
func TestRealAnkerQ1History(t *testing.T) {
	ctx := t.Context()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("ALPHALAKE_DUCKDB_MEMORY_LIMIT", "1GB")
	t.Setenv("ALPHALAKE_DUCKDB_THREADS", "2")
	output := os.Getenv("ALPHALAKE_Q1_HISTORY_REVIEW_DIR")
	if output == "" {
		output = filepath.Join(t.TempDir(), "earnings")
		t.Setenv("ALPHALAKE_EARNINGS_HISTORY_BASE_DB", "")
		t.Setenv("ALPHALAKE_EARNINGS_HISTORY_EXPORT_DIR", output)
		if !t.Run("earnings_fixture", TestRealAnkerEarningsHistory) {
			t.Fatal("fixture failed")
		}
	}
	var receipt struct {
		Database string
		Base     string `json:"base_database"`
		Scope    string
	}
	raw, err := os.ReadFile(filepath.Join(output, "acceptance.json"))
	check(err)
	check(json.Unmarshal(raw, &receipt))
	path := filepath.Join(output, "acceptance.duckdb")
	if receipt.Database != path || receipt.Base == path || receipt.Scope != "single_company_history_on_copy_not_main_publication" {
		t.Fatal("not an owned acceptance copy")
	}
	db, err := duckstore.OpenAndMigrate(ctx, path)
	check(err)
	defer func() { _ = db.Close() }()
	end := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	asof := time.Date(2026, 9, 10, 0, 0, 0, 0, time.UTC)
	before, err := duckstore.ExportValuationData(ctx, db, "300866", end, asof)
	check(err)
	beforeJSON, err := json.Marshal(before)
	check(err)
	importAnkerHistoricalEvidence(t, db, "testdata/anker-q1-history-2024", output)
	added, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	var value float64
	var basis, unit string
	check(db.QueryRowContext(ctx, `SELECT CAST(value AS DOUBLE),period_type,unit FROM fundamental.fact WHERE provider_code='300866' AND report_period=DATE '2024-03-31' AND source_provider_field='FN230'`).Scan(&value, &basis, &unit))
	if value != float64(math.Float32frombits(1333950316)) || basis != "Q1" || unit != "CNY" {
		t.Fatal(value, basis, unit)
	}
	at, err := time.Parse(time.RFC3339, "2024-04-28T00:00:00+08:00")
	check(err)
	for _, offset := range []time.Duration{-time.Nanosecond, 0} {
		var n int
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact_asof(CAST(? AS TIMESTAMPTZ)) WHERE provider_code='300866' AND report_period=DATE '2024-03-31' AND source_provider_field='FN230'`, at.Add(offset)).Scan(&n))
		want := 0
		if offset == 0 {
			want = 1
		}
		if n != want {
			t.Fatal("Q1 boundary", n, want)
		}
	}
	check(db.Close())
	db, err = duckstore.OpenAndMigrate(ctx, path)
	check(err)
	importAnkerHistoricalEvidence(t, db, "testdata/anker-q1-history-2024", output)
	replay, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if replay.Inserted != 0 || replay.Updated != 0 || replay.Removed != 0 {
		t.Fatal("Q1 replay", replay)
	}
	after, err := duckstore.ExportValuationData(ctx, db, "300866", end, asof)
	check(err)
	afterJSON, err := json.Marshal(after)
	check(err)
	if !bytes.Equal(beforeJSON, afterJSON) {
		t.Fatal("current inputs changed")
	}
	historical, err := duckstore.ExportValuationData(ctx, db, "300866", end.AddDate(-1, 0, 0), time.Date(2025, 9, 1, 0, 0, 0, 0, time.FixedZone("CST", 8*3600)))
	check(err)
	raw, err = json.MarshalIndent(historical, "", "  ")
	check(err)
	check(os.WriteFile(filepath.Join(output, "historical-q1.json"), raw, 0644))
	raw, err = json.MarshalIndent(map[string]any{"added": added, "replay": replay, "current_inputs_unchanged": true, "database": path, "source_bits": uint32(1333950316), "scope": "single_company_Q1_on_owned_copy_not_main_publication"}, "", "  ")
	check(err)
	check(os.WriteFile(filepath.Join(output, "q1-acceptance.json"), raw, 0644))
}
