package duckdb

import (
	"path/filepath"
	"testing"
)

func TestCoreAndRiskCurrentSchemaPersistsIdentity(t *testing.T) {
	ctx := t.Context()
	path := filepath.Join(t.TempDir(), "core.duckdb")
	db, err := OpenInitialized(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { db.Close() }()
	exec := func(q string) {
		t.Helper()
		if _, err := db.ExecContext(ctx, q); err != nil {
			t.Fatal(err)
		}
	}
	exec(`INSERT INTO core.company(legal_name) SELECT 'company' FROM range(20);
 INSERT INTO core.instrument(instrument_id,instrument_type,company_id) VALUES(9007199254740993,'equity',3);
 INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value,valid_from) VALUES(9007199254740993,'tdx','symbol','sz000001','2020-01-01');
 INSERT INTO core.exchange VALUES('XSHE','深圳','CN','Asia/Shanghai','CNY');
 INSERT INTO market.trading_calendar VALUES('XSHE','2026-09-18',true,'09:30:00','15:00:00','test');
 INSERT INTO core.listing(listing_id,instrument_id,exchange_mic,trading_currency,valid_from,artifact_id) VALUES(5,9007199254740993,'XSHE','CNY','2020-01-01',1);
 INSERT INTO core.listing_identifier(listing_id,provider,identifier_type,identifier_value,market_namespace,valid_from,artifact_id) VALUES(5,'tdx','code','000001','SZ','2020-01-01',1);
 INSERT INTO reference.country_risk(release_id,artifact_id,source_locator,raw_value,raw_unit,subject_kind,subject_code,observation_date,metric_code,method_code,value,value_status)
 VALUES(1,1,'a','0.01','fraction','country','CN','2026-07-01','country_risk_premium','rating',0.01,'reported');
 INSERT INTO reference.equity_risk_premium(release_id,artifact_id,source_locator,raw_value,raw_unit,subject_kind,subject_code,observation_date,metric_code,method_code,value,value_status)
 VALUES(1,1,'b','0.05','fraction','country','CN','2026-07-01','total_equity_risk_premium','rating',0.05,'reported');`)
	snapshot := func(table string) string {
		t.Helper()
		var s string
		if err := db.QueryRowContext(ctx, `SELECT CAST(to_json(list(x)) AS VARCHAR) FROM (SELECT * FROM `+table+` ORDER BY ALL) x`).Scan(&s); err != nil {
			t.Fatal(err)
		}
		return s
	}
	before := map[string]string{}
	for _, table := range []string{"core.company", "core.instrument", "core.instrument_identifier", "core.exchange", "core.listing", "core.listing_identifier", "market.trading_calendar", "reference.risk_observation"} {
		before[table] = snapshot(table)
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = OpenInitialized(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	for table, want := range before {
		if snapshot(table) != want {
			t.Fatalf("reopen changed %s", table)
		}
	}
	var id int64
	if err = db.QueryRowContext(ctx, `INSERT INTO core.company(legal_name) VALUES('next') RETURNING company_id`).Scan(&id); err != nil || id != 21 {
		t.Fatalf("sequence reset: %d %v", id, err)
	}
	if _, err = db.ExecContext(ctx, `INSERT INTO core.listing(instrument_id,exchange_mic,trading_currency,valid_from,artifact_id) VALUES(99,'XSHE','CNY','2020-01-01',1)`); err == nil {
		t.Fatal("listing foreign key lost")
	}
	if _, err = db.ExecContext(ctx, `UPDATE reference.country_risk SET metric_code='mature_market_erp'`); err == nil {
		t.Fatal("country risk accepts ERP")
	}
	if _, err = db.ExecContext(ctx, `UPDATE reference.equity_risk_premium SET metric_code='country_risk_premium'`); err == nil {
		t.Fatal("ERP accepts country spread")
	}
}
