package main

import (
	"os"
	"path/filepath"
	"testing"

	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestStandardFieldsPublication(t *testing.T) {
	ctx := t.Context()
	base := filepath.Join(t.TempDir(), "base.duckdb")
	candidate := base + ".candidate"
	must := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	db, err := openSchema44(ctx, base)
	must(err)
	raw, err := duck.Read("045_asset_disposal_cash.sql")
	must(err)
	_, err = db.ExecContext(ctx, string(raw))
	must(err)
	_, err = db.ExecContext(ctx, `INSERT INTO meta.schema_version(version,description) VALUES(45,'045_asset_disposal_cash.sql');
 INSERT INTO fundamental.fact(instrument_id,canonical_field,report_period,announcement_time,period_type,statement_scope,unit,value,primary_source,source_provider_field,provider_code,source_filing_id,revision_key,normalization_rule,materializer_version)
 VALUES(1,'total_shares','2026-06-30','2026-08-31','H1','provider_default','share',123,'tdx','FN238','300866',1,'test','test','test')`)
	must(err)
	must(db.Close())
	b, err := os.ReadFile(base)
	must(err)
	must(os.WriteFile(candidate, b, 0600))
	db, err = duck.OpenAndMigrate(ctx, candidate)
	must(err)
	must(db.Close())
	args := []string{base, candidate, "--standard-fields"}
	must(run(args))
	db, err = duck.Open(ctx, candidate)
	must(err)
	_, err = db.ExecContext(ctx, `UPDATE fundamental.fact SET value=124`)
	must(err)
	must(db.Close())
	if run(args) == nil {
		t.Fatal("changed standard value accepted")
	}
	db, err = duck.Open(ctx, candidate)
	must(err)
	_, err = db.ExecContext(ctx, `UPDATE fundamental.fact SET value=123`)
	must(err)
	must(db.Close())
	bh, err := digest(base)
	must(err)
	ch, err := digest(candidate)
	must(err)
	must(run(append(args, "--publish", "--base-sha256", bh, "--candidate-sha256", ch)))
	backup, err := digest(base + ".pre-standard-fields-20260918")
	must(err)
	if backup != bh {
		t.Fatal("backup changed")
	}
	db, err = duck.OpenReadOnly(ctx, base)
	must(err)
	defer db.Close()
	var kind string
	var value int
	must(db.QueryRowContext(ctx, `SELECT period_type,value FROM fundamental.fact`).Scan(&kind, &value))
	if kind != "instant" || value != 123 {
		t.Fatal(kind, value)
	}
}
