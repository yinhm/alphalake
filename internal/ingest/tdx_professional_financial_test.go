package ingest

import (
	"context"
	"crypto/md5"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	tdxfinancial "github.com/yinhm/alphalake/internal/source/tdx/financial"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestFinancialProgressKeepsCurrentFailure(t *testing.T) {
	summary := TDXProfessionalFinancialSummary{Failures: []TDXProfessionalFinancialFailure{{Package: "bad.zip", Err: fmt.Errorf("capacity exhausted")}}}
	var progress TDXProfessionalFinancialProgress
	options := TDXProfessionalFinancialOptions{OnProgress: func(p TDXProfessionalFinancialProgress) { progress = p }}
	reportProfessionalFinancialProgress(options, summary, 1, "bad.zip")
	if progress.Error != "capacity exhausted" || progress.Failures != 1 {
		t.Fatal(progress)
	}
	reportProfessionalFinancialProgress(options, summary, 2, "good.zip")
	if progress.Error != "" {
		t.Fatal("previous failure incorrectly attributed to current package", progress)
	}
}

type fakeProfessionalFinancialSource struct {
	instruments       []domain.InstrumentObservation
	packageBytes      []byte
	packageFilename   string
	packageCalls      int
	futurePlaceholder bool
	recordCode        string
	marketMarker      byte
}

func (f *fakeProfessionalFinancialSource) InstrumentSnapshot(context.Context) (domain.InstrumentMasterSnapshot, error) {
	return testMasterSnapshot(time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC), true, f.instruments), nil
}

func (f *fakeProfessionalFinancialSource) ProfessionalFinancialFileList(context.Context) ([]tdxfinancial.FileEntry, []byte, error) {
	sum := md5.Sum(f.packageBytes)
	entry := tdxfinancial.FileEntry{Filename: "gpcw20260630.zip", MD5: hex.EncodeToString(sum[:]), Size: int64(len(f.packageBytes))}
	if f.packageFilename != "" {
		entry.Filename = f.packageFilename
	}
	manifest := []byte(fmt.Sprintf("%s,%s,%d\n", entry.Filename, entry.MD5, entry.Size))
	entries := []tdxfinancial.FileEntry{entry}
	if f.futurePlaceholder {
		entries = append(entries, tdxfinancial.FileEntry{Filename: "gpcw20260930.zip", MD5: "00000000000000000000000000000000", Size: 164})
		manifest = append(manifest, []byte("gpcw20260930.zip,00000000000000000000000000000000,164\n")...)
	}
	return entries, manifest, nil
}

func (f *fakeProfessionalFinancialSource) ProfessionalFinancialPackage(_ context.Context, entry tdxfinancial.FileEntry) ([]byte, error) {
	if entry.Filename != f.packageFilename && !(f.packageFilename == "" && entry.Filename == "gpcw20260630.zip") {
		return nil, fmt.Errorf("unexpected package %s", entry.Filename)
	}
	f.packageCalls++
	return append([]byte(nil), f.packageBytes...), nil
}

func (f *fakeProfessionalFinancialSource) NormalizeProfessionalFinancialPackage(entry tdxfinancial.FileEntry, _ []byte, artifactID int64) ([]domain.ProviderFinancialRecord, error) {
	return []domain.ProviderFinancialRecord{{
		Provider: "tdx", ProviderCode: f.recordCode, MarketMarker: f.marketMarker,
		ReportPeriod:   time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC),
		ProviderFields: []domain.ProviderFloat32{{Bits: 0x3f800000, Value: 1}, {Bits: 0x40000000, Value: 2}},
		SourceFile:     entry.Filename, ArtifactID: artifactID,
	}}, nil
}

