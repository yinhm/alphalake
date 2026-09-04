package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

// ResolveInstrumentIdentifiersAt resolves a batch with the same strict
// temporal semantics used by classification. Returned IDs align with input;
// unresolved identifiers are zero, while overlapping matches are an error.
func ResolveInstrumentIdentifiersAt(ctx context.Context, db *sql.DB, identifiers []domain.Identifier, asOf time.Time) ([]int64, error) {
	if db == nil {
		return nil, errors.New("duckdb is nil")
	}
	if asOf.IsZero() {
		return nil, errors.New("as-of date is required")
	}
	if len(identifiers) == 0 {
		return []int64{}, nil
	}
	// duckdb-go currently rejects database/sql read-only transaction options.
	// This transaction performs reads only by convention, matching the existing
	// strict resolver used inside classification transactions.
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("begin batch identifier resolution: %w", err)
	}
	defer tx.Rollback()
	resolved, err := resolveInstrumentIdentifiersAtTx(ctx, tx, identifiers, dateUTC(asOf))
	if err != nil {
		return nil, err
	}
	ids := make([]int64, len(identifiers))
	for i, identifier := range identifiers {
		ids[i] = resolved[providerIdentifierKey(identifier.Provider, identifier.Type, identifier.Value)]
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit batch identifier resolution: %w", err)
	}
	return ids, nil
}
