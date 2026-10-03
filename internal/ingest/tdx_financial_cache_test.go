package ingest

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/binary"
	"fmt"
	"github.com/yinhm/alphalake/internal/domain"
	financial "github.com/yinhm/alphalake/internal/source/tdx/financial"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
	"math"
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
	// A registered historical cache remains usable when the retained manifest
	// describes a newer version; the checkpoint must still describe local bytes.
	seed.packageBytes = []byte("new unavailable version")
	_, manifest, err := seed.ProfessionalFinancialFileList(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "tdx-cache", "gpcw.txt"), manifest, 0600); err != nil {
		t.Fatal(err)
	}
	result, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, &noNetworkFinancialSource{seed}, root, TDXProfessionalFinancialOptions{Offline: true})
	if err != nil || result.RecordsInserted != 0 || result.Skipped != 1 || result.CacheFallbacks != 2 {
		t.Fatal(result, err)
	}
}

func TestFinancialInvalidPackagesPreserveFactsAndCheckpoint(t *testing.T) {
	for _, kind := range []string{"unregistered_cache", "wrong_period", "empty_package"} {
		t.Run(kind, func(t *testing.T) {
			ctx := t.Context()
			root := t.TempDir()
			t.Setenv("ALPHALAKE_WORKSPACE", root)
			path := filepath.Join(root, "main.duckdb")
			db, err := store.OpenInitialized(ctx, path)
			if err != nil {
				t.Fatal(err)
			}
			defer func() { db.Close() }()
			source := &realDuplicateFinancialSource{fakeProfessionalFinancialSource{
				packageBytes: financialCacheTestPackage(t, 20260630, false, 100),
				instruments:  []domain.InstrumentObservation{observation(domain.InstrumentEquity, "XSHG", "样本", "sh600001")},
			}}
			if _, err = SyncTDXProfessionalFinancial(ctx, db, source, root); err != nil {
				t.Fatal(err)
			}
			if _, err = MaterializeProviderFundamentals(ctx, db, "tdx"); err != nil {
				t.Fatal(err)
			}
			checkpointKey := "package:include-bse=false:gpcw20260630.zip"
			before, _, err := store.GetCheckpoint(ctx, db, "tdx", tdxProfessionalFinancialDataset, checkpointKey)
			if err != nil {
				t.Fatal(err)
			}
			options := TDXProfessionalFinancialOptions{}
			switch kind {
			case "unregistered_cache":
				// CRC-valid, structurally valid ZIP with a changed financial amount.
				options.Offline = true
				err = os.WriteFile(filepath.Join(root, "tdx-cache", "gpcw20260630.zip"), financialCacheTestPackage(t, 20260630, false, 999), 0600)
			case "wrong_period":
				source.packageBytes = financialCacheTestPackage(t, 20260331, false, 999)
			case "empty_package":
				source.packageBytes = financialCacheTestPackage(t, 20260630, true, 0)
			}
			if err != nil {
				t.Fatal(err)
			}
			result, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
			if err == nil || result.RecordsInserted != 0 || result.Packages != 0 {
				t.Fatalf("invalid package accepted: %+v %v", result, err)
			}
			wantStatus := store.IngestRunFailed
			if options.Offline {
				wantStatus = store.IngestRunPartial // explicit retained-source fallback
			}
			db.Close()
			db, err = store.OpenInitialized(ctx, path)
			if err != nil {
				t.Fatal(err)
			}
			after, found, err := store.GetCheckpoint(ctx, db, "tdx", tdxProfessionalFinancialDataset, checkpointKey)
			if err != nil || !found || after != before {
				t.Fatalf("checkpoint changed: %q %q %v", before, after, err)
			}
			var count int
			var value float64
			if err = db.QueryRowContext(ctx, "SELECT count(*),SUM(revenue) FROM fundamental.statement_snapshot").Scan(&count, &value); err != nil || count != 1 || value != 100 {
				t.Fatalf("trusted facts changed: %d %v %v", count, value, err)
			}
			var status string
			if err = db.QueryRowContext(ctx, "SELECT status FROM meta.ingest_run WHERE ingest_run_id=?", result.RunID).Scan(&status); err != nil || status != wantStatus {
				t.Fatalf("failure not retained after reopen: %s %v", status, err)
			}
		})
	}
}

func financialCacheTestPackage(t *testing.T, report uint32, empty bool, revenue float32) []byte {
	t.Helper()
	count := 1
	if empty {
		count = 0
	}
	dat := make([]byte, 20+count*(11+584*4))
	binary.LittleEndian.PutUint32(dat[2:6], report)
	binary.LittleEndian.PutUint16(dat[6:8], uint16(count))
	binary.LittleEndian.PutUint32(dat[12:16], 584*4)
	if count > 0 {
		copy(dat[20:26], "600001")
		binary.LittleEndian.PutUint32(dat[27:31], 31)
		catalog, err := financial.FieldCatalog()
		if err != nil {
			t.Fatal(err)
		}
		for _, field := range catalog {
			if field.Name == "revenue" {
				binary.LittleEndian.PutUint32(dat[31+(field.Index-1)*4:], math.Float32bits(revenue))
			}
		}
	}
	var raw bytes.Buffer
	w := zip.NewWriter(&raw)
	f, err := w.Create("gpcw20260630.dat")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = f.Write(dat); err != nil {
		t.Fatal(err)
	}
	if err = w.Close(); err != nil {
		t.Fatal(err)
	}
	return raw.Bytes()
}
