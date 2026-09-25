package ingest

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"math"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestRealStatementExpansion(t *testing.T) {
	testRealStatementBatch(t, "statement-expansion-2026", "statement-expansion-20260919", 64, 188, 4, 61, 3, "retained_earnings")
}

func TestRealCashflowReconciliation(t *testing.T) {
	testRealStatementBatch(t, "cashflow-reconciliation-2026", "cashflow-reconciliation-20260919", 12, 36, 0, 12, 0, "cashflow_reconciliation_net_income")
}

func testRealStatementBatch(t *testing.T, directory, review string, fieldCount, matchedCount, missingCount, completeCount, blockedCount int, boundaryField string) {
	ctx := t.Context()
	out := t.TempDir()
	t.Setenv("ALPHALAKE_VALUATION_EXPORT_DIR", out)
	if !t.Run("frozen_baseline", TestRealValuationStandardChain) {
		t.Fatal("baseline failed")
	}
	var evidence struct {
		Approved []string `json:"approved_fields"`
		Reports  []struct {
			PDF string `json:"pdf_path"`
			SHA string `json:"pdf_sha256"`
		} `json:"reports"`
		Observations []struct {
			Field      string  `json:"field"`
			Provider   string  `json:"provider_field"`
			Period     string  `json:"period"`
			Basis      string  `json:"period_basis"`
			Status     string  `json:"status"`
			Bits       uint32  `json:"source_bits"`
			Multiplier float64 `json:"multiplier"`
		} `json:"observations"`
	}
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	check(json.Unmarshal(readFinancialSample(t, "testdata/"+directory, "evidence.json"), &evidence))
	if len(evidence.Approved) != fieldCount || len(evidence.Observations) != fieldCount*3 {
		t.Fatal("review denominator")
	}
	for _, r := range evidence.Reports {
		raw := readFinancialSample(t, "testdata/"+directory, r.PDF)
		if fmt.Sprintf("%x", sha256.Sum256(raw)) != r.SHA {
			t.Fatal("original PDF changed")
		}
	}
	dbPath := filepath.Join(out, "acceptance.duckdb")
	db, err := duck.OpenInitialized(ctx, dbPath)
	check(err)
	defer func() { db.Close() }()
	// Restore the reviewed batch into a real archived/linked baseline. All older
	// fact IDs, values and lineage must remain byte-for-byte equivalent as rows.
	_, err = db.ExecContext(ctx, `CREATE TEMP TABLE previous_facts AS SELECT * FROM fundamental.financial_observations(NULL,NULL,NULL,NULL)`)
	check(err)
	restoreCurrentMappings(t, db, "notes LIKE '"+review+";%'")
	var fields []string
	seen := map[string]bool{}
	for _, r := range evidence.Observations {
		if !seen[r.Provider] {
			fields = append(fields, r.Provider)
			seen[r.Provider] = true
		}
	}
	result, err := MaterializeProviderFundamentals(ctx, db, "tdx", fields...)
	check(err)
	if result.Inserted == 0 || result.Updated != 0 || result.Removed != 0 {
		t.Fatal(result)
	}
	var changed int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM (SELECT * FROM previous_facts EXCEPT SELECT * FROM fundamental.financial_observations(NULL,NULL,NULL,NULL))`).Scan(&changed))
	if changed != 0 {
		t.Fatal("old facts changed", changed)
	}
	matched, missing := 0, 0
	for _, r := range evidence.Observations {
		var bits uint32
		check(sourceEvidenceDB(t, ctx, db).QueryRowContext(ctx, `SELECT value_float32_bits FROM _source_evidence WHERE provider_code='300866' AND provider_field=? AND report_period=CAST(? AS DATE)`, r.Provider, r.Period).Scan(&bits))
		if bits != r.Bits {
			t.Fatal("source evidence differs", r)
		}
		var count int
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.financial_observations(NULL,NULL,NULL,NULL) WHERE provider_code='300866' AND canonical_field=? AND report_period=CAST(? AS DATE)`, r.Field, r.Period).Scan(&count))
		if r.Status == "printed_missing" {
			if count != 0 {
				t.Fatal("source zero promoted", r)
			}
			missing++
			continue
		}
		if count != 1 {
			t.Fatal("reviewed nonzero missing", r, count)
		}
		var value float64
		var unit, basis string
		check(db.QueryRowContext(ctx, `SELECT value,unit,period_type FROM fundamental.financial_observations(NULL,NULL,NULL,NULL) WHERE provider_code='300866' AND canonical_field=? AND report_period=CAST(? AS DATE)`, r.Field, r.Period).Scan(&value, &unit, &basis))
		wantBasis := "H1"
		if r.Period == "2025-12-31" {
			wantBasis = "FY"
		}
		if r.Basis == "instant" {
			wantBasis = "instant"
		}
		if value != float64(math.Float32frombits(r.Bits))*r.Multiplier || unit != "CNY" || basis != wantBasis {
			t.Fatal("standard meaning differs", r, value, unit, basis)
		}
		matched++
	}
	if matched != matchedCount || missing != missingCount {
		t.Fatal(matched, missing)
	}
	// CNINFO date precision remains unavailable until next China midnight.
	at := time.Date(2026, 8, 31, 16, 0, 0, 0, time.UTC)
	for _, delta := range []time.Duration{-time.Nanosecond, 0} {
		var count int
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.financial_observations_asof(NULL,NULL,NULL,?) WHERE provider_code='300866' AND canonical_field=? AND report_period=DATE '2026-06-30'`, at.Add(delta), boundaryField).Scan(&count))
		if (count == 1) != (delta == 0) {
			t.Fatal("PIT boundary", count)
		}
	}
	data, err := duck.ExportValuationData(ctx, db, "300866", time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC), at)
	check(err)
	encoded, err := json.Marshal(data)
	check(err)
	var exported struct {
		Windows []struct {
			Field  string  `json:"field"`
			Value  *string `json:"value"`
			Status string  `json:"coverage_status"`
		} `json:"windows"`
	}
	check(json.Unmarshal(encoded, &exported))
	complete, blocked := 0, 0
	for _, name := range evidence.Approved {
		found := false
		want := float64(0)
		valid := true
		for _, r := range evidence.Observations {
			if r.Field != name || (r.Basis == "instant" && r.Period != "2026-06-30") {
				continue
			}
			if r.Status != "matched" {
				valid = false
				continue
			}
			sign := float64(1)
			if r.Basis == "ytd" && r.Period == "2025-06-30" {
				sign = -1
			}
			want += sign * float64(math.Float32frombits(r.Bits)) * r.Multiplier
		}
		for _, w := range exported.Windows {
			if w.Field != name {
				continue
			}
			found = true
			if !valid {
				if w.Status == "complete" || w.Value != nil {
					t.Fatal("missing window fabricated", w)
				}
				blocked++
				continue
			}
			if w.Status != "complete" || w.Value == nil {
				t.Fatal("reviewed window absent", w)
			}
			got, e := strconv.ParseFloat(*w.Value, 64)
			check(e)
			if math.Abs(got-want) > 4*math.Abs(math.Nextafter(want, math.Inf(1))-want) {
				t.Fatal("TTM/instant export differs", name, got, want)
			}
			complete++
		}
		if !found {
			t.Fatal("standard field absent from valuation export", name)
		}
	}
	if complete != completeCount || blocked != blockedCount {
		t.Fatal("window coverage", complete, blocked)
	}
	// Batch invalidation must not remove unrelated facts.
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=-1 WHERE notes LIKE ?`, review+";%")
	check(err)
	rejected, err := MaterializeProviderFundamentals(ctx, db, "tdx", fields...)
	check(err)
	if rejected.Removed != result.Inserted {
		t.Fatal("invalidated batch not removed", result, rejected)
	}
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM (SELECT * FROM previous_facts EXCEPT SELECT * FROM fundamental.financial_observations(NULL,NULL,NULL,NULL))`).Scan(&changed))
	if changed != 0 {
		t.Fatal("unrelated facts removed")
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=(SELECT value_multiplier FROM fundamental.source_field s WHERE s.source=provider_field.source AND s.provider_field=provider_field.provider_field) WHERE notes LIKE ?`, review+";%")
	check(err)
	restored, err := MaterializeProviderFundamentals(ctx, db, "tdx", fields...)
	check(err)
	if restored.Inserted != result.Inserted {
		t.Fatal("batch not rebuilt", restored)
	}
	check(db.Close())
	db, err = duck.OpenInitialized(ctx, dbPath)
	check(err)
	replay, err := MaterializeProviderFundamentals(ctx, db, "tdx", fields...)
	check(err)
	if replay.Inserted != 0 || replay.Updated != 0 || replay.Removed != 0 {
		t.Fatal("replay changed facts", replay)
	}
	t.Logf("batch inserted=%d rejected=%d; reviewed current cells=%d missing=%d", result.Inserted, result.Rejected, matched, missing)
}
