package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
)

type CorporateActionSnapshotStats struct {
	Actions      int
	ShareCapital int
}

func GetCorporateActionSnapshotStats(ctx context.Context, db *sql.DB, instrumentID int64, source string) (CorporateActionSnapshotStats, error) {
	var stats CorporateActionSnapshotStats
	if db == nil {
		return stats, errors.New("duckdb is nil")
	}
	if instrumentID <= 0 {
		return stats, errors.New("instrument ID must be positive")
	}
	source = strings.TrimSpace(source)
	if source == "" {
		return stats, errors.New("source is required")
	}
	if err := db.QueryRowContext(ctx, `
		SELECT
			(SELECT count(*) FROM market.corporate_action WHERE instrument_id=? AND source=?),
			(SELECT count(*) FROM market.share_capital WHERE instrument_id=? AND source=?)
	`, instrumentID, source, instrumentID, source).Scan(&stats.Actions, &stats.ShareCapital); err != nil {
		return stats, fmt.Errorf("query corporate action snapshot stats: %w", err)
	}
	return stats, nil
}
