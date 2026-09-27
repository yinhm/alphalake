package ingest

import (
	"github.com/yinhm/alphalake/internal/source/damodaran"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestNativeReferenceLocalPublication(t *testing.T) {
	python := os.Getenv("ALPHALAKE_TEST_PYTHON")
	if python == "" {
		t.Skip("CI supplies ALPHALAKE_TEST_PYTHON with xlrd")
	}
	ctx := t.Context()
	root := t.TempDir()
	path := filepath.Join(root, "main.duckdb")
	db, err := duckstore.OpenInitialized(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { db.Close() }()
	script, err := filepath.Abs("../../" + damodaran.NativeReferenceScript)
	if err != nil {
		t.Fatal(err)
	}
	for _, stem := range damodaran.NativeReferenceFiles {
		opts := ReferenceOptions{Python: python, Script: script, LocalPath: "../source/damodaran/testdata/native/" + stem + ".xls"}
		first, err := SyncNativeReference(ctx, db, root, stem, opts)
		if err != nil || !first.Inserted {
			t.Fatal(stem, first, err)
		}
		opts.LocalPath = ""
		opts.Offline = true
		second, err := SyncNativeReference(ctx, db, root, stem, opts)
		if err != nil || second.Inserted || first.ReleaseID != second.ReleaseID {
			t.Fatal(stem, second, err)
		}
	}
	var v string
	var n int
	if err = db.QueryRow(`SELECT CAST(value AS VARCHAR) FROM reference.country_tax WHERE subject_code='CN'`).Scan(&v); err != nil || v != "0.250000000000" {
		t.Fatal(v, err)
	}
	if err = db.QueryRow(`SELECT count(*) FROM reference.country_tax WHERE subject_code='US' AND value IS NULL AND value_status='ambiguous' AND source_locator='Sheet1!B217;Sheet1!B246'`).Scan(&n); err != nil || n != 1 {
		t.Fatal(n, err)
	}
	if _, err = duckstore.NativeReferenceExport(ctx, db, time.Now().UTC()); err == nil || !strings.Contains(err.Error(), "missing published native reference") {
		t.Fatal("missing ERP/company publications must be diagnosed", err)
	}
	if _, err = db.Exec(`UPDATE reference.industry_stat SET value=value+0.01 WHERE sample_region='us' AND metric_code='cost_of_debt_pretax'`); err != nil {
		t.Fatal(err)
	}
	_, err = SyncNativeReference(ctx, db, root, "wacc", ReferenceOptions{Python: python, Script: script, Offline: true})
	if err == nil {
		t.Fatal("changed published values accepted")
	}
	// One-time upgrade keeps existing observations; no old-schema runtime path.
	if _, err = db.Exec(`DROP TABLE reference.country_tax; DELETE FROM meta.schema_version; INSERT INTO meta.schema_version(version,description) VALUES (53,'upgrade fixture')`); err != nil {
		t.Fatal(err)
	}
	var before int
	db.QueryRow(`SELECT count(*) FROM reference.industry_stat`).Scan(&before)
	if err = duckstore.UpgradeNativeReferences(ctx, db); err != nil {
		t.Fatal(err)
	}
	db.Close()
	db, err = duckstore.OpenInitialized(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if err = db.QueryRow(`SELECT count(*) FROM reference.industry_stat`).Scan(&n); err != nil || n != before {
		t.Fatal(n, before, err)
	}
	if err = duckstore.UpgradeNativeReferences(ctx, db); err == nil {
		t.Fatal("upgrade replay should reject")
	}
}
