package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/yinhm/alphalake/internal/ingest"
	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestDisposalPublication(t *testing.T) {
	ctx := t.Context()
	root := t.TempDir()
	base := filepath.Join(root, "base.duckdb")
	candidate := filepath.Join(root, "candidate.duckdb")
	must := func(e error) {
		t.Helper()
		if e != nil {
			t.Fatal(e)
		}
	}
	read := func(p string) []byte { t.Helper(); b, e := os.ReadFile(filepath.Join("../..", p)); must(e); return b }
	db, e := openSchema44(ctx, base)
	must(e)
	var receipts map[string]ingest.ArchivedCNINFODocument
	must(json.Unmarshal(read("internal/ingest/testdata/anker-valuation-2026/reports.json"), &receipts))
	ids := []string{"1224614835", "1225090973", "1225533054"}
	periods := []string{"2025-06-30", "2025-12-31", "2026-06-30"}
	for i, id := range ids {
		kind := "semiannual"
		if i == 1 {
			kind = "annual"
		}
		var filing int64
		must(db.QueryRowContext(ctx, `INSERT INTO fundamental.filing(instrument_id,source,source_filing_id,provider_code,report_period,announcement_time,source_url,filing_type) VALUES (1,'cninfo',?,'300866',CAST(? AS DATE),'2026-08-31 16:00:00+00',?,?) RETURNING filing_id`, id, periods[i], receipts[id].URL, kind).Scan(&filing))
		value := 0
		if i == 2 {
			value = 17350
		}
		_, e = db.ExecContext(ctx, `INSERT INTO fundamental.provider_fact(instrument_id,source,report_period,provider_code,provider_field,value,revision_key) VALUES(1,'tdx',CAST(? AS DATE),'300866','FN110',?,?)`, periods[i], value, id)
		must(e)
		_, e = db.ExecContext(ctx, `INSERT INTO fundamental.provider_filing_link(provider_source,provider_revision_key,provider_artifact_id,provider_code,report_period,instrument_id,filing_id,status,linker_version) VALUES('tdx',?,1,'300866',CAST(? AS DATE),1,?,'linked','test')`, id, periods[i], filing)
		must(e)
	}
	must(db.Close())
	b, e := os.ReadFile(base)
	must(e)
	must(os.WriteFile(candidate, b, 0600))
	db, e = duck.OpenAndMigrate(ctx, candidate)
	must(e)
	for i, id := range ids[:2] {
		r := receipts[id]
		r.Code = "300866"
		r.Period = periods[i]
		r.AnnouncementID = id
		_, e = ingest.ImportArchivedCNINFODocument(ctx, db, root, r, read("internal/ingest/testdata/anker-valuation-2026/"+id+".pdf"))
		must(e)
	}
	var notes []duck.ReviewedSupplement
	must(json.Unmarshal(read("valuation/research/disposal-cash-20260918/zero-supplements.json"), &notes))
	_, e = duck.ImportReviewedSupplements(ctx, db, notes)
	must(e)
	_, e = ingest.MaterializeProviderFundamentals(ctx, db, "tdx", "FN110")
	must(e)
	must(db.Close())
	args := []string{base, candidate, "--disposal-cash", "--raw-root", root}
	must(run(args))
	db, e = duck.Open(ctx, candidate)
	must(e)
	_, e = db.ExecContext(ctx, `UPDATE fundamental.fact SET value=value+1 WHERE source_provider_field='FN110'`)
	must(e)
	must(db.Close())
	if run(args) == nil {
		t.Fatal("changed source amount accepted")
	}
	db, e = duck.Open(ctx, candidate)
	must(e)
	_, e = db.ExecContext(ctx, `UPDATE fundamental.fact SET value=value-1 WHERE source_provider_field='FN110'`)
	must(e)
	must(db.Close())
	bh, e := digest(base)
	must(e)
	ch, e := digest(candidate)
	must(e)
	must(run(append(args, "--publish", "--base-sha256", bh, "--candidate-sha256", ch)))
	h, e := digest(base + ".pre-disposal-cash-20260918")
	must(e)
	if h != bh {
		t.Fatal("backup changed")
	}
	db, e = duck.OpenReadOnly(ctx, base)
	must(e)
	defer db.Close()
	version, e := duck.CurrentSchemaVersion(ctx, db)
	must(e)
	if version != 45 {
		t.Fatal(version)
	}
}
