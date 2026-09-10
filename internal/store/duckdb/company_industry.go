package duckdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"

	"github.com/yinhm/alphalake/internal/source/damodaran"
)

// PublishCompanyIndustries 只发布来源观察，不改变证券身份或标准分类成员。
func PublishCompanyIndustries(ctx context.Context, db *sql.DB, runID, artifactID int64, parserHash string, s damodaran.CompanyIndustrySnapshot) (int64, bool, error) {
	if err := damodaran.ValidateCompanyIndustries(s); err != nil {
		return 0, false, err
	}
	p, err := beginReferencePublication(ctx, db, runID, artifactID, referenceInput{Source: damodaran.Source, Dataset: damodaran.CompanyIndustryDataset, URL: damodaran.CompanyIndustryURL, SHA: s.SHA256, ParserVersion: s.ParserVersion, Runtime: s.Runtime, Normalization: "source-company-industries-undated-v1", ParserHash: parserHash})
	if err != nil {
		return 0, false, err
	}
	defer p.tx.Rollback()
	nodes, err := damodaranIndustryNodes(ctx, p.tx, p.existing, s.Industries)
	if err != nil {
		return 0, false, err
	}
	if p.existing {
		var count int
		if err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM reference.security_industry WHERE release_id=?`, p.id).Scan(&count); err != nil {
			return 0, false, err
		}
		if count != len(s.Companies) {
			return 0, false, errors.New("published company industry count changed")
		}
	}
	for _, c := range s.Companies {
		raw, err := json.Marshal(c)
		if err != nil {
			return 0, false, err
		}
		args := []any{p.id, artifactID, c.SourceLocator, c.Ticker, nodes[c.Industry], string(raw)}
		if p.existing {
			var count int
			err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM reference.security_industry WHERE release_id=? AND artifact_id=? AND source_locator=? AND exchange_ticker=? AND industry_node_id=? AND raw_payload=?`, args...).Scan(&count)
			if err != nil {
				return 0, false, err
			}
			if count != 1 {
				return 0, false, errors.New("published company industry interpretation changed")
			}
		} else {
			if _, err = p.tx.ExecContext(ctx, `INSERT INTO reference.security_industry(release_id,artifact_id,source_locator,exchange_ticker,industry_node_id,raw_payload) VALUES (?,?,?,?,?,?)`, args...); err != nil {
				return 0, false, err
			}
		}
	}
	return p.finish(ctx)
}
