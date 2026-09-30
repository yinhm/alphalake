package ingest

import (
	"context"
	"fmt"
	"github.com/yinhm/alphalake/internal/domain"
	financial "github.com/yinhm/alphalake/internal/source/tdx/financial"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
	"os"
	"path/filepath"
	"testing"
	"time"
)

type failingCachedFinancialSource struct {
	*fakeProfessionalFinancialSource
	manifestDown, packageDown, masterDown bool
}

func (s *failingCachedFinancialSource) ProfessionalFinancialFileList(ctx context.Context) ([]financial.FileEntry, []byte, error) {
	if s.manifestDown {
		return nil, nil, fmt.Errorf("manifest offline")
	}
	return s.fakeProfessionalFinancialSource.ProfessionalFinancialFileList(ctx)
}
func (s *failingCachedFinancialSource) ProfessionalFinancialPackage(ctx context.Context, e financial.FileEntry) ([]byte, error) {
	if s.packageDown {
		return nil, fmt.Errorf("package offline")
	}
	return s.fakeProfessionalFinancialSource.ProfessionalFinancialPackage(ctx, e)
}
func (s *failingCachedFinancialSource) InstrumentSnapshot(ctx context.Context) (domain.InstrumentMasterSnapshot, error) {
	if s.masterDown {
		return domain.InstrumentMasterSnapshot{}, fmt.Errorf("master offline")
	}
	return s.fakeProfessionalFinancialSource.InstrumentSnapshot(ctx)
}
func TestFinancialCacheOfflineStaleAndTamper(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	t.Setenv("ALPHALAKE_WORKSPACE", root)
	db, e := store.OpenInitialized(ctx, filepath.Join(root, "main.duckdb"))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	s := &failingCachedFinancialSource{fakeProfessionalFinancialSource: &fakeProfessionalFinancialSource{instruments: []domain.InstrumentObservation{{Instrument: domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "sample"}, Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"}}}, packageBytes: []byte("original verified bytes"), recordCode: "600001"}}
	first, e := SyncTDXProfessionalFinancial(ctx, db, s, root)
	if e != nil || first.RecordsInserted != 1 {
		t.Fatal(first, e)
	}
	files, err := os.ReadDir(filepath.Join(root, "tdx-cache"))
	if err != nil || len(files) != 2 {
		t.Fatal("cache must contain only manifest and package", files, err)
	}
	for _, file := range files {
		if !file.Type().IsRegular() {
			t.Fatal("cache must contain regular files", file.Name())
		}
	}
	old, _, e := ReadFinancialCache(ctx, db, root, financial.FileEntry{Filename: "gpcw20260630.zip"})
	if e != nil {
		t.Fatal(e)
	}
	manifestPath := filepath.Join(root, "tdx-cache", "gpcw.txt")
	fixed := time.Unix(1234567890, 0)
	if e = os.Chtimes(manifestPath, fixed, fixed); e != nil {
		t.Fatal(e)
	}
	unchanged, e := SyncTDXProfessionalFinancial(ctx, db, s, root)
	if e != nil || unchanged.Skipped != 1 {
		t.Fatal(unchanged, e)
	}
	info, e := os.Stat(manifestPath)
	if e != nil || !info.ModTime().Equal(fixed) {
		t.Fatal("unchanged manifest rewritten", e)
	}
	s.manifestDown = true
	s.masterDown = true
	s.packageDown = true
	offline, e := SyncTDXProfessionalFinancial(ctx, db, s, root)
	if e != nil || offline.CacheFallbacks != 2 || offline.Skipped != 1 {
		t.Fatal(offline, e)
	}
	s.manifestDown = false
	s.masterDown = false
	s.packageBytes = []byte("new unavailable upstream version")
	stale, e := SyncTDXProfessionalFinancial(ctx, db, s, root)
	if e != nil || stale.CacheFallbacks != 1 || stale.RecordsInserted != 0 {
		t.Fatal(stale, e)
	}
	checkpoint, found, e := store.GetCheckpoint(ctx, db, "tdx", tdxProfessionalFinancialDataset, "package:include-bse=false:gpcw20260630.zip")
	if e != nil || !found || checkpoint != old.MD5 {
		t.Fatal(checkpoint, found, e)
	}
	var status string
	if e = db.QueryRowContext(ctx, "SELECT status FROM meta.ingest_run WHERE ingest_run_id=?", stale.RunID).Scan(&status); e != nil || status != store.IngestRunPartial {
		t.Fatal(status, e)
	}
	if e = os.WriteFile(filepath.Join(root, "tdx-cache", "gpcw20260630.zip"), []byte("tampered"), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = SyncTDXProfessionalFinancial(ctx, db, s, root); e == nil {
		t.Fatal("corrupt local cache accepted")
	}
}

type noNetworkFinancialSource struct {
	*fakeProfessionalFinancialSource
}

func (s *noNetworkFinancialSource) ProfessionalFinancialFileList(context.Context) ([]financial.FileEntry, []byte, error) {
	panic("offline called manifest network")
}
func (s *noNetworkFinancialSource) ProfessionalFinancialPackage(context.Context, financial.FileEntry) ([]byte, error) {
	panic("offline called package network")
}
func (s *noNetworkFinancialSource) InstrumentSnapshot(context.Context) (domain.InstrumentMasterSnapshot, error) {
	panic("offline called identities network")
}
func TestFinancialOfflineNeverCallsNetwork(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	t.Setenv("ALPHALAKE_WORKSPACE", root)
	db, err := store.OpenInitialized(ctx, filepath.Join(root, "main.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	seed := &fakeProfessionalFinancialSource{instruments: []domain.InstrumentObservation{{Instrument: domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "sample"}, Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"}}}, packageBytes: []byte("original"), recordCode: "600001"}
	if _, err := SyncTDXProfessionalFinancial(ctx, db, seed, root); err != nil {
		t.Fatal(err)
	}
	// A local revision can differ from the saved upstream manifest: record actual
	// bytes, parse them, but never advance a checkpoint to the remote MD5.
	if err := os.WriteFile(filepath.Join(root, "tdx-cache", "gpcw20260630.zip"), []byte("offline revision"), 0600); err != nil {
		t.Fatal(err)
	}
	result, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, &noNetworkFinancialSource{seed}, root, TDXProfessionalFinancialOptions{Offline: true})
	if err != nil || result.RecordsInserted != 1 || result.CacheFallbacks != 2 {
		t.Fatal(result, err)
	}
}
