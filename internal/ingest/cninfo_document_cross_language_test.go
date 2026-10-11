//go:build full

package ingest

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/domain"
	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestCNINFOPDFCacheGoPythonRoundTrip(t *testing.T) {
	python := os.Getenv("ALPHALAKE_TEST_PYTHON")
	if python == "" {
		var err error
		python, err = exec.LookPath("python3")
		if err != nil {
			t.Fatal("python3 required for explicit PDF cache cross-language acceptance")
		}
	}
	root := t.TempDir()
	backend, err := filepath.Abs("../../valuation/backend")
	if err != nil {
		t.Fatal(err)
	}
	runPython := func(script string) []byte {
		t.Helper()
		command := exec.Command(python, "-c", script)
		command.Env = append(os.Environ(), "ALPHALAKE_WORKSPACE="+root, "PYTHONPATH="+backend)
		output, err := command.CombinedOutput()
		if err != nil {
			t.Fatalf("Python cache: %v\n%s", err, output)
		}
		return output
	}
	var receipt artifact.CNINFODocumentReference
	output := runPython(`import json
from data_sources.cninfo_documents import document_lock, publish_reference, digest
from data_sources.paths import workspace_path
path = workspace_path('cninfo', 'temporary.pdf')
path.parent.mkdir()
path.write_bytes(b'%PDF-1.7\nPython original source')
url = 'https://static.cninfo.com.cn/finalpage/2026-04-10/1234.PDF'
with document_lock(url):
    record = publish_reference(url, path, digest(path), '2026-04-11T00:00:00+00:00')
path.unlink()
print(json.dumps(record))`)
	if err = json.Unmarshal(output, &receipt); err != nil {
		t.Fatal(err)
	}
	db, err := duck.OpenInitialized(t.Context(), filepath.Join(root, "test.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	filing := domain.FilingObservation{SourceURL: receipt.URL}
	var summary CNINFOFilingSummary
	if err = archiveCNINFODocument(t.Context(), db, nil, root, 1, time.Now(), &filing, &summary); err != nil {
		t.Fatal(err)
	}
	if summary.ReusedDocs != 1 || summary.Documents != 0 || filing.DocumentSHA256 != receipt.SHA256 {
		t.Fatalf("Go did not reuse Python cache: %+v", summary)
	}
	// Go的直接归档也必须供Python复用，不依赖原采集器的文件名。
	pdf, err := os.ReadFile(filepath.Join(root, receipt.LocalPath))
	if err != nil {
		t.Fatal(err)
	}
	second := "https://static.cninfo.com.cn/finalpage/2026-04-10/1235.pdf"
	if _, err = artifact.Persist(t.Context(), db, root, artifact.Input{Source: "cninfo", Dataset: "filing_document", SourceLocator: second, FetchedAt: *receipt.FetchedAt, MediaType: "application/pdf", Content: pdf}); err != nil {
		t.Fatal(err)
	}
	runPython(`from data_sources.cninfo_documents import read_reference
from data_sources.paths import workspace_path
a = read_reference('https://static.cninfo.com.cn/finalpage/2026-04-10/1234.PDF')
b = read_reference('https://static.cninfo.com.cn/finalpage/2026-04-10/1235.pdf')
assert a['sha256'] == b['sha256']
assert a['fetched_at'] == b['fetched_at']
assert len(list(workspace_path('cninfo', 'objects').rglob('*.pdf'))) == 1`)
}
