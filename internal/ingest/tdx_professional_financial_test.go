package ingest

import (
	"context"
	"crypto/md5"
	"encoding/hex"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	tdxfinancial "github.com/yinhm/alphalake/internal/source/tdx/financial"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type fakeProfessionalFinancialSource struct {
	instruments  []domain.InstrumentObservation
	packageBytes []byte
	packageCalls int
	recordSymbol string
}

func (f *fakeProfessionalFinancialSource) Instruments(context.Context) ([]domain.InstrumentObservation, error) {
	return f.instruments, nil
}

func (f *fakeProfessionalFinancialSource) ProfessionalFinancialFileList(context.Context) ([]tdxfinancial.FileEntry, []byte, error) {
	sum := md5.Sum(f.packageBytes)
	entry := tdxfinancial.FileEntry{Filename: "gpcw20260630.zip", MD5: hex.EncodeToString(sum[:]), Size: int64(len(f.packageBytes))}
	manifest := []byte(entry.Filename + "," + entry.MD5 + "," + "4\n")
	return []tdxfinancial.FileEntry{entry}, manifest, nil
}

func (f *fakeProfessionalFinancialSource) ProfessionalFinancialPackage(context.Context, tdxfinancial.FileEntry) ([]byte, error) {
	f.packageCalls++
	return append([]byte(nil), f.packageBytes...), nil
}

func (f *fakeProfessionalFinancialSource) NormalizeProfessionalFinancialPackage(entry tdxfinancial.FileEntry, _ []byte, artifactID int64) ([]domain.ProviderFinancialRecord, error) {
	return []domain.ProviderFinancialRecord{{
		Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: f.recordSymbol},
		ReportPeriod: time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC),
		ProviderFields: []domain.ProviderFloat32{{Bits: 0x3f800000, Value: 1}, {Bits: 0x40000000, Value: 2}},
		SourceFile: entry.Filename, ArtifactID: artifactID,
	}}, nil
}

func TestSyncTDXProfessionalFinancialPersistsArtifactFactsAndCheckpoint(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "financial.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	root := filepath.Join(t.TempDir(), "raw")
	source := &fakeProfessionalFinancialSource{
		instruments: []domain.InstrumentObservation{{
			Instrument: domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Test"},
			Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"},
		}},
		packageBytes: []byte("zip!"), recordSymbol: "sh600001",
	}
	now := time.Date(2026, 9, 4, 12, 0, 0, 0, time.UTC)
	options := TDXProfessionalFinancialOptions{MaxPackages: 1, Now: func() time.Time { return now }}
	first, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if first.Packages != 1 || first.Facts != 2 || first.Unresolved != 0 || source.packageCalls != 1 {
		t.Fatalf("first=%#v calls=%d", first, source.packageCalls)
	}
	var facts, artifacts int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.provider_fact`).Scan(&facts); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.artifact WHERE dataset='professional_financial'`).Scan(&artifacts); err != nil {
		t.Fatal(err)
	}
	if facts != 2 || artifacts != 2 { // manifest + package
		t.Fatalf("facts/artifacts=%d/%d", facts, artifacts)
	}

	second, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if second.Skipped != 1 || source.packageCalls != 1 {
		t.Fatalf("second=%#v calls=%d, want checkpoint skip without package fetch", second, source.packageCalls)
	}
}

func TestUnresolvedFinancialRecordRetriesFromLocalArtifactWithoutRedownload(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "unresolved.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	root := filepath.Join(t.TempDir(), "raw")
	source := &fakeProfessionalFinancialSource{
		instruments: []domain.InstrumentObservation{{
			Instrument: domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Current"},
			Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"},
		}},
		packageBytes: []byte("zip!"), recordSymbol: "sh600999", // deliberately absent from master
	}
	options := TDXProfessionalFinancialOptions{MaxPackages: 1, Now: func() time.Time {
		return time.Date(2026, 9, 4, 12, 0, 0, 0, time.UTC)
	}}
	first, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if first.Unresolved != 1 || source.packageCalls != 1 {
		t.Fatalf("first=%#v calls=%d", first, source.packageCalls)
	}
	second, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if second.Unresolved != 1 || source.packageCalls != 1 {
		t.Fatalf("second=%#v calls=%d, want local artifact retry", second, source.packageCalls)
	}
	var checkpoints int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.checkpoint WHERE dataset='professional_financial' AND checkpoint_key LIKE 'package:%'`).Scan(&checkpoints); err != nil {
		t.Fatal(err)
	}
	if checkpoints != 0 {
		t.Fatalf("checkpoints=%d, unresolved package must remain retryable", checkpoints)
	}
}
