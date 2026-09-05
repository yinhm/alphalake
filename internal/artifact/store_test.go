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

func TestPersistRepairsCorruptContentAddressedFile(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "artifact-repair.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	root := filepath.Join(t.TempDir(), "raw")
	input := Input{
		Source: "tdx", Dataset: "professional_financial", SourceLocator: "tdxfin/a.zip",
		FetchedAt: time.Now(), Content: []byte("verified provider bytes"),
	}
	stored, err := Persist(ctx, db, root, input)
	if err != nil {
		t.Fatal(err)
	}
	path := Resolve(root, stored)
	if err := os.WriteFile(path, []byte("corrupt"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadVersions(ctx, db, root, "tdx", "professional_financial", "tdxfin/a.zip", 1); err == nil {
		t.Fatal("strict load unexpectedly accepted corrupt artifact")
	}

	repaired, err := Persist(ctx, db, root, input)
	if err != nil {
		t.Fatalf("Persist() repair error = %v", err)
	}
	if repaired.ArtifactID != stored.ArtifactID {
		t.Fatalf("repair artifact ID=%d, want original %d", repaired.ArtifactID, stored.ArtifactID)
	}
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(input.Content) {
		t.Fatalf("repaired bytes=%q, want %q", got, input.Content)
	}
}

func TestLoadHealthyVersionsSkipsCorruptRetainedRevision(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "artifact-healthy.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	root := filepath.Join(t.TempDir(), "raw")
	base := Input{Source: "tdx", Dataset: "professional_financial", SourceLocator: "tdxfin/a.zip", FetchedAt: time.Now()}
	base.Content = []byte("old")
	old, err := Persist(ctx, db, root, base)
	if err != nil {
		t.Fatal(err)
	}
	base.FetchedAt = base.FetchedAt.Add(time.Minute)
	base.Content = []byte("new")
	newer, err := Persist(ctx, db, root, base)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(Resolve(root, old), []byte("broken-old"), 0o644); err != nil {
		t.Fatal(err)
	}

	versions, failures, err := LoadHealthyVersions(ctx, db, root, "tdx", "professional_financial", "tdxfin/a.zip", 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(failures) != 1 || len(versions) != 1 {
		t.Fatalf("healthy/failures=%d/%d, want 1/1", len(versions), len(failures))
	}
	if versions[0].Stored.ArtifactID != newer.ArtifactID || string(versions[0].Content) != "new" {
		t.Fatalf("healthy version=%#v", versions[0])
	}
}

func TestSafeSegmentCannotTraverseDirectories(t *testing.T) {
	for _, input := range []string{"", ".", "..", " ../.. ", "/tmp/raw", "tdx"} {
		segment := safeSegment(input)
		if !filepath.IsLocal(segment) || filepath.Base(segment) != segment || segment == "." {
			t.Fatalf("unsafe segment %q for %q", segment, input)
		}
	}
}
