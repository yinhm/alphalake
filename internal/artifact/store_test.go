package artifact

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"
	"time"

	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestPersistIsContentAddressedAndIdempotent(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "artifact.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	root := filepath.Join(t.TempDir(), "raw")
	content := []byte("gpcw20260630.zip bytes")
	fetched := time.Date(2026, 9, 4, 12, 0, 0, 0, time.UTC)
	input := Input{
		Source: "tdx", Dataset: "professional_financial",
		SourceLocator: "tdxfin/gpcw20260630.zip", FetchedAt: fetched,
		MediaType: "application/zip", ParserVersion: "gpcw-v1", Content: content,
	}
	first, err := Persist(ctx, db, root, input)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Persist(ctx, db, root, input)
	if err != nil {
		t.Fatal(err)
	}
	if first.ArtifactID <= 0 || first.ArtifactID != second.ArtifactID {
		t.Fatalf("artifact IDs=%d/%d, want stable positive ID", first.ArtifactID, second.ArtifactID)
	}
	sum := sha256.Sum256(content)
	wantSHA := hex.EncodeToString(sum[:])
	if first.SHA256 != wantSHA || filepath.Ext(first.LocalPath) != ".zip" {
		t.Fatalf("stored=%#v", first)
	}
	got, err := os.ReadFile(Resolve(root, first))
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(content) {
		t.Fatalf("artifact bytes=%q", got)
	}
	var rows int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.artifact WHERE source='tdx' AND dataset='professional_financial'`).Scan(&rows); err != nil {
		t.Fatal(err)
	}
	if rows != 1 {
		t.Fatalf("artifact rows=%d, want 1", rows)
	}
}

func TestPersistSameBytesDifferentLocatorKeepsLineageRows(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "artifact-lineage.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	root := filepath.Join(t.TempDir(), "raw")
	base := Input{Source: "tdx", Dataset: "professional_financial", FetchedAt: time.Now(), Content: []byte("same")}
	base.SourceLocator = "tdxfin/a.zip"
	a, err := Persist(ctx, db, root, base)
	if err != nil {
		t.Fatal(err)
	}
	base.SourceLocator = "tdxfin/b.zip"
	b, err := Persist(ctx, db, root, base)
	if err != nil {
		t.Fatal(err)
	}
	if a.SHA256 != b.SHA256 || a.LocalPath != b.LocalPath || a.ArtifactID == b.ArtifactID {
		t.Fatalf("a=%#v b=%#v", a, b)
	}
}
