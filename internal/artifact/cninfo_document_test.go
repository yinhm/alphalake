package artifact

import (
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestCNINFODocumentReferencePreservesSourceAndRetrievalTime(t *testing.T) {
	root := t.TempDir()
	pdf := []byte("%PDF-1.7\nunit test document")
	sha := fmt.Sprintf("%x", sha256.Sum256(pdf))
	local := "cninfo/objects/" + sha[:2] + "/" + sha + ".pdf"
	path := filepath.Join(root, local)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, pdf, 0o600); err != nil {
		t.Fatal(err)
	}
	fetched := time.Date(2026, 4, 11, 0, 0, 0, 0, time.UTC)
	r := CNINFODocumentReference{URL: "https://static.cninfo.com.cn/finalpage/2026-04-10/1234.PDF", SHA256: sha, LocalPath: local, FetchedAt: &fetched}
	if err := PublishCNINFODocumentReference(root, r); err != nil {
		t.Fatal(err)
	}
	view, _ := CNINFODocumentPath(root, r.URL)
	if info, err := os.Lstat(view); err != nil || info.Mode()&os.ModeSymlink == 0 {
		t.Fatal("directory entry copied instead of referencing source", err)
	}
	later := fetched.Add(time.Hour)
	r.FetchedAt = &later
	if err := PublishCNINFODocumentReference(root, r); err != nil {
		t.Fatal(err)
	}
	got, exists, err := ReadCNINFODocumentReference(root, r.URL)
	if err != nil || !exists || !got.FetchedAt.Equal(fetched) || got.SHA256 != sha {
		t.Fatalf("reference/time changed: %+v %v %v", got, exists, err)
	}
	if err := os.WriteFile(path, []byte("%PDF-1.7\ntampered"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := ReadCNINFODocumentReference(root, r.URL); err == nil {
		t.Fatal("accepted corrupt source object")
	}
}

func TestCNINFODocumentURLAndReceiptAreBounded(t *testing.T) {
	for _, url := range []string{"https://other.example/finalpage/2026-04-10/1234.pdf", "https://static.cninfo.com.cn/finalpage/2026-02-31/1234.pdf", "https://static.cninfo.com.cn/finalpage/2026-04-10/../1234.pdf"} {
		if _, ok := CNINFODocumentPath(t.TempDir(), url); ok {
			t.Fatal("accepted document URL", url)
		}
	}
}

func TestCNINFODocumentLinkRejectsExternalSource(t *testing.T) {
	root := t.TempDir()
	external := filepath.Join(t.TempDir(), "original.pdf")
	if err := os.WriteFile(external, []byte("%PDF-1.7\nexternal"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "source.pdf")
	if err := os.Symlink(external, link); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(root, "copy.pdf")
	if err := shareDocumentFile(root, link, target); err == nil {
		t.Fatal("linked an external source into workspace")
	}
	if _, err := os.Lstat(target); !os.IsNotExist(err) {
		t.Fatal("rejected source created an archive file", err)
	}
}
