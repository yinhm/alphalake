package ingest

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestArchivedCNINFODocument(t *testing.T) {
	ctx := t.Context()
	root := t.TempDir()
	path := filepath.Join(root, "test.duckdb")
	db, err := duck.OpenInitialized(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { db.Close() }()
	raw, err := os.ReadFile("testdata/anker-valuation-2026/reports.json")
	if err != nil {
		t.Fatal(err)
	}
	var receipts map[string]ArchivedCNINFODocument
	if err = json.Unmarshal(raw, &receipts); err != nil {
		t.Fatal(err)
	}
	r := receipts["1225533054"]
	r.Code = "300866"
	r.Period = "2026-06-30"
	r.AnnouncementID = "1225533054"
	pdf, err := os.ReadFile("testdata/anker-valuation-2026/1225533054.pdf")
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.filing(instrument_id,source,source_filing_id,provider_code,report_period,announcement_time,source_url) VALUES (1,'cninfo',?,'300866','2026-06-30','2026-08-31 16:00:00+00',?)`, r.AnnouncementID, r.URL)
	if err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"hash", "identity", "url"} {
		bad := r
		switch kind {
		case "hash":
			bad.SHA256 = "bad"
		case "identity":
			bad.Code = "002032"
		case "url":
			bad.URL = "https://example.com/document.pdf"
		}
		if _, err = ImportArchivedCNINFODocument(ctx, db, root, bad, pdf); err == nil {
			t.Fatalf("accepted %s", kind)
		}
	}
	if ok, e := ImportArchivedCNINFODocument(ctx, db, root, r, pdf); e != nil || !ok {
		t.Fatalf("import %v %v", ok, e)
	}
	db.Close()
	db, err = duck.OpenInitialized(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if ok, e := ImportArchivedCNINFODocument(ctx, db, root, r, pdf); e != nil || ok {
		t.Fatalf("replay %v %v", ok, e)
	}
	if _, err = db.ExecContext(ctx, `UPDATE meta.artifact SET source='mirror' WHERE source='cninfo'`); err != nil {
		t.Fatal(err)
	}
	if _, err = ImportArchivedCNINFODocument(ctx, db, root, r, pdf); err == nil {
		t.Fatal("accepted altered provenance")
	}
}
