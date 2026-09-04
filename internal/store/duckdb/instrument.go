package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

// UpsertInstrument resolves an instrument through the provider identifier that
// is active for the observation interval and creates or refreshes its canonical
// instrument row. Identifier validity uses half-open [valid_from, valid_to)
// semantics. A current observation (ValidFrom=nil) only resolves an open
// identifier (valid_to IS NULL), so a previously closed provider code is never
// silently reused for a new security.
func UpsertInstrument(ctx context.Context, db *sql.DB, ref domain.InstrumentRef, identifier domain.Identifier) (int64, error) {
	if db == nil {
		return 0, errors.New("duckdb is nil")
	}
	identifier = normalizedIdentifier(identifier)
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
		identifier := normalizedIdentifier(observation.Identifier)
		if err := validateInstrumentInput(observation.Instrument, identifier); err != nil {
			return nil, fmt.Errorf("instrument %d: %w", i, err)
		}
		instrumentID, err := upsertInstrumentTx(ctx, tx, observation.Instrument, identifier)
		if err != nil {
			return nil, fmt.Errorf("upsert instrument %d (%s): %w", i, identifier.Value, err)
		}
		ids[i] = instrumentID
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit instrument batch upsert: %w", err)
	}
	return ids, nil
}

// ResolveInstrumentIdentifierAt resolves exactly one provider identifier at an
// observation date. Overlapping validity intervals are treated as data
// corruption rather than resolved with an arbitrary ORDER BY/LIMIT rule.
func ResolveInstrumentIdentifierAt(ctx context.Context, db *sql.DB, provider, identifierType, value string, asOf time.Time) (int64, bool, error) {
	if db == nil {
		return 0, false, errors.New("duckdb is nil")
	}
	provider = strings.TrimSpace(provider)
	identifierType = strings.TrimSpace(identifierType)
	value = strings.TrimSpace(value)
	if provider == "" || identifierType == "" || value == "" || asOf.IsZero() {
		return 0, false, errors.New("provider, identifier type, value, and as-of date are required")
	}
	asOf = dateUTC(asOf)
	rows, err := db.QueryContext(ctx, `
		SELECT instrument_id
		FROM ref.instrument_identifier
		WHERE provider=?
		  AND identifier_type=?
		  AND identifier_value=?
		  AND (valid_from IS NULL OR valid_from <= ?)
		  AND (valid_to IS NULL OR valid_to > ?)
		ORDER BY valid_from DESC NULLS FIRST
	`, provider, identifierType, value, asOf, asOf)
	if err != nil {
		return 0, false, fmt.Errorf("resolve temporal instrument identifier: %w", err)
	}
	defer rows.Close()
	var ids []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return 0, false, fmt.Errorf("scan temporal instrument identifier: %w", err)
		}
		ids = append(ids, id)
		if len(ids) > 1 {
			return 0, false, fmt.Errorf("ambiguous provider identifier %s/%s/%s at %s", provider, identifierType, value, asOf.Format("2006-01-02"))
		}
	}
	if err := rows.Err(); err != nil {
		return 0, false, fmt.Errorf("iterate temporal instrument identifier: %w", err)
	}
	if len(ids) == 0 {
		return 0, false, nil
	}
	return ids[0], true, nil
}

