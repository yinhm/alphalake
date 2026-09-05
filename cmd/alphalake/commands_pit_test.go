package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestFilingUnresolvedCommand(t *testing.T) {
	ctx := t.Context()
	dbPath := filepath.Join(t.TempDir(), "filings.duckdb")
	db, err := duckstore.OpenAndMigrate(ctx, dbPath)
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"first", "second"} {
		_, err := duckstore.UpsertFilings(ctx, db, 1, []domain.FilingObservation{{
			Source: "cninfo", SourceFilingID: id, ProviderCode: "000001",
			AnnouncementDate:          time.Date(2026, 3, 28, 0, 0, 0, 0, time.UTC),
			AnnouncementTime:          time.Date(2026, 3, 28, 16, 0, 0, 0, time.UTC),
			AnnouncementTimePrecision: domain.AnnouncementPrecisionDate,
			ClassifierVersion:         "test", ResolutionReason: "missing identity",
		}})
		if err != nil {
			db.Close()
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	output, err := os.CreateTemp(t.TempDir(), "stdout")
	if err != nil {
		t.Fatal(err)
	}
	defer output.Close()
	prior := os.Stdout
	os.Stdout = output
	defer func() { os.Stdout = prior }()
	handled, err := runExtendedCommand(ctx, []string{"filing-unresolved", dbPath, "--limit", "1", "--offset", "1"})
	if err != nil || !handled {
		t.Fatalf("handled=%v err=%v", handled, err)
	}
	data, err := os.ReadFile(output.Name())
	if err != nil {
		t.Fatal(err)
	}
	got := string(data)
	for _, want := range []string{"pending filing resolutions: 1 (limit=1 offset=1)", "source_id=second", "date=2026-03-28 precision=date", "reason=\"missing identity\""} {
		if !strings.Contains(got, want) {
			t.Fatalf("output %q missing %q", got, want)
		}
	}
	if strings.Contains(got, "source_id=first") {
		t.Fatalf("offset ignored: %s", got)
	}
	for _, args := range [][]string{nil, {dbPath, "--limit", "0"}, {dbPath, "--offset", "-1"}, {dbPath, "--bad", "1"}} {
		if err := runFilingUnresolved(ctx, args); err == nil {
			t.Fatalf("expected error for %v", args)
		}
	}
}
