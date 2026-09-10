package duckdb

import (
	"encoding/json"
	"path/filepath"
	"testing"
	"time"
)

func TestValuationExportCandidateIdentitiesPreserveVersionSelection(t *testing.T) {
	ctx := t.Context()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "export.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	// 无当前主数据的合成身份：同码多身份、超出 float64 精度、仅远期历史、
	// 后续改码/改来源和未来公告。验证查询边界，不代替真实来源验收。
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.fact(fact_id,instrument_id,canonical_field,report_period,announcement_time,period_type,
 statement_scope,currency,unit,value,primary_source,source_provider_field,provider_code,source_filing_id,revision_key,normalization_rule,materializer_version)
 SELECT n,id,'monetary_funds',CAST(period AS DATE),CAST(announced AS TIMESTAMPTZ),'instant','provider_default','CNY','CNY',n,source,'FN8',code,1,CAST(n AS VARCHAR),'test','test'
 FROM (VALUES
 (1,1,'2026-06-30','2026-07-01','tdx','000001'),
 (2,9007199254740993,'2026-06-30','2026-07-01','tdx','000001'),
 (3,3,'2010-12-31','2011-01-01','tdx','000001'),
 (4,4,'2026-06-30','2026-07-01','tdx','000001'),
 (5,4,'2026-06-30','2026-08-01','tdx','000002'),
 (6,5,'2026-06-30','2026-07-01','tdx','000001'),
 (7,5,'2026-06-30','2026-08-01','other','000001'),
 (8,6,'2026-06-30','2026-10-01','tdx','000001'),
 (9,7,'2026-06-30','2026-07-01','tdx','000003')) t(n,id,period,announced,source,code)`)
	if err != nil {
		t.Fatal(err)
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
		want := map[int64]string{1: "1.0000000000", 9007199254740993: "2.0000000000", 3: ""}
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
			if w.Field != "FN8" {
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
