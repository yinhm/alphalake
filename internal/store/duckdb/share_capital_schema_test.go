package duckdb

import (
	"context"
	"path/filepath"
	"testing"
)

func TestShareCapitalIdentityAllowsMultipleSourceRecordsPerDay(t *testing.T) {
	ctx := context.Background()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "share-capital.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	for _, recordID := range []string{"event-a", "event-b"} {
		if _, err := db.ExecContext(ctx, `
			INSERT INTO market.share_capital (
				instrument_id, effective_date, float_shares, total_shares,
				source_category, source, source_record_id
			) VALUES (1, DATE '2026-09-04', 100, 200, 5, 'tdx', ?)
		`, recordID); err != nil {
			t.Fatalf("insert %s: %v", recordID, err)
		}
	}
	var count int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.share_capital`).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 2 {
		t.Fatalf("share capital rows = %d, want 2", count)
	}
}
