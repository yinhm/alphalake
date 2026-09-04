package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"github.com/yinhm/alphalake/internal/domain"
)

// ListProviderInstruments returns the provider's current open primary
// identifiers only. valid_to follows half-open interval semantics, so an
// identifier with valid_to=today is already inactive today.
func ListProviderInstruments(ctx context.Context, db *sql.DB, provider string) ([]domain.InstrumentObservation, error) {
	if db == nil {
		return nil, errors.New("duckdb is nil")
	}
	provider = strings.TrimSpace(provider)
	if provider == "" {
		return nil, errors.New("provider is required")
	}

	rows, err := db.QueryContext(ctx, `
		SELECT i.instrument_id, i.instrument_type,
		       i.exchange_mic, i.currency, i.name, i.list_date, i.delist_date,
		       x.provider, x.identifier_type, x.identifier_value, x.valid_from, x.valid_to
		FROM ref.instrument_identifier x
		JOIN ref.instrument i ON i.instrument_id = x.instrument_id
		WHERE x.provider = ? AND x.is_primary = true
		  AND x.valid_to IS NULL
		  AND (x.valid_from IS NULL OR x.valid_from <= current_date)
		ORDER BY i.instrument_id
	`, provider)
	if err != nil {
		return nil, fmt.Errorf("query provider instruments: %w", err)
	}
	defer rows.Close()

	var out []domain.InstrumentObservation
	for rows.Next() {
		var instrument domain.InstrumentRef
		var identifier domain.Identifier
		var instrumentType string
		var mic, currency, name sql.NullString
		var listDate, delistDate, validFrom, validTo sql.NullTime
		if err := rows.Scan(
			&instrument.InstrumentID, &instrumentType,
			&mic, &currency, &name, &listDate, &delistDate,
			&identifier.Provider, &identifier.Type, &identifier.Value, &validFrom, &validTo,
		); err != nil {
			return nil, fmt.Errorf("scan provider instrument: %w", err)
		}
		instrument.Type = domain.InstrumentType(instrumentType)
		instrument.ExchangeMIC = mic.String
		instrument.Currency = currency.String
		instrument.Name = name.String
		if listDate.Valid {
			t := listDate.Time
			instrument.ListDate = &t
		}
		if delistDate.Valid {
			t := delistDate.Time
			instrument.DelistDate = &t
		}
		identifier.InstrumentID = instrument.InstrumentID
		if validFrom.Valid {
			t := validFrom.Time
			identifier.ValidFrom = &t
		}
		if validTo.Valid {
			t := validTo.Time
			identifier.ValidTo = &t
		}
		out = append(out, domain.InstrumentObservation{Instrument: instrument, Identifier: identifier})
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate provider instruments: %w", err)
	}
	return out, nil
}
