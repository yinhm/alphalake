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
	db, err := store.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "duplicates.duckdb"))
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
	options := TDXProfessionalFinancialOptions{MaxPackages: 1, Now: func() time.Time { return time.Date(2026, 9, 9, 0, 0, 0, 0, time.UTC) }}
	result, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil || result.FactsInserted != 3*584 || result.Unresolved != 0 {
		t.Fatalf("%+v %v", result, err)
	}
	result, err = SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil || result.FactsInserted != 0 || source.packageCalls != 1 {
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
		if _, _, err = resolveProviderFinancialRecords(ctx, db, bad); err == nil {
			t.Fatal("accepted conflicting duplicate")
		}
	}
}
