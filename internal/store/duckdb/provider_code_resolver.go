package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
)

// ProviderCodeResolution resolves a raw provider code that lacks a verified
// exchange marker. A unique temporal provider symbol resolves to one canonical
// instrument. Multiple distinct provider symbols are a legitimate ambiguity of
// the raw evidence and remain unresolved rather than being guessed.
type ProviderCodeResolution struct {
	InstrumentID    int64
	IdentifierValue string
	Candidates      []string
}

func (r ProviderCodeResolution) Resolved() bool {
	return r.InstrumentID > 0 && r.IdentifierValue != "" && len(r.Candidates) == 1
}

// ResolveProviderCodesAt resolves six-digit raw provider codes by querying the
// provider's temporal symbol identifiers at the observation date. It does not
// consult current code-range classifiers, so historical B-share/legacy-market
// records are not rejected merely because today's SDK does not classify them.
func ResolveProviderCodesAt(ctx context.Context, db *sql.DB, provider string, codes []string, asOf time.Time) ([]ProviderCodeResolution, error) {
	if db == nil {
		return nil, errors.New("duckdb is nil")
	}
	provider = strings.TrimSpace(provider)
	if provider == "" || asOf.IsZero() {
		return nil, errors.New("provider and as-of date are required")
	}
	if len(codes) == 0 {
		return []ProviderCodeResolution{}, nil
	}
	wanted := make(map[string]struct{}, len(codes))
	for i, code := range codes {
		code = strings.TrimSpace(code)
		if !sixDigitProviderCode(code) {
			return nil, fmt.Errorf("provider code %d %q is not six digits", i, code)
		}
		wanted[code] = struct{}{}
	}
	asOf = dateUTC(asOf)
	rows, err := db.QueryContext(ctx, `
		SELECT instrument_id, identifier_value
		FROM ref.instrument_identifier
		WHERE provider=?
		  AND identifier_type='symbol'
		  AND (valid_from IS NULL OR valid_from <= ?)
		  AND (valid_to IS NULL OR valid_to > ?)
	`, provider, asOf, asOf)
	if err != nil {
		return nil, fmt.Errorf("query temporal provider symbols: %w", err)
	}
	defer rows.Close()

	type candidate struct {
		instrumentID int64
		identifier   string
	}
	byCode := make(map[string]map[string]candidate, len(wanted))
	for rows.Next() {
		var instrumentID int64
		var identifier string
		if err := rows.Scan(&instrumentID, &identifier); err != nil {
			return nil, fmt.Errorf("scan temporal provider symbol: %w", err)
		}
		identifier = strings.TrimSpace(identifier)
		if len(identifier) < 6 {
			continue
		}
		code := identifier[len(identifier)-6:]
		if _, ok := wanted[code]; !ok {
			continue
		}
		m := byCode[code]
		if m == nil {
			m = make(map[string]candidate)
			byCode[code] = m
		}
		if prior, exists := m[identifier]; exists && prior.instrumentID != instrumentID {
			return nil, fmt.Errorf("ambiguous active provider identifier %s/symbol/%s at %s", provider, identifier, asOf.Format("2006-01-02"))
		}
		m[identifier] = candidate{instrumentID: instrumentID, identifier: identifier}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate temporal provider symbols: %w", err)
	}

	out := make([]ProviderCodeResolution, len(codes))
	for i, raw := range codes {
		code := strings.TrimSpace(raw)
		candidates := byCode[code]
		if len(candidates) == 0 {
			continue
		}
		names := make([]string, 0, len(candidates))
		for identifier := range candidates {
			names = append(names, identifier)
		}
		sort.Strings(names)
		out[i].Candidates = names
		if len(names) == 1 {
			item := candidates[names[0]]
			out[i].InstrumentID = item.instrumentID
			out[i].IdentifierValue = item.identifier
		}
	}
	return out, nil
}

func sixDigitProviderCode(code string) bool {
	if len(code) != 6 {
		return false
	}
	for _, r := range code {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}