func TestSyncTDXProfessionalFinancialPersistsArtifactFactsAndCheckpoint(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenInitialized(ctx, filepath.Join(t.TempDir(), "financial.duckdb"))
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
		packageBytes: []byte("zip!"), recordCode: "600001", marketMarker: 7, futurePlaceholder: true,
	}
	now := time.Date(2026, 9, 4, 12, 0, 0, 0, time.UTC)
	options := TDXProfessionalFinancialOptions{MaxPackages: 1, Now: func() time.Time { return now }}
	first, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if first.Listed != 2 || first.Selected != 1 || first.Packages != 1 || first.FactsAttempted != 2 || first.FactsInserted != 2 || first.Unresolved != 0 || source.packageCalls != 1 {
		t.Fatalf("first=%#v calls=%d", first, source.packageCalls)
	}
	var facts, artifacts, resolved int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.provider_fact`).Scan(&facts); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.artifact WHERE dataset='professional_financial'`).Scan(&artifacts); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.provider_record_resolution WHERE status='resolved'`).Scan(&resolved); err != nil {
		t.Fatal(err)
	}
	if facts != 2 || artifacts != 2 || resolved != 1 { // manifest + package
		t.Fatalf("facts/artifacts/resolved=%d/%d/%d", facts, artifacts, resolved)
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
	db, err := duckstore.OpenInitialized(ctx, filepath.Join(t.TempDir(), "unresolved.duckdb"))
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
		packageBytes: []byte("zip!"), recordCode: "600999", marketMarker: 9,
	}
	options := TDXProfessionalFinancialOptions{MaxPackages: 1, Now: func() time.Time {
		return time.Date(2026, 9, 4, 12, 0, 0, 0, time.UTC)
	}}
	first, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if first.Unresolved != 1 || first.FactsAttempted != 0 || source.packageCalls != 1 {
		t.Fatalf("first=%#v calls=%d", first, source.packageCalls)
	}
	second, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if second.Unresolved != 1 || source.packageCalls != 1 {
		t.Fatalf("second=%#v calls=%d, want local artifact retry", second, source.packageCalls)
	}
	var checkpoints, pending int
	var artifactID int64
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.checkpoint WHERE dataset='professional_financial' AND checkpoint_key LIKE 'package:%'`).Scan(&checkpoints); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `
		SELECT count(*), min(artifact_id)
		FROM fundamental.provider_record_resolution
		WHERE provider_code='600999' AND status='pending'
	`).Scan(&pending, &artifactID); err != nil {
		t.Fatal(err)
	}
	if checkpoints != 0 || pending != 1 {
		t.Fatalf("checkpoints/pending=%d/%d, unresolved package must remain retryable", checkpoints, pending)
	}
	if artifactID <= 0 {
		t.Fatal("pending resolution has no artifact ID")
	}
}

func TestCorruptRetainedFinancialPackageUsesCacheThenRedownloads(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenInitialized(ctx, filepath.Join(t.TempDir(), "corrupt-retained.duckdb"))
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
		packageBytes: []byte("zip!"), recordCode: "600999", marketMarker: 9,
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

	var localPath string
	if err := db.QueryRowContext(ctx, `
		SELECT local_path FROM meta.artifact
		WHERE source='tdx' AND dataset='professional_financial'
		  AND source_locator='tdxfin/gpcw20260630.zip'
		ORDER BY artifact_id DESC LIMIT 1
	`).Scan(&localPath); err != nil {
		t.Fatal(err)
	}
	fullPath := filepath.Join(root, filepath.FromSlash(localPath))
	if err := os.WriteFile(fullPath, []byte("bad!"), 0o644); err != nil {
		t.Fatal(err)
	}

	second, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatalf("second sync after corruption: %v", err)
	}
	if second.Unresolved != 1 || source.packageCalls != 1 {
		t.Fatalf("second=%#v calls=%d, want verified local cache repair", second, source.packageCalls)
	}
	got, err := os.ReadFile(fullPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(source.packageBytes) {
		t.Fatalf("repaired retained bytes=%q, want %q", got, source.packageBytes)
	}
	for _, path := range []string{fullPath, filepath.Join(root, "tdx-cache", "gpcw20260630.zip")} {
		if err := os.WriteFile(path, []byte("bad!"), 0644); err != nil {
			t.Fatal(err)
		}
	}
	third, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil || third.Unresolved != 1 || source.packageCalls != 2 {
		t.Fatalf("third=%#v calls=%d err=%v, want provider repair", third, source.packageCalls, err)
	}
	for _, path := range []string{fullPath, filepath.Join(root, "tdx-cache", "gpcw20260630.zip")} {
		got, err := os.ReadFile(path)
		if err != nil || string(got) != string(source.packageBytes) {
			t.Fatal("repair failed", path, err)
		}
	}

}

