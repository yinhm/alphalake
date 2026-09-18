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

func TestValuationExportMappingVersions(t *testing.T) {
	ctx := t.Context()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "mapping.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	// 合成数据专门验证映射版本，不宣称新增真实字段语义验收。
	_, err = db.ExecContext(ctx, `
 INSERT INTO meta.artifact(artifact_id,source,dataset,source_locator,fetched_at,sha256,content_length)
 VALUES(1,'tdx','test','test',now(),'test',1);
 INSERT INTO fundamental.filing(filing_id,source,source_filing_id,provider_code) VALUES(1,'cninfo','test','000001');
 INSERT INTO fundamental.provider_fact(provider_fact_id,instrument_id,source,report_period,provider_code,provider_field,value,value_float32_bits,artifact_id,revision_key)
 SELECT n,1,'tdx',CAST(period AS DATE),'000001','FN8',10,1092616192,1,CAST(n AS VARCHAR)
 FROM (VALUES (1,'2025-12-31'),(2,'2026-06-30')) t(n,period);
 INSERT INTO fundamental.fact(instrument_id,canonical_field,report_period,announcement_time,period_type,statement_scope,currency,unit,value,primary_source,source_provider_field,provider_code,provider_fact_id,source_filing_id,revision_key,normalization_rule,materializer_version)
 SELECT 1,'monetary_funds',report_period,'2026-07-01','instant','provider_default','CNY','CNY',10,'tdx','FN8','000001',provider_fact_id,1,revision_key,'test','test' FROM fundamental.provider_fact;`)
	if err != nil {
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
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=10000 WHERE source='tdx' AND provider_field='FN8' AND valid_from='2026-06-30';
 UPDATE fundamental.fact SET value=100000 WHERE report_period='2026-06-30';`)
	if err != nil {
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
