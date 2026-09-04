package duckdb

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestUpsertInstrumentIsIdempotentByOpenProviderIdentifier(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "instrument.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	ref := domain.InstrumentRef{
		Type:        domain.InstrumentEquity,
		ExchangeMIC: "XSHG",
		Currency:    "CNY",
		Name:        "贵州茅台",
	}
	identifier := domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600519"}

	firstID, err := UpsertInstrument(ctx, db, ref, identifier)
	if err != nil {
		t.Fatalf("first UpsertInstrument() error = %v", err)
	}
	if firstID == 0 {
		t.Fatal("first instrument ID is zero")
	}

	ref.Name = "贵州茅台股份有限公司"
	secondID, err := UpsertInstrument(ctx, db, ref, identifier)
	if err != nil {
		t.Fatalf("second UpsertInstrument() error = %v", err)
	}
	if secondID != firstID {
		t.Fatalf("second instrument ID = %d, want %d", secondID, firstID)
	}

	var name string
	if err := db.QueryRowContext(ctx, `SELECT name FROM ref.instrument WHERE instrument_id = ?`, firstID).Scan(&name); err != nil {
		t.Fatalf("query instrument: %v", err)
	}
	if name != ref.Name {
		t.Fatalf("name = %q, want %q", name, ref.Name)
	}

	var identifierCount int
	if err := db.QueryRowContext(ctx, `
		SELECT count(*) FROM ref.instrument_identifier
		WHERE provider = 'tdx' AND identifier_type = 'symbol' AND identifier_value = 'sh600519'
	`).Scan(&identifierCount); err != nil {
		t.Fatalf("count identifiers: %v", err)
	}
	if identifierCount != 1 {
		t.Fatalf("identifier count = %d, want 1", identifierCount)
	}
}

func TestClosedProviderCodeReuseCreatesNewInstrument(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "reuse.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	oldRef := domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Old Co"}
	identifier := domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"}
	oldID, err := UpsertInstrument(ctx, db, oldRef, identifier)
	if err != nil {
		t.Fatal(err)
	}
	cutover := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	closedID, closed, err := CloseInstrumentIdentifier(ctx, db, "tdx", "symbol", "sh600001", cutover)
	if err != nil {
		t.Fatal(err)
	}
	if !closed || closedID != oldID {
		t.Fatalf("CloseInstrumentIdentifier() = %d/%v, want %d/true", closedID, closed, oldID)
	}

	newRef := domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "New Co"}
	newID, err := UpsertInstrument(ctx, db, newRef, identifier)
	if err != nil {
		t.Fatal(err)
	}
	if newID == oldID {
		t.Fatalf("reused provider code resolved to old instrument %d", oldID)
	}

	before := cutover.AddDate(0, 0, -1)
	resolvedOld, ok, err := ResolveInstrumentIdentifierAt(ctx, db, "tdx", "symbol", "sh600001", before)
	if err != nil {
		t.Fatal(err)
	}
	if !ok || resolvedOld != oldID {
		t.Fatalf("resolve before cutover = %d/%v, want %d/true", resolvedOld, ok, oldID)
	}

	after := cutover.AddDate(0, 0, 1)
	resolvedNew, ok, err := ResolveInstrumentIdentifierAt(ctx, db, "tdx", "symbol", "sh600001", after)
	if err != nil {
		t.Fatal(err)
	}
	if !ok || resolvedNew != newID {
		t.Fatalf("resolve after cutover = %d/%v, want %d/true", resolvedNew, ok, newID)
	}
}

func TestExplicitIdentifierValidityResolvesHalfOpenInterval(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "interval.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	from := time.Date(2020, 1, 1, 9, 0, 0, 0, time.FixedZone("UTC+8", 8*60*60))
	to := time.Date(2025, 1, 1, 9, 0, 0, 0, time.FixedZone("UTC+8", 8*60*60))
	id, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Interval Co"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600099", ValidFrom: &from, ValidTo: &to},
	)
	if err != nil {
		t.Fatal(err)
	}

	inside, ok, err := ResolveInstrumentIdentifierAt(ctx, db, "tdx", "symbol", "sh600099", time.Date(2024, 12, 31, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if !ok || inside != id {
		t.Fatalf("inside interval = %d/%v, want %d/true", inside, ok, id)
	}
	_, ok, err = ResolveInstrumentIdentifierAt(ctx, db, "tdx", "symbol", "sh600099", time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if ok {
		t.Fatal("valid_to must be exclusive")
	}
}

func TestUpsertInstrumentRejectsInvalidIdentifierInterval(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "instrument.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	from := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	to := from
	_, err = UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001", ValidFrom: &from, ValidTo: &to},
	)
	if err == nil {
		t.Fatal("UpsertInstrument() expected invalid interval error")
	}
}

func TestUpsertInstrumentRejectsIncompleteIdentifier(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "instrument.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	_, err = UpsertInstrument(ctx, db, domain.InstrumentRef{Type: domain.InstrumentEquity}, domain.Identifier{Provider: "tdx"})
	if err == nil {
		t.Fatal("UpsertInstrument() expected validation error")
	}
}