func TestAcknowledgedFinancialRecordAllowsPackageCompletion(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenInitialized(ctx, filepath.Join(t.TempDir(), "acknowledged.duckdb"))
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
		packageBytes: []byte("zip!"), recordCode: "870001", marketMarker: 3,
	}
	options := TDXProfessionalFinancialOptions{MaxPackages: 1, Now: func() time.Time {
		return time.Date(2026, 9, 4, 12, 0, 0, 0, time.UTC)
	}}
	first, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if first.Unresolved != 1 {
		t.Fatalf("first=%#v, want one pending unresolved record", first)
	}
	var artifactID int64
	if err := db.QueryRowContext(ctx, `
		SELECT artifact_id FROM fundamental.provider_record_resolution
		WHERE provider_code='870001' AND status='pending'
	`).Scan(&artifactID); err != nil {
		t.Fatal(err)
	}
	changed, err := duckstore.AcknowledgeProviderFinancialResolution(ctx, db, artifactID, "870001", "historical identity unavailable after manual review")
	if err != nil || !changed {
		t.Fatalf("acknowledge changed=%v err=%v", changed, err)
	}

	second, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if second.Unresolved != 0 || second.Acknowledged != 1 || source.packageCalls != 1 {
		t.Fatalf("second=%#v calls=%d", second, source.packageCalls)
	}
	var checkpoints int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.checkpoint WHERE dataset='professional_financial' AND checkpoint_key LIKE 'package:%'`).Scan(&checkpoints); err != nil {
		t.Fatal(err)
	}
	if checkpoints != 1 {
		t.Fatalf("checkpoints=%d, acknowledged package should complete", checkpoints)
	}
	third, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if third.Skipped != 1 {
		t.Fatalf("third=%#v, want completed package checkpoint skip", third)
	}
}

func TestFinancialDefaultScopeAndExplicitReplay(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenInitialized(ctx, filepath.Join(t.TempDir(), "scope.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = duckstore.UpsertInstrument(ctx, db, domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XBSE", Currency: "CNY", Name: "Beijing"}, domain.Identifier{Provider: "tdx", Type: "symbol", Value: "bj920001"})
	if err != nil {
		t.Fatal(err)
	}
	source := &fakeProfessionalFinancialSource{instruments: []domain.InstrumentObservation{{Instrument: domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Shanghai"}, Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"}}}, packageBytes: []byte("scope zip"), recordCode: "920001"}
	root := filepath.Join(t.TempDir(), "raw")
	first, err := SyncTDXProfessionalFinancial(ctx, db, source, root)
	if err != nil || first.FactsInserted != 0 || first.Unresolved != 0 {
		t.Fatal(first, err)
	}
	included, err := SyncTDXProfessionalFinancial(domain.WithBSE(ctx), db, source, root)
	if err != nil || included.FactsInserted != 2 || included.Skipped != 0 || source.packageCalls != 1 {
		t.Fatal(included, err, source.packageCalls)
	}
	replay, err := SyncTDXProfessionalFinancial(ctx, db, source, root)
	if err != nil || replay.Skipped != 1 || replay.FactsRemoved != 0 {
		t.Fatal(replay, err)
	}
}
