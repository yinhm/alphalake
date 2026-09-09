package duckdb

import (
	"path/filepath"
	"strings"
	"testing"
)

// Exercise the real embedded migration on a populated v26 database. These are
// storage-contract tests, not evidence that an external data source is parsed.
func TestReferenceSchemaUpgradeAndConstraints(t *testing.T) {
	ctx := t.Context()
	path := filepath.Join(t.TempDir(), "reference.duckdb")
	db, err := Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if db != nil {
			db.Close()
		}
	}()
	migrations, err := Migrations()
	if err != nil {
		t.Fatal(err)
	}
	for _, m := range migrations[:26] {
		if err := applyMigration(ctx, db, m); err != nil {
			t.Fatal(err)
		}
	}
	exec := func(q string) {
		t.Helper()
		if _, err := db.ExecContext(ctx, q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
	exec(`INSERT INTO meta.checkpoint VALUES ('test','legacy','keep','v26',current_timestamp)`)
	exec(`INSERT INTO meta.ingest_run (ingest_run_id,source,dataset,status) VALUES (1,'test','reference','completed')`)
	exec(`INSERT INTO meta.artifact (artifact_id,source,dataset,source_locator,fetched_at,sha256,content_length) VALUES (1,'test','reference','fixture','2026-09-01',repeat('a',64),1)`)
	for range 2 {
		if err := Apply(ctx, db); err != nil {
			t.Fatal(err)
		}
	}
	release := `INSERT INTO meta.dataset_release (release_id,source,dataset,content_key,publication_precision,available_at,availability_basis,first_seen_at,recorded_at,parser_version,normalization_version,ingest_run_id) VALUES (1,'test','reference',repeat('a',64),'unknown','2026-09-01','first_seen','2026-09-01','2026-09-02','v1','v1',1)`
	exec(release)
	exec(`INSERT INTO meta.dataset_release_artifact VALUES (1,1,'data')`)
	country := `INSERT INTO reference.country_risk VALUES (1,1,1,'Sheet!A1','0','percent','country','CN','2026-07-01','country_risk_premium','rating',0,'reported')`
	exec(country)
	exec(`INSERT INTO reference.country_risk VALUES (2,1,1,'Sheet!A2','NA','percent','country','US','2026-07-01','country_risk_premium','rating',NULL,'missing')`)
	yield := `INSERT INTO market.yield_curve_point VALUES (1,1,1,'curve:10y','-0.1','percent','government','2026-09-01','CNY',120,'yield_to_maturity','annual','unknown',-0.001)`
	exec(yield)
	fx := `INSERT INTO market.fx_rate VALUES (1,1,1,'fixing','0.91','CNY/HKD','HKD','CNY','2026-09-01','date','Asia/Shanghai','official','midpoint',0.91)`
	exec(fx)
	exec(`INSERT INTO classification.taxonomy (taxonomy_id,source,taxonomy_code,name,taxonomy_type) VALUES (1,'test','industry_2026','Industry','industry')`)
	exec(`INSERT INTO classification.node (node_id,taxonomy_id,source_node_code,name) VALUES (1,1,'alcohol','Alcohol')`)
	industry := `INSERT INTO reference.industry_stat VALUES (1,1,1,'Sheet!B2','1.2','ratio',1,'global','2026-01-01','beta_unlevered','cash_excluded','mean',20,1.2,'reported')`
	exec(industry)
	rejects := map[string]string{
		"duplicate content":       strings.Replace(release, "VALUES (1,", "VALUES (2,", 1),
		"missing ingest run":      strings.Replace(strings.Replace(strings.Replace(release, "VALUES (1,", "VALUES (9,", 1), "repeat('a',64)", "repeat('c',64)", 1), "'v1','v1',1)", "'v1','v1',999)", 1),
		"unknown publication":     `UPDATE meta.dataset_release SET source_published_at='2026-08-01' WHERE release_id=1`,
		"false first seen":        `UPDATE meta.dataset_release SET available_at='2026-08-01' WHERE release_id=1`,
		"self supersession":       `UPDATE meta.dataset_release SET supersedes_release_id=1 WHERE release_id=1`,
		"missing artifact":        `INSERT INTO meta.dataset_release_artifact VALUES (1,999,'data')`,
		"missing release":         `INSERT INTO meta.dataset_release_artifact VALUES (999,1,'data')`,
		"country duplicate":       strings.Replace(country, "VALUES (1,", "VALUES (3,", 1),
		"missing is not zero":     `UPDATE reference.country_risk SET value=0 WHERE observation_id=2`,
		"reported cannot be null": `UPDATE reference.country_risk SET value=NULL WHERE observation_id=1`,
		"metric whitelist":        `UPDATE reference.country_risk SET metric_code='wacc' WHERE observation_id=1`,
		"mature market scope":     `UPDATE reference.country_risk SET metric_code='mature_market_erp' WHERE observation_id=1`,
		"zero tenor":              `UPDATE market.yield_curve_point SET tenor_months=0`,
		"currency format":         `UPDATE market.yield_curve_point SET currency='China'`,
		"duplicate curve":         strings.Replace(yield, "VALUES (1,", "VALUES (2,", 1),
		"zero fx":                 `UPDATE market.fx_rate SET value=0`,
		"same currency":           `UPDATE market.fx_rate SET base_currency='CNY'`,
		"duplicate fx":            strings.Replace(fx, "VALUES (1,", "VALUES (2,", 1),
		"negative sample":         `UPDATE reference.industry_stat SET sample_count=-1`,
		"duplicate industry":      strings.Replace(industry, "VALUES (1,", "VALUES (2,", 1),
	}
	for name, q := range rejects {
		t.Run(name, func(t *testing.T) {
			_, err := db.ExecContext(ctx, q)
			if err == nil || !strings.Contains(strings.ToLower(err.Error()), "constraint") {
				t.Fatalf("want constraint rejection, got %v", err)
			}
		})
	}
	// A correction is a separate version; equal keys in different releases coexist.
	exec(strings.Replace(strings.Replace(release, "VALUES (1,", "VALUES (2,", 1), "repeat('a',64)", "repeat('b',64)", 1))
	exec(`UPDATE meta.dataset_release SET supersedes_release_id=1 WHERE release_id=2`)
	exec(`INSERT INTO meta.dataset_release_artifact VALUES (2,1,'data')`)
	exec(strings.Replace(country, "VALUES (1,1,", "VALUES (3,2,", 1))
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if err := Apply(ctx, db); err != nil {
		t.Fatal(err)
	}
	var versions, countries, zeros, missing, legacy int
	err = db.QueryRowContext(ctx, `SELECT
 (SELECT count(*) FROM meta.dataset_release),
 (SELECT count(*) FROM reference.country_risk),
 (SELECT count(*) FROM reference.country_risk WHERE value=0 AND value_status='reported'),
 (SELECT count(*) FROM reference.country_risk WHERE value IS NULL AND value_status='missing'),
 (SELECT count(*) FROM meta.checkpoint WHERE checkpoint_value='v26')`).Scan(&versions, &countries, &zeros, &missing, &legacy)
	if err != nil {
		t.Fatal(err)
	}
	if versions != 2 || countries != 3 || zeros != 2 || missing != 1 || legacy != 1 {
		t.Fatalf("persisted counts: %d %d %d %d %d", versions, countries, zeros, missing, legacy)
	}
}
