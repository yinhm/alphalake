package ingest

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/domain"
	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

type pdfCacheTransport func(*http.Request) (*http.Response, error)

func (f pdfCacheTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestCNINFODocumentReusesSharedCacheWithoutDownload(t *testing.T) {
	ctx := t.Context()
	root := t.TempDir()
	db, err := duck.OpenInitialized(ctx, filepath.Join(root, "test.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	pdf := []byte("%PDF-1.7\nshared cache document")
	sha := fmt.Sprintf("%x", sha256.Sum256(pdf))
	local := "cninfo/objects/" + sha[:2] + "/" + sha + ".pdf"
	if err = os.MkdirAll(filepath.Dir(filepath.Join(root, local)), 0o755); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(root, local), pdf, 0o600); err != nil {
		t.Fatal(err)
	}
	fetched := time.Date(2026, 4, 11, 0, 0, 0, 0, time.UTC)
	r := artifact.CNINFODocumentReference{URL: "https://static.cninfo.com.cn/finalpage/2026-04-10/1234.PDF", SHA256: sha, LocalPath: local, FetchedAt: &fetched}
	if err = artifact.PublishCNINFODocumentReference(root, r); err != nil {
		t.Fatal(err)
	}
	filing := domain.FilingObservation{SourceURL: r.URL}
	var summary CNINFOFilingSummary
	if err = archiveCNINFODocument(ctx, db, nil, root, 1, fetched.Add(time.Hour), &filing, &summary); err != nil {
		t.Fatal(err)
	}
	if summary.ReusedDocs != 1 || summary.Documents != 0 || filing.DocumentSHA256 != sha || filing.DocumentArtifactID <= 0 {
		t.Fatalf("cache not reused: %+v %+v", filing, summary)
	}
	var actual time.Time
	if err = db.QueryRowContext(ctx, `SELECT fetched_at FROM meta.artifact WHERE artifact_id=?`, filing.DocumentArtifactID).Scan(&actual); err != nil || !actual.Equal(fetched) {
		t.Fatalf("original acquisition changed: %v %v", actual, err)
	}
	feed := referenceFeed{"issuer_disclosure", "share_classes/test", r.URL, "application/pdf", "test"}
	options := ReferenceOptions{Client: &http.Client{Transport: pdfCacheTransport(func(*http.Request) (*http.Response, error) {
		return nil, errors.New("network forbidden")
	})}}
	ref, err := fetchReferenceArtifact(ctx, db, root, options, feed, 1)
	if err != nil {
		t.Fatal("reference consumer downloaded the same official PDF", err)
	}
	original, _ := os.Stat(filepath.Join(root, ref.LocalPath))
	canonical, _ := os.Stat(filepath.Join(root, local))
	if !os.SameFile(original, canonical) {
		t.Fatal("reference consumer stored a second PDF payload")
	}
	if _, err = fetchReferenceArtifact(ctx, db, root, options, feed, 1); err != nil {
		t.Fatal("reference replay did not reuse its source artifact", err)
	}
	// 未知原获取时间保持未知，不靠重复下载制造新的首次取得记录。
	r.URL = "https://static.cninfo.com.cn/finalpage/2026-04-10/1235.pdf"
	r.FetchedAt = nil
	if err = artifact.PublishCNINFODocumentReference(root, r); err != nil {
		t.Fatal(err)
	}
	filing.SourceURL = r.URL
	if err = archiveCNINFODocument(ctx, db, nil, root, 1, fetched.Add(time.Hour), &filing, &summary); err == nil || !strings.Contains(err.Error(), "unknown original retrieval") {
		t.Fatalf("unknown acquisition was fabricated: %v", err)
	}
}
