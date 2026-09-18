package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"github.com/yinhm/alphalake/internal/source/capital"
)

// PublishShareClasses only accepts reviewed issuer documents, never arbitrary company mappings.
func PublishShareClasses(ctx context.Context, db *sql.DB, runID, artifactID int64, hash string, s capital.Snapshot) (int64, bool, error) {
	if err := capital.Validate(s); err != nil {
		return 0, false, err
	}
	p, err := beginReferencePublication(ctx, db, runID, artifactID, referenceInput{Source: capital.Source, Dataset: capital.Dataset + "/" + s.Code, URL: s.URL, Date: s.ObservationDate, SHA: s.SHA256, ParserVersion: s.ParserVersion, Runtime: s.Runtime, Normalization: "issued-minus-treasury-v1", ParserHash: hash})
	if err != nil {
		return 0, false, err
	}
	defer p.tx.Rollback()
	// A-share identity must already exist. Do not create a parallel financial instrument.
	symbol := "sz300866"
	if s.Code == "600519" {
		symbol = "sh600519"
	}
	var aid int64
	var company sql.NullInt64
	var count int
	err = p.tx.QueryRowContext(ctx, `SELECT count(DISTINCT i.instrument_id),coalesce(min(i.instrument_id),0),min(i.company_id) FROM core.instrument i JOIN core.instrument_identifier x ON x.instrument_id=i.instrument_id WHERE x.provider='tdx' AND x.identifier_type='symbol' AND x.identifier_value=? AND x.valid_to IS NULL AND i.instrument_type='equity'`, symbol).Scan(&count, &aid, &company)
	if err != nil {
		return 0, false, err
	}
	if count != 1 {
		return 0, false, errors.New("sync unambiguous A-share identity before capital publication")
	}
	if !company.Valid {
		if p.existing {
			return 0, false, errors.New("published company association removed")
		}
		err = p.tx.QueryRowContext(ctx, `INSERT INTO core.company(legal_name,country_code) VALUES (?,'CN') RETURNING company_id`, s.LegalName).Scan(&company.Int64)
		if err == nil {
			_, err = p.tx.ExecContext(ctx, `UPDATE core.instrument SET company_id=? WHERE instrument_id=?`, company.Int64, aid)
		}
		if err != nil {
			return 0, false, err
		}
	} else {
		var name string
		if err = p.tx.QueryRowContext(ctx, `SELECT legal_name FROM core.company WHERE company_id=?`, company.Int64).Scan(&name); err != nil {
			return 0, false, err
		}
		if name != s.LegalName {
			return 0, false, errors.New("issuer association conflict")
		}
	}
	for _, c := range s.Classes {
		id := aid
		if c.Kind == "H" {
			err = p.tx.QueryRowContext(ctx, `SELECT count(*),coalesce(min(instrument_id),0) FROM core.instrument WHERE company_id=? AND exchange_mic='XHKG' AND currency='HKD' AND instrument_type='equity'`, company.Int64).Scan(&count, &id)
			if err != nil {
				return 0, false, err
			}
			if count > 1 {
				return 0, false, errors.New("ambiguous H class")
			}
			if count == 0 {
				if p.existing {
					return 0, false, errors.New("published H identity removed")
				}
				err = p.tx.QueryRowContext(ctx, `INSERT INTO core.instrument(instrument_type,exchange_mic,currency,company_id,name) VALUES ('equity','XHKG','HKD',?,?) RETURNING instrument_id`, company.Int64, s.LegalName+" H").Scan(&id)
				if err != nil {
					return 0, false, err
				}
			}
		}
		var listing int64
		err = p.tx.QueryRowContext(ctx, `SELECT listing_id FROM core.listing WHERE instrument_id=? AND exchange_mic=? AND trading_currency=? AND valid_from=? AND artifact_id=?`, id, c.MIC, c.Currency, s.ObservationDate, artifactID).Scan(&listing)
		if errors.Is(err, sql.ErrNoRows) && !p.existing {
			if err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM core.listing WHERE instrument_id=? AND (valid_to IS NULL OR valid_to>?)`, id, s.ObservationDate).Scan(&count); err != nil {
				return 0, false, err
			}
			if count != 0 {
				return 0, false, errors.New("overlapping listing evidence; explicit correction required")
			}
			err = p.tx.QueryRowContext(ctx, `INSERT INTO core.listing(instrument_id,exchange_mic,trading_currency,valid_from,artifact_id) VALUES (?,?,?,?,?) RETURNING listing_id`, id, c.MIC, c.Currency, s.ObservationDate, artifactID).Scan(&listing)
			if err == nil {
				provider := "tdx"
				if c.Kind == "H" {
					provider = "hkex"
				}
				_, err = p.tx.ExecContext(ctx, `INSERT INTO core.listing_identifier(listing_id,provider,identifier_type,identifier_value,market_namespace,valid_from,artifact_id) VALUES (?,?,'symbol',?,?,?,?)`, listing, provider, c.Symbol, c.MIC, s.ObservationDate, artifactID)
			}
		}
		if err != nil {
			return 0, false, err
		}

		provider := "tdx"
		if c.Kind == "H" {
			provider = "hkex"
		}
		err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM core.listing_identifier WHERE listing_id=? AND provider=? AND identifier_type='symbol' AND identifier_value=? AND market_namespace=? AND valid_from=? AND valid_to IS NULL AND artifact_id=?`, listing, provider, c.Symbol, c.MIC, s.ObservationDate, artifactID).Scan(&count)
		if err != nil {
			return 0, false, err
		}
		if count != 1 {
			return 0, false, errors.New("published listing identifier changed")
		}
		for basis, value := range map[string]string{"issued": c.Issued, "treasury": c.Treasury, "outstanding": c.Outstanding} {
			args := []any{p.id, artifactID, id, basis, s.ObservationDate, value, c.Locator}
			if p.existing {
				err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM market.share_count_observation WHERE release_id=? AND artifact_id=? AND instrument_id=? AND share_basis=? AND effective_date=? AND value=CAST(? AS DECIMAL(38,10)) AND source_locator=? AND scope='share_class' AND company_id IS NULL`, args...).Scan(&count)
				if err == nil && count != 1 {
					err = errors.New("published share interpretation changed")
				}
			} else {
				_, err = p.tx.ExecContext(ctx, `INSERT INTO market.share_count_observation(release_id,artifact_id,instrument_id,share_basis,effective_date,value,source_locator,scope) VALUES (?,?,?,?,?,CAST(? AS DECIMAL(38,10)),?,'share_class')`, args...)
			}
			if err != nil {
				return 0, false, err
			}
		}
	}
	if err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM market.share_count_observation WHERE release_id=?`, p.id).Scan(&count); err != nil {
		return 0, false, err
	}
	if count != 3*len(s.Classes) {
		return 0, false, fmt.Errorf("share release incomplete: %d", count)
	}
	return p.finish(ctx)
}