// CloseInstrumentIdentifier closes the currently open validity interval for a
// provider identifier. It does not mark the canonical instrument itself as
// delisted because identifiers may close due to a code change as well as a
// security lifecycle event.
func CloseInstrumentIdentifier(ctx context.Context, db *sql.DB, provider, identifierType, value string, validTo time.Time) (int64, bool, error) {
	if db == nil {
		return 0, false, errors.New("duckdb is nil")
	}
	provider = strings.TrimSpace(provider)
	identifierType = strings.TrimSpace(identifierType)
	value = strings.TrimSpace(value)
	if provider == "" || identifierType == "" || value == "" || validTo.IsZero() {
		return 0, false, errors.New("provider, identifier type, value, and valid-to date are required")
	}
	validTo = dateUTC(validTo)

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return 0, false, fmt.Errorf("begin identifier close: %w", err)
	}
	defer tx.Rollback()
	rows, err := tx.QueryContext(ctx, `
		SELECT instrument_identifier_id, instrument_id, valid_from
		FROM ref.instrument_identifier
		WHERE provider=? AND identifier_type=? AND identifier_value=? AND valid_to IS NULL
	`, provider, identifierType, value)
	if err != nil {
		return 0, false, fmt.Errorf("query open instrument identifier: %w", err)
	}
	type openIdentifier struct {
		rowID        int64
		instrumentID int64
		validFrom    sql.NullTime
	}
	var open []openIdentifier
	for rows.Next() {
		var item openIdentifier
		if err := rows.Scan(&item.rowID, &item.instrumentID, &item.validFrom); err != nil {
			rows.Close()
			return 0, false, fmt.Errorf("scan open instrument identifier: %w", err)
		}
		open = append(open, item)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return 0, false, fmt.Errorf("iterate open instrument identifier: %w", err)
	}
	rows.Close()
	if len(open) == 0 {
		return 0, false, nil
	}
	if len(open) > 1 {
		return 0, false, fmt.Errorf("multiple open provider identifiers for %s/%s/%s", provider, identifierType, value)
	}
	item := open[0]
	if item.validFrom.Valid && !item.validFrom.Time.Before(validTo) {
		return 0, false, fmt.Errorf("valid-to %s must be after valid-from %s", validTo.Format("2006-01-02"), item.validFrom.Time.Format("2006-01-02"))
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE ref.instrument_identifier SET valid_to=? WHERE instrument_identifier_id=?
	`, validTo, item.rowID); err != nil {
		return 0, false, fmt.Errorf("close instrument identifier: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return 0, false, fmt.Errorf("commit identifier close: %w", err)
	}
	return item.instrumentID, true, nil
}

func validateInstrumentInput(ref domain.InstrumentRef, identifier domain.Identifier) error {
	if strings.TrimSpace(identifier.Provider) == "" || strings.TrimSpace(identifier.Type) == "" || strings.TrimSpace(identifier.Value) == "" {
		return errors.New("provider, identifier type, and identifier value are required")
	}
	if ref.Type == "" {
		return errors.New("instrument type is required")
	}
	if identifier.ValidFrom != nil && identifier.ValidTo != nil && !identifier.ValidFrom.Before(*identifier.ValidTo) {
		return errors.New("identifier valid_from must be before valid_to")
	}
	return nil
}

func upsertInstrumentTx(ctx context.Context, tx *sql.Tx, ref domain.InstrumentRef, identifier domain.Identifier) (int64, error) {
	instrumentID, identifierRowID, found, err := resolveInstrumentIdentifierTx(ctx, tx, identifier)
	if err != nil {
		return 0, err
	}
	if !found {
		if err := tx.QueryRowContext(ctx, `
			INSERT INTO ref.instrument (
				instrument_type, exchange_mic, currency, name, list_date, delist_date
			) VALUES (?, ?, ?, ?, ?, ?)
			RETURNING instrument_id
		`, ref.Type, nullableString(ref.ExchangeMIC), nullableString(ref.Currency), nullableString(ref.Name), ref.ListDate, ref.DelistDate).Scan(&instrumentID); err != nil {
			return 0, fmt.Errorf("insert canonical instrument: %w", err)
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO ref.instrument_identifier (
				instrument_id, provider, identifier_type, identifier_value,
				valid_from, valid_to, is_primary
			) VALUES (?, ?, ?, ?, ?, ?, true)
		`, instrumentID, identifier.Provider, identifier.Type, identifier.Value, identifier.ValidFrom, identifier.ValidTo); err != nil {
			return 0, fmt.Errorf("insert instrument identifier: %w", err)
		}
		return instrumentID, nil
	}

	if _, err := tx.ExecContext(ctx, `
		UPDATE ref.instrument
		SET instrument_type = ?,
		    exchange_mic = ?,
		    currency = ?,
		    name = ?,
		    list_date = COALESCE(?, list_date),
		    delist_date = COALESCE(?, delist_date),
		    updated_at = now()
		WHERE instrument_id = ?
	`, ref.Type, nullableString(ref.ExchangeMIC), nullableString(ref.Currency), nullableString(ref.Name), ref.ListDate, ref.DelistDate, instrumentID); err != nil {
		return 0, fmt.Errorf("refresh canonical instrument: %w", err)
	}
	if identifier.ValidFrom != nil || identifier.ValidTo != nil {
		if _, err := tx.ExecContext(ctx, `
			UPDATE ref.instrument_identifier
			SET valid_from=COALESCE(?, valid_from),
			    valid_to=COALESCE(?, valid_to)
			WHERE instrument_identifier_id=?
		`, identifier.ValidFrom, identifier.ValidTo, identifierRowID); err != nil {
			return 0, fmt.Errorf("refresh instrument identifier validity: %w", err)
		}
	}
	return instrumentID, nil
}

func resolveInstrumentIdentifierTx(ctx context.Context, tx *sql.Tx, identifier domain.Identifier) (instrumentID int64, identifierRowID int64, found bool, err error) {
	var rows *sql.Rows
	if identifier.ValidFrom == nil {
		rows, err = tx.QueryContext(ctx, `
			SELECT instrument_identifier_id, instrument_id
			FROM ref.instrument_identifier
			WHERE provider=? AND identifier_type=? AND identifier_value=? AND valid_to IS NULL
		`, identifier.Provider, identifier.Type, identifier.Value)
	} else {
		rows, err = tx.QueryContext(ctx, `
			SELECT instrument_identifier_id, instrument_id
			FROM ref.instrument_identifier
			WHERE provider=?
			  AND identifier_type=?
			  AND identifier_value=?
			  AND (valid_from IS NULL OR valid_from <= ?)
			  AND (valid_to IS NULL OR valid_to > ?)
		`, identifier.Provider, identifier.Type, identifier.Value, *identifier.ValidFrom, *identifier.ValidFrom)
	}
	if err != nil {
		return 0, 0, false, fmt.Errorf("resolve instrument identifier: %w", err)
	}
	defer rows.Close()
	count := 0
	for rows.Next() {
		if err := rows.Scan(&identifierRowID, &instrumentID); err != nil {
			return 0, 0, false, fmt.Errorf("scan instrument identifier: %w", err)
		}
		count++
		if count > 1 {
			return 0, 0, false, fmt.Errorf("ambiguous active provider identifier %s/%s/%s", identifier.Provider, identifier.Type, identifier.Value)
		}
	}
	if err := rows.Err(); err != nil {
		return 0, 0, false, fmt.Errorf("iterate instrument identifier: %w", err)
	}
	return instrumentID, identifierRowID, count == 1, nil
}

func normalizedIdentifier(identifier domain.Identifier) domain.Identifier {
	if identifier.ValidFrom != nil {
		v := dateUTC(*identifier.ValidFrom)
		identifier.ValidFrom = &v
	}
	if identifier.ValidTo != nil {
		v := dateUTC(*identifier.ValidTo)
		identifier.ValidTo = &v
	}
	return identifier
}

func nullableString(v string) any {
	if strings.TrimSpace(v) == "" {
		return nil
	}
	return v
}
