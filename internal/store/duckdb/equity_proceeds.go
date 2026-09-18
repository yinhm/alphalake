package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"github.com/yinhm/alphalake/internal/source/proceeds"
)

func PublishEquityProceeds(ctx context.Context, db *sql.DB, run, artifactID int64, hash string, s proceeds.Snapshot) (int64, bool, error) {
	if err := proceeds.Validate(s); err != nil {
		return 0, false, err
	}
	p, err := beginReferencePublication(ctx, db, run, artifactID, referenceInput{Source: proceeds.Source, Dataset: proceeds.Dataset + "/" + s.Event, URL: s.URL, Date: s.ObservationDate, SHA: s.SHA256, ParserVersion: s.ParserVersion, Runtime: s.Runtime, Normalization: "issuer-estimated-net-proceeds-v1", ParserHash: hash})
	if err != nil {
		return 0, false, err
	}
	defer p.tx.Rollback()
	var company int64
	var count int
	err = p.tx.QueryRowContext(ctx, `SELECT count(DISTINCT i.company_id),coalesce(min(i.company_id),0) FROM core.instrument i JOIN core.instrument_identifier x ON x.instrument_id=i.instrument_id JOIN core.company co ON co.company_id=i.company_id WHERE x.provider='tdx' AND x.identifier_type='symbol' AND x.identifier_value='sz300866' AND x.valid_to IS NULL AND co.legal_name='安克创新科技股份有限公司'`).Scan(&count, &company)
	if err != nil {
		return 0, false, err
	}
	if count != 1 {
		return 0, false, errors.New("sync reviewed Anker share identity before proceeds")
	}
	args := []any{p.id, artifactID, company, s.Event, s.ObservationDate, s.DateStatus, s.Shares, s.Currency, s.NetProceeds, s.AmountStatus, s.Locator}
	if p.existing {
		err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM market.equity_proceeds_observation WHERE release_id=? AND artifact_id=? AND company_id=? AND event_code=? AND listing_date=? AND date_status=? AND issued_shares=CAST(? AS DECIMAL(38,0)) AND currency=? AND net_proceeds=CAST(? AS DECIMAL(38,10)) AND amount_status=? AND source_locator=?`, args...).Scan(&count)
		if err == nil && count != 1 {
			err = errors.New("published proceeds interpretation changed")
		}
	} else {
		_, err = p.tx.ExecContext(ctx, `INSERT INTO market.equity_proceeds_observation(release_id,artifact_id,company_id,event_code,listing_date,date_status,issued_shares,currency,net_proceeds,amount_status,source_locator) VALUES (?,?,?,?,?,?,CAST(? AS DECIMAL(38,0)),?,CAST(? AS DECIMAL(38,10)),?,?)`, args...)
	}
	if err != nil {
		return 0, false, err
	}
	if err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM market.equity_proceeds_observation WHERE release_id=?`, p.id).Scan(&count); err != nil {
		return 0, false, err
	}
	if count != 1 {
		return 0, false, errors.New("incomplete proceeds release")
	}
	return p.finish(ctx)
}
