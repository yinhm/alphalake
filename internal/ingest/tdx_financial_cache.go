package ingest

import (
	"bytes"
	"context"
	"crypto/md5"
	"crypto/sha256"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/yinhm/alphalake/internal/artifact"
	financial "github.com/yinhm/alphalake/internal/source/tdx/financial"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

// The cache is plain files. Upstream MD5/size verifies the current version;
// archived provenance in DuckDB verifies an older version used while offline.
func readFinancialCache(ctx context.Context, db *sql.DB, root string, expected financial.FileEntry) (financial.FileEntry, []byte, error) {
	filename := expected.Filename
	if filepath.Base(filename) != filename {
		return financial.FileEntry{}, nil, fmt.Errorf("invalid cache filename")
	}
	raw, err := os.ReadFile(filepath.Join(root, "tdx-cache", filename))
	if err != nil {
		return financial.FileEntry{}, nil, err
	}
	entry := expected
	entry.MD5 = fmt.Sprintf("%x", md5.Sum(raw))
	entry.Size = int64(len(raw))
	if sameFinancialEntry(entry, expected) {
		return entry, raw, nil
	}
	var known bool
	err = db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM meta.artifact WHERE source='tdx' AND dataset=? AND source_locator=? AND sha256=? AND content_length=?)`, tdxProfessionalFinancialDataset, "tdxfin/"+filename, fmt.Sprintf("%x", sha256.Sum256(raw)), entry.Size).Scan(&known)
	if err != nil {
		return entry, nil, err
	}
	if !known {
		return entry, nil, fmt.Errorf("cache checksum/size mismatch: %s", filename)
	}
	return entry, raw, nil
}

func atomicCacheWrite(path string, raw []byte) error {
	if previous, err := os.ReadFile(path); err == nil && bytes.Equal(previous, raw) {
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".cache-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(raw); err != nil {
		f.Close()
		return err
	}
	if err = f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	return os.Rename(f.Name(), path)
}

func publishFinancialCache(root string, entry financial.FileEntry, stored artifact.Stored) error {
	raw, err := os.ReadFile(artifact.Resolve(root, stored))
	if err != nil {
		return err
	}
	return atomicCacheWrite(filepath.Join(root, "tdx-cache", entry.Filename), raw)
}

func financialCacheDiagnostic(ctx context.Context, db *sql.DB, run int64, subject string, cause error) error {
	return store.RecordIngestDiagnostics(ctx, db, run, "tdx", tdxProfessionalFinancialDataset, []store.IngestDiagnostic{{RuleCode: "financial.local_cache_fallback", Severity: "warning", SubjectType: "financial_cache", SubjectKey: subject, Details: fmt.Sprintf("using verified local evidence; upstream freshness not confirmed: %v", cause)}})
}

func financialManifest(ctx context.Context, source TDXProfessionalFinancialSource, root string) ([]financial.FileEntry, []byte, bool, error) {
	// Read local state first; a failed remote request cannot overwrite it.
	local, localErr := os.ReadFile(filepath.Join(root, "tdx-cache", "gpcw.txt"))
	entries, raw, err := source.ProfessionalFinancialFileList(ctx)
	if err == nil {
		entries, err = financial.ParseFileList(raw)
	}
	if err == nil {
		return entries, raw, false, nil
	}
	if ctx.Err() != nil {
		return nil, nil, false, ctx.Err()
	}
	if localErr != nil {
		return nil, nil, false, fmt.Errorf("upstream manifest: %w; local cache: %v", err, localErr)
	}
	entries, localErr = financial.ParseFileList(local)
	if localErr != nil {
		return nil, nil, false, fmt.Errorf("upstream manifest: %w; invalid local cache: %v", err, localErr)
	}
	return entries, local, true, err
}

func sameFinancialEntry(a, b financial.FileEntry) bool {
	return a.Filename == b.Filename && a.Size == b.Size && strings.EqualFold(a.MD5, b.MD5)
}

// Explicit offline ingestion selects only files actually present locally. MD5
// describes those bytes, not an assertion that they match the latest upstream
// revision. The normal package parser still validates ZIP structure and CRC.
func localFinancialManifest(root string) ([]financial.FileEntry, []byte, error) {
	raw, err := os.ReadFile(filepath.Join(root, "tdx-cache", "gpcw.txt"))
	if err != nil {
		return nil, nil, err
	}
	entries, err := financial.ParseFileList(raw)
	if err != nil {
		return nil, nil, err
	}
	local := make([]financial.FileEntry, 0, len(entries))
	for _, entry := range entries {
		b, err := os.ReadFile(filepath.Join(root, "tdx-cache", entry.Filename))
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return nil, nil, err
		}
		entry.Size = int64(len(b))
		entry.MD5 = fmt.Sprintf("%x", md5.Sum(b))
		local = append(local, entry)
	}
	if len(local) == 0 {
		return nil, nil, fmt.Errorf("no local financial packages")
	}
	return local, raw, nil
}
