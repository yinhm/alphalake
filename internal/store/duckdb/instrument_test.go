package duckdb

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestUpsertInstrumentIsIdempotentByProviderIdentifier(t *testing.T) {
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
