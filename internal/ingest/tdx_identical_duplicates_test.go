package ingest

import (
	"context"
	"github.com/yinhm/alphalake/internal/domain"
	tdxsource "github.com/yinhm/alphalake/internal/source/tdx"
	financial "github.com/yinhm/alphalake/internal/source/tdx/financial"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"
)

type realDuplicateFinancialSource struct {
	fakeProfessionalFinancialSource
}

func (f *realDuplicateFinancialSource) NormalizeProfessionalFinancialPackage(entry financial.FileEntry, raw []byte, id int64) ([]domain.ProviderFinancialRecord, error) {
	return new(tdxsource.Client).NormalizeProfessionalFinancialPackage(entry, raw, id)
}

func TestRealIdenticalFinancialDuplicatesPreserveRawAndReplay(t *testing.T) {
	ctx := context.Background()
	db, err := store.OpenInitialized(ctx, filepath.Join(t.TempDir(), "duplicates.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	raw, err := os.ReadFile("testdata/tdx-identical-duplicates-2026/gpcw20260630.zip")
	if err != nil {
		t.Fatal(err)
	}
	source := &realDuplicateFinancialSource{fakeProfessionalFinancialSource{packageBytes: raw, instruments: []domain.InstrumentObservation{
		observation(domain.InstrumentEquity, "XSHE", "重复样本1", "sz301192"), observation(domain.InstrumentEquity, "XSHE", "重复样本2", "sz301321"), observation(domain.InstrumentEquity, "XSHG", "对照", "sh600519"),
	}}}
	rows, err := source.NormalizeProfessionalFinancialPackage(financial.FileEntry{Filename: "gpcw20260630.zip"}, raw, 1)
	if err != nil || len(rows) != 5 {
		t.Fatalf("raw records=%d %v", len(rows), err)
	}
	root := filepath.Join(t.TempDir(), "raw")
	t.Setenv("ALPHALAKE_WORKSPACE", root)
	options := TDXProfessionalFinancialOptions{MaxPackages: 1, Now: func() time.Time { return time.Date(2026, 9, 9, 0, 0, 0, 0, time.UTC) }}
	result, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil || result.RecordsInserted != 3 || result.Unresolved != 0 {
		t.Fatalf("%+v %v", result, err)
	}
	result, err = SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil || result.RecordsInserted != 0 || source.packageCalls != 1 {
		t.Fatalf("replay %+v calls=%d %v", result, source.packageCalls, err)
	}
	for _, marker := range []bool{false, true} {
		bad := append([]domain.ProviderFinancialRecord(nil), rows...)
		if marker {
			bad[1].MarketMarker++
		} else {
			bad[1].ProviderFields = append([]domain.ProviderFloat32(nil), rows[1].ProviderFields...)
			bad[1].ProviderFields[0].Bits ^= 1
			bad[1].ProviderFields[0].Value = float64(math.Float32frombits(bad[1].ProviderFields[0].Bits))
		}
		kept, states, err := resolveProviderFinancialRecords(ctx, db, bad)
		if err != nil || len(kept) != 2 || len(states) != 3 || states[0].InstrumentID != 0 || states[0].Reason == "" {
			t.Fatalf("conflicting security not isolated: %d %+v %v", len(kept), states, err)
		}
	}
}

func TestUnmappedDuplicateDifferenceDoesNotWithholdStandardFields(t *testing.T) {
	ctx := context.Background()
	db, err := store.OpenInitialized(ctx, filepath.Join(t.TempDir(), "duplicate-scope.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	raw, err := os.ReadFile("testdata/tdx-identical-duplicates-2026/gpcw20260630.zip")
	if err != nil {
		t.Fatal(err)
	}
	source := &realDuplicateFinancialSource{fakeProfessionalFinancialSource{packageBytes: raw, instruments: []domain.InstrumentObservation{
		observation(domain.InstrumentEquity, "XSHE", "样本", "sz301192"), observation(domain.InstrumentEquity, "XSHE", "样本2", "sz301321"), observation(domain.InstrumentEquity, "XSHG", "对照", "sh600519"),
	}}}
	if _, err := store.UpsertInstruments(ctx, db, source.instruments); err != nil {
		t.Fatal(err)
	}
	rows, err := source.NormalizeProfessionalFinancialPackage(financial.FileEntry{Filename: "gpcw20260630.zip"}, raw, 1)
	if err != nil {
		t.Fatal(err)
	}
	catalogue, err := financial.FieldCatalog()
	if err != nil {
		t.Fatal(err)
	}
	excluded, err := store.UnmappedDuplicatePositions(ctx, db)
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range catalogue {
		if f.ValueKind != "ratio" && excluded[f.Index] {
			t.Fatal("non-ratio excluded", f.Name)
		}
	}
	position := 0
	for _, f := range catalogue {
		if f.Name == "reported_dividend_payout_ratio" {
			position = f.Index - 1
		}
	}
	if position <= 0 {
		t.Fatal("missing source definition")
	}
	rows[1].ProviderFields = append([]domain.ProviderFloat32(nil), rows[1].ProviderFields...)
	rows[1].ProviderFields[position].Bits ^= 1
	rows[1].ProviderFields[position].Value = float64(math.Float32frombits(rows[1].ProviderFields[position].Bits))
	kept, states, err := resolveProviderFinancialRecords(ctx, db, rows)
	if err != nil || len(kept) != 3 || len(states) != 3 {
		t.Fatalf("%d %+v %v", len(kept), states, err)
	}
	if _, err := db.ExecContext(ctx, `INSERT INTO fundamental.provider_field(source,provider_field,canonical_field,display_name,unit,value_kind,period_basis,value_multiplier,zero_policy) VALUES('tdx','FN337','reported_dividend_payout_ratio','source maintenance','percent','ratio','ytd',1,'reject')`); err != nil {
		t.Fatal(err)
	}
	kept, _, err = resolveProviderFinancialRecords(ctx, db, rows)
	if err != nil || len(kept) != 2 {
		t.Fatalf("new mapping did not restore conflict: %d %v", len(kept), err)
	}
}
