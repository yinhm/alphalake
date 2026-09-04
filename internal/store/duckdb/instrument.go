package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"github.com/yinhm/alphalake/internal/domain"
)

// UpsertInstrument resolves an instrument through a provider identifier and
// creates or refreshes its canonical instrument row. The provider identifier,
// not the current display name, is used for idempotency.
func UpsertInstrument(ctx context.Context, db *sql.DB, ref domain.InstrumentRef, identifier domain.Identifier) (int64, error) {
	if db == nil {
		return 0, errors.New("duckdb is nil")
	}
	if err := validateInstrumentInput(ref, identifier); err != nil {
		return 0, err
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return 0, fmt.Errorf("begin instrument upsert: %w", err)
	}
	defer tx.Rollback()

	instrumentID, err := upsertInstrumentTx(ctx, tx, ref, identifier)
	if err != nil {
		return 0, err
	}
	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("commit instrument upsert: %w", err)
	}
	return instrumentID, nil
}

// UpsertInstruments performs an idempotent security-master refresh in one
// transaction. Returned IDs align with observations by index.
func UpsertInstruments(ctx context.Context, db *sql.DB, observations []domain.InstrumentObservation) ([]int64, error) {
	if db == nil {
		return nil, errors.New("duckdb is nil")
	}
	if len(observations) == 0 {
		return []int64{}, nil
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("begin instrument batch upsert: %w", err)
	}
	defer tx.Rollback()

	ids := make([]int64, len(observations))
	for i, observation := range observations {
		if err := validateInstrumentInput(observation.Instrument, observation.Identifier); err != nil {
			return nil, fmt.Errorf("instrument %d: %w", i, err)
		}
		instrumentID, err := upsertInstrumentTx(ctx, tx, observation.Instrument, observation.Identifier)
		if err != nil {
			return nil, fmt.Errorf("upsert instrument %d (%s): %w", i, observation.Identifier.Value, err)
		}
		ids[i] = instrumentID
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit instrument batch upsert: %w", err)
	}
	return ids, nil
}

func validateInstrumentInput(ref domain.InstrumentRef, identifier domain.Identifier) error {
	if strings.TrimSpace(identifier.Provider) == "" || strings.TrimSpace(identifier.Type) == "" || strings.TrimSpace(identifier.Value) == "" {
		return errors.New("provider, identifier type, and identifier value are required")
	}
	if ref.Type == "" {
		return errors.New("instrument type is required")
	}
	return nil
}

func upsertInstrumentTx(ctx context.Context, tx *sql.Tx, ref domain.InstrumentRef, identifier domain.Identifier) (int64, error) {
	var instrumentID int64
	err := tx.QueryRowContext(ctx, `
		SELECT instrument_id
		FROM ref.instrument_identifier
		WHERE provider = ?
		  AND identifier_type = ?
		  AND identifier_value = ?
		ORDER BY valid_from DESC NULLS LAST
		LIMIT 1
	`, identifier.Provider, identifier.Type, identifier.Value).Scan(&instrumentID)

	switch {
	case errors.Is(err, sql.ErrNoRows):
		err = tx.QueryRowContext(ctx, `
			INSERT INTO ref.instrument (
				instrument_type, exchange_mic, currency, name, list_date, delist_date
			) VALUES (?, ?, ?, ?, ?, ?)
			RETURNING instrument_id
		`, ref.Type, nullableString(ref.ExchangeMIC), nullableString(ref.Currency), nullableString(ref.Name), ref.ListDate, ref.DelistDate).Scan(&instrumentID)
		if err != nil {
			return 0, fmt.Errorf("insert canonical instrument: %w", err)
		}

		_, err = tx.ExecContext(ctx, `
			INSERT INTO ref.instrument_identifier (
				instrument_id, provider, identifier_type, identifier_value,
				valid_from, valid_to, is_primary
			) VALUES (?, ?, ?, ?, ?, ?, true)
		`, instrumentID, identifier.Provider, identifier.Type, identifier.Value, identifier.ValidFrom, identifier.ValidTo)
		if err != nil {
			return 0, fmt.Errorf("insert instrument identifier: %w", err)
		}

	case err != nil:
		return 0, fmt.Errorf("resolve instrument identifier: %w", err)

	default:
		_, err = tx.ExecContext(ctx, `
			UPDATE ref.instrument
			SET instrument_type = ?,
			    exchange_mic = ?,
			    currency = ?,
			    name = ?,
			    list_date = COALESCE(?, list_date),
			    delist_date = COALESCE(?, delist_date),
			    updated_at = now()
			WHERE instrument_id = ?
		`, ref.Type, nullableString(ref.ExchangeMIC), nullableString(ref.Currency), nullableString(ref.Name), ref.ListDate, ref.DelistDate, instrumentID)
		if err != nil {
			return 0, fmt.Errorf("refresh canonical instrument: %w", err)
		}
	}
	return instrumentID, nil
}

func nullableString(v string) any {
	if strings.TrimSpace(v) == "" {
		return nil
	}
	return v
}
