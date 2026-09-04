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

// resolveInstrumentIdentifiersAtTx resolves a batch of identifiers at one
// observation date using the same strict temporal semantics as the single-item
// resolver. It performs at most one active-identifier scan per provider and
// treats overlapping intervals as corruption instead of last-row-wins.
func resolveInstrumentIdentifiersAtTx(ctx context.Context, tx *sql.Tx, identifiers []domain.Identifier, asOf time.Time) (map[string]int64, error) {
	if tx == nil {
		return nil, errors.New("transaction is nil")
	}
	if asOf.IsZero() {
		return nil, errors.New("as-of date is required")
	}
	asOf = dateUTC(asOf)
	requestedByProvider := map[string]map[string]struct{}{}
	for _, identifier := range identifiers {
		provider := strings.TrimSpace(identifier.Provider)
		typ := strings.TrimSpace(identifier.Type)
		value := strings.TrimSpace(identifier.Value)
		if provider == "" || typ == "" || value == "" {
			return nil, errors.New("identifier provider, type, and value are required")
		}
		key := providerIdentifierKey(provider, typ, value)
		if requestedByProvider[provider] == nil {
			requestedByProvider[provider] = map[string]struct{}{}
		}
		requestedByProvider[provider][key] = struct{}{}
	}

	out := make(map[string]int64, len(identifiers))
	for provider, requested := range requestedByProvider {
		rows, err := tx.QueryContext(ctx, `
			SELECT instrument_id, provider, identifier_type, identifier_value
			FROM ref.instrument_identifier
			WHERE provider=?
			  AND (valid_from IS NULL OR valid_from <= ?)
			  AND (valid_to IS NULL OR valid_to > ?)
		`, provider, asOf, asOf)
		if err != nil {
			return nil, fmt.Errorf("query temporal identifiers for %q: %w", provider, err)
		}
		for rows.Next() {
			var instrumentID int64
			var p, typ, value string
			if err := rows.Scan(&instrumentID, &p, &typ, &value); err != nil {
				rows.Close()
				return nil, fmt.Errorf("scan temporal identifier: %w", err)
			}
			key := providerIdentifierKey(p, typ, value)
			if _, wanted := requested[key]; !wanted {
				continue
			}
			if existing, duplicate := out[key]; duplicate && existing != instrumentID {
				rows.Close()
				return nil, fmt.Errorf("ambiguous provider identifier %s/%s/%s at %s", p, typ, value, asOf.Format("2006-01-02"))
			}
			out[key] = instrumentID
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			return nil, fmt.Errorf("iterate temporal identifiers for %q: %w", provider, err)
		}
		rows.Close()
	}
	return out, nil
}
