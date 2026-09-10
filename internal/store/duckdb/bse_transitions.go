package duckdb

import (
	"context"
	"database/sql"
	"errors"

	"github.com/yinhm/alphalake/internal/source/bse"
)

// PublishBSECodeTransitions 原子发布四份官方证据支撑的关系，不写证券身份。
func PublishBSECodeTransitions(ctx context.Context, db *sql.DB, runID int64, artifacts map[string]int64, parserHash string, s bse.Snapshot) (int64, bool, error) {
	if err := s.Validate(); err != nil {
		return 0, false, err
	}
	if len(artifacts) != len(bse.URLs) || artifacts["mapping"] <= 0 {
		return 0, false, errors.New("four BSE artifacts required")
	}
	var supporting []referenceEvidence
	for _, role := range []string{"pilot-list", "pilot-start", "rollout"} {
		kind := "timing"
		if role == "pilot-list" {
			kind = "publication"
		}
		supporting = append(supporting, referenceEvidence{artifacts[role], kind, bse.URLs[role], s.Sources[role]})
	}
	p, err := beginReferencePublication(ctx, db, runID, artifacts["mapping"], referenceInput{Source: bse.Source, Dataset: bse.Dataset, URL: bse.URLs["mapping"], SHA: s.Sources["mapping"], ParserVersion: s.ParserVersion, Runtime: s.Runtime, Normalization: "bse-code-transitions-undated-v1:" + bse.Digest(s), ParserHash: parserHash}, supporting...)
	if err != nil {
		return 0, false, err
	}
	defer p.tx.Rollback()
	if p.existing {
		var count int
		if err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM reference.security_code_transition WHERE release_id=?`, p.id).Scan(&count); err != nil {
			return 0, false, err
		}
		if count != len(s.Transitions) {
			return 0, false, errors.New("published BSE transition count changed")
		}
	}
	for _, r := range s.Transitions {
		args := []any{p.id, artifacts["mapping"], r.SourceRow, r.OldCode, r.NewCode, r.SourceName, r.SourceListingDate, r.SwitchDate}
		if p.existing {
			var count int
			err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM reference.security_code_transition WHERE release_id=? AND artifact_id=? AND source_row=? AND exchange_mic='XBSE' AND old_code=? AND new_code=? AND source_name=? AND source_listing_date=? AND switch_date=CAST(? AS DATE)`, args...).Scan(&count)
			if err != nil {
				return 0, false, err
			}
			if count != 1 {
				return 0, false, errors.New("published BSE transition interpretation changed")
			}
		} else if _, err = p.tx.ExecContext(ctx, `INSERT INTO reference.security_code_transition(release_id,artifact_id,source_row,old_code,new_code,source_name,source_listing_date,switch_date,exchange_mic) VALUES (?,?,?,?,?,?,?,CAST(? AS DATE),'XBSE')`, args...); err != nil {
			return 0, false, err
		}
	}
	return p.finish(ctx)
}
