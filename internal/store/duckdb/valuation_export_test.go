package duckdb

import (
	"encoding/json"
	"github.com/yinhm/alphalake/internal/domain"
	"path/filepath"
	"testing"
	"time"
)

func TestValuationExportCandidateIdentitiesPreserveVersionSelection(t *testing.T) {
	ctx := t.Context()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "export.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	// 无当前主数据的合成身份：同码多身份、超出 float64 精度、仅远期历史、
	// 后续改码/改来源和未来公告。验证查询边界，不代替真实来源验收。
	for i, r := range []struct {
		id                              int64
		period, announced, source, code string
	}{
		{1, "2026-06-30", "2026-07-01", "tdx", "000001"},
		{9007199254740993, "2026-06-30", "2026-07-01", "tdx", "000001"},
		{3, "2025-03-31", "2025-04-01", "tdx", "000001"},
		{4, "2026-06-30", "2026-07-01", "tdx", "000001"},
		{4, "2026-06-30", "2026-08-01", "tdx", "000002"},
		{5, "2026-06-30", "2026-07-01", "tdx", "000001"},
		{5, "2026-06-30", "2026-08-01", "other", "000001"},
		{6, "2026-06-30", "2026-10-01", "tdx", "000001"},
		{7, "2026-06-30", "2026-07-01", "tdx", "000003"},
	} {
		seedStandardSnapshot(t, db, r.id, r.code, r.source, "monetary_funds", r.period, r.announced, i+1)
	}
	end := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	for _, month := range []time.Month{7, 9} {
		result, err := ExportValuationData(ctx, db, "000001", end, time.Date(2026, month, 10, 0, 0, 0, 0, time.UTC))
		if err != nil {
			t.Fatal(err)
		}
		var windows []struct {
			ID     int64   `json:"instrument_id"`
			Field  string  `json:"field"`
			Value  *string `json:"value"`
			Status string  `json:"coverage_status"`
		}
		if err = json.Unmarshal(result["windows"].(json.RawMessage), &windows); err != nil {
			t.Fatal(err)
		}
		want := map[int64]string{1: "1.0000000000", 9007199254740993: "2.0000000000", 3: "", 5: "7.0000000000"}
		if month == 7 {
			want[4] = "4.0000000000"
			want[5] = "6.0000000000"
		}
		found := map[int64]bool{}
		for _, w := range windows {
			value, ok := want[w.ID]
			if !ok {
				t.Fatalf("superseded/future/unrelated identity leaked: %+v", w)
			}
			if w.Field != "monetary_funds" {
				continue
			}
			if found[w.ID] {
				t.Fatalf("duplicate identity: %d", w.ID)
			}
			found[w.ID] = true
			if value == "" {
				if w.Value != nil || w.Status != "missing_inputs" {
					t.Fatalf("old identity lost missing window: %+v", w)
				}
			} else if w.Value == nil || *w.Value != value || w.Status != "complete" {
				t.Fatalf("wrong value: %+v", w)
			}
		}
		if len(found) != len(want) {
			t.Fatalf("identities: %v, want %v", found, want)
		}
	}
	empty, err := ExportValuationData(ctx, db, "999999", end, end.AddDate(0, 3, 0))
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"facts", "windows", "supplements", "source_conflicts"} {
		if string(empty[key].(json.RawMessage)) != "[]" {
			t.Fatal(key, empty[key])
		}
	}
}

func TestValuationExportMappingVersions(t *testing.T) {
	ctx := t.Context()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "mapping.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err = db.ExecContext(ctx, `DELETE FROM fundamental.provider_field WHERE canonical_field<>'monetary_funds'`); err != nil {
		t.Fatal(err)
	}
	for i, p := range []string{"2025-12-31", "2026-06-30"} {
		period, e := time.Parse("2006-01-02", p)
		if e != nil {
			t.Fatal(e)
		}
		r, sha, _ := archiveFinancialFixture(t, ctx, db, "000001", 1, period, map[int]float32{8: 10})
		if _, e = ReconcileFinancialSourceRecords(ctx, db, 1, "tdx", sha, []domain.ProviderFinancialRecord{r}); e != nil {
			t.Fatal(e)
		}
		kind := "annual"
		if i == 1 {
			kind = "semiannual"
		}
		if _, e = db.ExecContext(ctx, `INSERT INTO fundamental.filing(filing_id,instrument_id,source,source_filing_id,provider_code,report_period,announcement_time,filing_type,filing_variant) VALUES(?,1,'cninfo',?,'000001',?,'2026-07-01',?,'full')`, i+1, p, period, kind); e != nil {
			t.Fatal(e)
		}
	}
	if _, err = RefreshProviderFilingLinks(ctx, db, 1, "tdx"); err != nil {
		t.Fatal(err)
	}
	if _, err = MaterializeCanonicalFundamentals(ctx, db, 1, "tdx"); err != nil {
		t.Fatal(err)
	}
	end := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	asof := end.AddDate(0, 1, 1)
	baseline, err := ExportValuationData(ctx, db, "000001", end, asof)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET valid_to='2026-06-30' WHERE source='tdx' AND provider_field='FN8';
 INSERT INTO fundamental.provider_field SELECT * REPLACE(DATE '2026-06-30' AS valid_from, NULL::DATE AS valid_to) FROM fundamental.provider_field WHERE source='tdx' AND provider_field='FN8';`)
	if err != nil {
		t.Fatal(err)
	}
	split, err := ExportValuationData(ctx, db, "000001", end, asof)
	if err != nil {
		t.Fatal(err)
	}
	before, _ := json.Marshal(baseline)
	after, _ := json.Marshal(split)
	if string(before) != string(after) {
		t.Fatal("same-semantic split changed export", string(after))
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=10000 WHERE source='tdx' AND provider_field='FN8' AND valid_from='2026-06-30';`)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = MaterializeCanonicalFundamentals(ctx, db, 2, "tdx"); err != nil {
		t.Fatal(err)
	}
	changed, err := ExportValuationData(ctx, db, "000001", end, asof)
	if err != nil {
		t.Fatal(err)
	}
	var facts []struct {
		Period     string `json:"period"`
		Multiplier int    `json:"multiplier"`
	}
	if err = json.Unmarshal(changed["facts"].(json.RawMessage), &facts); err != nil {
		t.Fatal(err)
	}
	if len(facts) != 2 || facts[0].Multiplier != 1 || facts[1].Multiplier != 10000 {
		t.Fatalf("wrong period mappings: %+v", facts)
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET valid_to=NULL WHERE source='tdx' AND provider_field='FN8' AND valid_from<'2026-06-30'`)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = ExportValuationData(ctx, db, "000001", end, asof); err == nil {
		t.Fatal("overlapping mapping accepted")
	}
}
