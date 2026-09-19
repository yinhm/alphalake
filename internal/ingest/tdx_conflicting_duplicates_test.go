package ingest

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/domain"
	financial "github.com/yinhm/alphalake/internal/source/tdx/financial"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestRealConflictingSecurityIsolatedAndValuationBlocked(t *testing.T) {
	ctx := t.Context()
	dbPath := filepath.Join(t.TempDir(), "conflicts.duckdb")
	db, err := store.OpenInitialized(ctx, dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { db.Close() }()
	raw, err := os.ReadFile("testdata/tdx-conflicting-duplicates-2026/gpcw20251231.zip")
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprintf("%x", sha256.Sum256(raw)) != "c4fa96b14bb28c6b6c39dd9cb61e42060adc169d98c9c31357fb74c70fbcb4b1" {
		t.Fatal("sample hash")
	}
	source := &realDuplicateFinancialSource{fakeProfessionalFinancialSource{packageBytes: raw, packageFilename: "gpcw20251231.zip", instruments: []domain.InstrumentObservation{
		observation(domain.InstrumentEquity, "XSHE", "工控对照", "sz300124"), observation(domain.InstrumentEquity, "XSHE", "冲突证券", "sz300750"), observation(domain.InstrumentEquity, "XSHG", "食品对照", "sh603288"),
	}}}
	rows, err := source.NormalizeProfessionalFinancialPackage(financial.FileEntry{Filename: "gpcw20251231.zip"}, raw, 1)
	if err != nil || len(rows) != 4 {
		t.Fatalf("rows=%d %v", len(rows), err)
	}
	if rows[1].ProviderCode != "300750" || rows[2].ProviderCode != "300750" || rows[1].ProviderFields[336].Bits != 1096464466 || rows[2].ProviderFields[336].Bits != 1106247680 {
		t.Fatal("real FN337 disagreement")
	}
	for i, a := range rows[1].ProviderFields {
		if i != 336 && a.Bits != rows[2].ProviderFields[i].Bits {
			t.Fatalf("unexpected additional difference FN%d", i+1)
		}
	}
	root := filepath.Join(t.TempDir(), "raw")
	day := time.Date(2026, 9, 9, 0, 0, 0, 0, time.UTC)
	options := TDXProfessionalFinancialOptions{MaxPackages: 1, Now: func() time.Time { return day }}
	result, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil || result.Packages != 1 || result.FactsInserted != 2*584 || result.Unresolved != 1 {
		t.Fatalf("%+v %v", result, err)
	}
	var badFacts int
	if err = db.QueryRow(`SELECT count(*) FROM fundamental.provider_fact WHERE provider_code='300750'`).Scan(&badFacts); err != nil || badFacts != 0 {
		t.Fatalf("conflicting values published: %d %v", badFacts, err)
	}
	if _, found, err := store.GetCheckpoint(ctx, db, "tdx", "professional_financial", "package:gpcw20251231.zip"); err != nil || found {
		t.Fatalf("conflict checkpointed: %v %v", found, err)
	}
	result, err = SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil || result.FactsInserted != 0 || result.Unresolved != 1 || source.packageCalls != 1 {
		t.Fatalf("replay %+v calls=%d %v", result, source.packageCalls, err)
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = store.Open(ctx, dbPath)
	if err != nil {
		t.Fatal(err)
	}
	pending, err := store.ListProviderFinancialResolutions(ctx, db, "pending", 10)
	if err != nil || len(pending) != 1 || !strings.Contains(pending[0].Reason, "FN337") {
		t.Fatalf("persisted conflict %+v %v", pending, err)
	}
	end := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	check := func(asof time.Time, want int) {
		t.Helper()
		export, e := store.ExportValuationData(ctx, db, "300750", end, asof)
		if e != nil {
			t.Fatal(e)
		}
		var conflicts []map[string]any
		if e = json.Unmarshal(export["source_conflicts"].(json.RawMessage), &conflicts); e != nil || len(conflicts) != want {
			t.Fatalf("export conflicts %v %v", conflicts, e)
		}
		scan, e := store.ExportValuationReadiness(ctx, db, end, asof)
		if e != nil {
			t.Fatal(e)
		}
		if scan["financial_status_counts"].(map[string]int)["blocked_source_record_conflict"] != want {
			t.Fatal(scan)
		}
	}
	check(day.Add(-time.Second), 0)
	check(day, 1)
	// 后来的无冲突状态是合成恢复对照，绝不作为该公司的真实修订财务值。
	newer, e := artifact.Persist(ctx, db, root, artifact.Input{Source: "tdx", Dataset: "professional_financial", SourceLocator: "synthetic-recovery", FetchedAt: day.Add(time.Hour), MediaType: "application/octet-stream", ParserVersion: "test", Content: []byte("synthetic later resolved observation")})
	if e != nil {
		t.Fatal(e)
	}
	_, e = store.ApplyProviderFinancialResolutions(ctx, db, 1, []store.ProviderFinancialResolutionInput{{ArtifactID: newer.ArtifactID, Source: "tdx", SourceFile: "gpcw20251231.zip", ReportPeriod: rows[1].ReportPeriod, ProviderCode: "300750", InstrumentID: 2, IdentifierValue: "sz300750"}})
	if e != nil {
		t.Fatal(e)
	}
	check(day, 1)
	check(day.Add(time.Hour), 0)
}
