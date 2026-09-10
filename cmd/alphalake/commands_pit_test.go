package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type repairCatalogueSource struct {
	calls  []string
	cancel context.CancelFunc
}

func (s *repairCatalogueSource) CataloguePage(ctx context.Context, r cninfo.CatalogueRequest) (cninfo.CataloguePage, []byte, error) {
	s.calls = append(s.calls, r.Code)
	if s.cancel != nil {
		s.cancel()
		return cninfo.CataloguePage{}, nil, ctx.Err()
	}
	if r.Code == "000001" {
		return cninfo.CataloguePage{}, nil, errors.New("simulated first security failure")
	}
	return cninfo.CataloguePage{Page: 1, TotalPages: 1}, []byte(`{"announcements":[]}`), nil
}
func (*repairCatalogueSource) FilingDocumentURL(locator string) (string, error) { return locator, nil }
func (*repairCatalogueSource) FilingDocument(context.Context, string) ([]byte, string, string, error) {
	return nil, "", "", errors.New("metadata repair must not fetch PDF")
}

func TestRepairFilingsContinuesAndCountsCancellation(t *testing.T) {
	ctx := t.Context()
	path := filepath.Join(t.TempDir(), "repair.duckdb")
	db, err := duckstore.OpenAndMigrate(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	day := time.Date(2025, 4, 29, 0, 0, 0, 0, time.UTC)
	queries := []duckstore.FilingRepairQuery{{Code: "000001", StartDate: day, MissingPeriods: 1}, {Code: "000002", StartDate: day, MissingPeriods: 1}}
	source := &repairCatalogueSource{}
	attempted, failed, err := repairFilingQueries(ctx, db, source, filepath.Join(t.TempDir(), "raw"), queries, day)
	if err != nil || attempted != 2 || failed != 1 || len(source.calls) != 2 {
		t.Fatalf("repair did not isolate failure: %d %d %v %v", attempted, failed, err, source.calls)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = duckstore.Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var failures, completed int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FILTER(WHERE status='failed'),count(*) FILTER(WHERE status='completed') FROM meta.ingest_run WHERE dataset='filing'`).Scan(&failures, &completed); err != nil || failures != 1 || completed != 1 {
		t.Fatalf("run persistence: %d %d %v", failures, completed, err)
	}
	canceled, cancel := context.WithCancel(ctx)
	defer cancel()
	source = &repairCatalogueSource{cancel: cancel}
	attempted, failed, err = repairFilingQueries(canceled, db, source, filepath.Join(t.TempDir(), "raw"), queries, day)
	if !errors.Is(err, context.Canceled) || attempted != 1 || failed != 1 || len(source.calls) != 1 {
		t.Fatalf("false attempted count after cancellation: %d %d %v", attempted, failed, err)
	}
}

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

func TestFinancialPackageLimit(t *testing.T) {
	for _, c := range []struct {
		args []string
		want int
	}{{nil, 1}, {[]string{"--all"}, 0}, {[]string{"--latest", "6"}, 6}} {
		got, err := parseFinancialLimit(c.args)
		if err != nil || got != c.want {
			t.Fatalf("%v: %d %v", c.args, got, err)
		}
	}
	for _, args := range [][]string{{"--latest", "0"}, {"--latest", "-1"}, {"--all", "--latest", "1"}, {"oops"}} {
		if _, err := parseFinancialLimit(args); err == nil {
			t.Fatalf("accepted %v", args)
		}
	}
}
