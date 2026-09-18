package duckdb

import (
	"path/filepath"
	"testing"
)

func TestCoreAndRiskMigrationPreservesIdentity(t *testing.T) {
	ctx := t.Context()
	path := filepath.Join(t.TempDir(), "upgrade.duckdb")
	db, err := Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { db.Close() }()
	migrations, err := Migrations()
	if err != nil {
		t.Fatal(err)
	}
	for _, m := range migrations[:42] {
		if err = applyMigration(ctx, db, m); err != nil {
			t.Fatal(err)
		}
	}
	exec := func(q string) {
		t.Helper()
		if _, e := db.ExecContext(ctx, q); e != nil {
			t.Fatal(e)
		}
	}
	exec(`INSERT INTO ref.company(company_id,legal_name) VALUES(3,'公司');
 INSERT INTO ref.instrument(instrument_id,instrument_type,company_id) VALUES(9007199254740993,'equity',3);
 INSERT INTO ref.instrument_identifier(instrument_identifier_id,instrument_id,provider,identifier_type,identifier_value,valid_from) VALUES(7,9007199254740993,'tdx','symbol','sz000001','2020-01-01');
 INSERT INTO ref.exchange VALUES('XSHE','深圳','CN','Asia/Shanghai','CNY');
 INSERT INTO ref.trading_calendar VALUES('XSHE','2026-09-18',true,'09:30:00','15:00:00','test');
 INSERT INTO ref.listing(listing_id,instrument_id,exchange_mic,trading_currency,valid_from,artifact_id) VALUES(5,9007199254740993,'XSHE','CNY','2020-01-01',1);
 INSERT INTO ref.listing_identifier(listing_identifier_id,listing_id,provider,identifier_type,identifier_value,market_namespace,valid_from,artifact_id) VALUES(8,5,'tdx','code','000001','SZ','2020-01-01',1);
 SELECT nextval('ref.company_id_seq') FROM range(20);
 INSERT INTO reference.country_risk(release_id,artifact_id,source_locator,raw_value,raw_unit,subject_kind,subject_code,observation_date,metric_code,method_code,value,value_status)
 VALUES(1,1,'a','0.01','fraction','country','CN','2026-07-01','country_risk_premium','rating',0.01,'reported'),
 (1,1,'b','0.05','fraction','country','CN','2026-07-01','total_equity_risk_premium','rating',0.05,'reported'),
 (1,1,'c','0.04','fraction','market_group','mature','2026-07-01','mature_market_erp','implied',0.04,'reported');`)
	snapshot := func(table string) string {
		t.Helper()
		var s string
		if e := db.QueryRowContext(ctx, `SELECT CAST(to_json(list(x)) AS VARCHAR) FROM (SELECT * FROM `+table+` ORDER BY ALL) x`).Scan(&s); e != nil {
			t.Fatal(e)
		}
		return s
	}
	tables := map[string]string{"ref.company": "core.company", "ref.instrument": "core.instrument", "ref.instrument_identifier": "core.instrument_identifier", "ref.listing": "core.listing", "ref.listing_identifier": "core.listing_identifier", "ref.exchange": "core.exchange", "ref.trading_calendar": "market.trading_calendar", "reference.country_risk": "reference.risk_observation"}
	before := map[string]string{}
	for old := range tables {
		before[old] = snapshot(old)
	}
	// 多余对象阻止旧 schema 删除时，整个043回滚，不半迁移。
	exec(`CREATE TABLE ref.unexpected(x INTEGER)`)
	if err = Apply(ctx, db); err == nil {
		t.Fatal("unexpected object silently dropped")
	}
	if v, e := CurrentSchemaVersion(ctx, db); e != nil || v != 42 {
		t.Fatalf("failed migration advanced: %d %v", v, e)
	}
	if snapshot("ref.instrument") != before["ref.instrument"] {
		t.Fatal("failed migration changed identity")
	}
	exec(`DROP TABLE ref.unexpected`)
	if err = Apply(ctx, db); err != nil {
		t.Fatal(err)
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = OpenAndMigrate(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	for old, newName := range tables {
		if snapshot(newName) != before[old] {
			t.Fatalf("changed %s", old)
		}
	}
	var id int64
	if err = db.QueryRowContext(ctx, `INSERT INTO core.company(legal_name) VALUES('next') RETURNING company_id`).Scan(&id); err != nil || id <= 20 {
		t.Fatalf("consumed company ID reused: %d %v", id, err)
	}
	if err = db.QueryRowContext(ctx, `INSERT INTO core.instrument(instrument_type) VALUES('equity') RETURNING instrument_id`).Scan(&id); err != nil || id <= 9007199254740993 {
		t.Fatalf("large identity reused: %d %v", id, err)
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
	if err = db.QueryRowContext(ctx, `INSERT INTO reference.equity_risk_premium(release_id,artifact_id,source_locator,raw_value,raw_unit,subject_kind,subject_code,observation_date,metric_code,method_code,value,value_status) VALUES(2,1,'d','0.04','fraction','market_group','mature','2026-07-01','mature_market_erp','implied',0.04,'reported') RETURNING observation_id`).Scan(&id); err != nil || id <= 3 {
		t.Fatalf("risk sequence reused: %d %v", id, err)
	}
}
