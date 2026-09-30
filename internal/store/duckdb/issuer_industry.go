package duckdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/yinhm/alphalake/internal/source/damodaran"
)

type IssuerIndustryDocument struct {
	LocalPath           string `json:"local_path"`
	SHA256              string `json:"sha256"`
	CataloguePath       string `json:"catalogue_path"`
	CatalogueSHA256     string `json:"catalogue_sha256"`
	AnnouncementID      string `json:"announcement_id"`
	OrganizationID      string `json:"organization_id"`
	SourceURL           string `json:"source_url"`
	ArtifactID          int64  `json:"artifact_id"`
	CatalogueArtifactID int64  `json:"catalogue_artifact_id"`
}

type IssuerIndustryAssociation struct {
	TargetTicker              string                    `json:"target_ticker"`
	SourceTicker              string                    `json:"source_ticker"`
	SourceLocator             string                    `json:"source_locator"`
	ReviewSHA256              string                    `json:"review_sha256"`
	ReviewedAt                time.Time                 `json:"reviewed_at"`
	Reviewer                  string                    `json:"reviewer"`
	Relationship              string                    `json:"relationship"`
	SourceCompany             damodaran.CompanyIndustry `json:"source_company"`
	Document                  IssuerIndustryDocument    `json:"document"`
	InstrumentID              int64                     `json:"instrument_id"`
	SourceReleaseID           int64                     `json:"source_release_id"`
	AssociationArtifactID     int64                     `json:"association_artifact_id"`
	AssociationArtifactSHA256 string                    `json:"association_artifact_sha256"`
	WorkbookSHA256            string                    `json:"workbook_sha256"`
}

type IssuerIndustryReviewEvent struct {
	ReviewSHA256   string          `json:"review_sha256"`
	ReviewedRecord json.RawMessage `json:"reviewed_record"`
}

type IssuerIndustryPacket struct {
	Contract       string                      `json:"contract"`
	WorkbookSHA256 string                      `json:"workbook_sha256"`
	ManifestSHA256 string                      `json:"review_manifest_sha256"`
	CoverageSHA256 string                      `json:"coverage_sha256"`
	SourceUniverse int                         `json:"source_universe"`
	Active         []IssuerIndustryAssociation `json:"active"`
	ReviewEvents   []IssuerIndustryReviewEvent `json:"review_events"`
	Revoked        []string                    `json:"revoked"`
}

// ImportIssuerIndustryReview accepts only the result of the CLI's local evidence verifier.
// A new manifest must retain the preceding ledger; omission is not revocation.
func ImportIssuerIndustryReview(ctx context.Context, db *sql.DB, artifactID int64, packet IssuerIndustryPacket) (bool, error) {
	if packet.Contract != "alphalake-reviewed-issuer-inputs-v1" || len(packet.ReviewEvents) == 0 {
		return false, errors.New("verified issuer review ledger required")
	}
	raw, err := json.Marshal(packet)
	if err != nil {
		return false, err
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	var verified int
	if err = tx.QueryRowContext(ctx, `SELECT count(*) FROM meta.artifact WHERE artifact_id=? AND source='issuer-review' AND dataset='company_industry' AND sha256=sha256(?)`, artifactID, string(raw)).Scan(&verified); err != nil {
		return false, err
	}
	if verified != 1 {
		return false, errors.New("issuer verification artifact differs")
	}
	var previous string
	var intact bool
	err = tx.QueryRowContext(ctx, `SELECT v.verified_record,a.sha256=sha256(v.verified_record) FROM reference.issuer_industry_review v JOIN meta.artifact a USING(artifact_id) ORDER BY v.recorded_at DESC,v.artifact_id DESC LIMIT 1`).Scan(&previous, &intact)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return false, err
	}
	if err == nil {
		if !intact {
			return false, errors.New("previous issuer review snapshot hash mismatch")
		}
		var prior IssuerIndustryPacket
		if err = json.Unmarshal([]byte(previous), &prior); err != nil {
			return false, err
		}
		if len(packet.ReviewEvents) < len(prior.ReviewEvents) {
			return false, errors.New("cannot omit issuer review history; revoke explicitly")
		}
		for i, e := range prior.ReviewEvents {
			if packet.ReviewEvents[i].ReviewSHA256 != e.ReviewSHA256 {
				return false, errors.New("issuer review history changed")
			}
		}
	}
	var published int
	if err = tx.QueryRowContext(ctx, `SELECT count(*) FROM meta.dataset_release r JOIN meta.dataset_release_artifact l USING(release_id) JOIN meta.artifact a USING(artifact_id) JOIN meta.ingest_run i ON i.ingest_run_id=r.ingest_run_id JOIN meta.checkpoint c ON c.source=r.source AND c.dataset=r.dataset AND c.checkpoint_key=r.content_key AND c.checkpoint_value=CAST(r.release_id AS VARCHAR) WHERE r.source='damodaran' AND a.source='damodaran' AND a.dataset=r.dataset AND r.dataset=? AND a.sha256=? AND l.role='data' AND i.status='completed'`, damodaran.CompanyIndustryDataset, packet.WorkbookSHA256).Scan(&published); err != nil {
		return false, err
	}
	if published == 0 {
		return false, errors.New("issuer review workbook is not a published company source")
	}
	var exists int
	if err = tx.QueryRowContext(ctx, `SELECT count(*) FROM reference.issuer_industry_review WHERE artifact_id=?`, artifactID).Scan(&exists); err != nil {
		return false, err
	}
	if exists != 0 {
		return false, nil
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO reference.issuer_industry_review(artifact_id,workbook_sha256,verified_record) VALUES (?,?,?)`, artifactID, packet.WorkbookSHA256, string(raw)); err != nil {
		return false, err
	}
	return true, tx.Commit()
}

// issuerIndustryAssociations resolves the reviewed A identity in one batch.
// The latest ledger wins even when revoked or stale; never fall back to an older ledger.
func issuerIndustryAssociations(ctx context.Context, tx *sql.Tx, releaseID int64, sha string, asof time.Time) ([]IssuerIndustryAssociation, error) {
	out := []IssuerIndustryAssociation{}
	var record, hash, workbook string
	var artifactID int64
	err := tx.QueryRowContext(ctx, `SELECT v.verified_record,a.sha256,v.workbook_sha256,v.artifact_id FROM reference.issuer_industry_review v JOIN meta.artifact a USING(artifact_id) WHERE v.recorded_at<=? ORDER BY v.recorded_at DESC,v.artifact_id DESC LIMIT 1`, asof).Scan(&record, &hash, &workbook, &artifactID)
	if errors.Is(err, sql.ErrNoRows) {
		return out, nil
	}
	if err != nil {
		return nil, err
	}
	var valid int
	if err = tx.QueryRowContext(ctx, `SELECT count(*) FROM meta.artifact WHERE artifact_id=? AND sha256=sha256(?) AND source='issuer-review' AND dataset='company_industry'`, artifactID, record).Scan(&valid); err != nil {
		return nil, err
	}
	if valid != 1 {
		return nil, errors.New("issuer review snapshot hash mismatch")
	}
	var packet IssuerIndustryPacket
	if err = json.Unmarshal([]byte(record), &packet); err != nil {
		return nil, err
	}
	if packet.Contract != "alphalake-reviewed-issuer-inputs-v1" || packet.WorkbookSHA256 != workbook {
		return nil, errors.New("issuer review contract/workbook mismatch")
	}
	if workbook != sha {
		return out, nil
	}
	raw, err := json.Marshal(packet.Active)
	if err != nil {
		return nil, err
	}
	rows, err := tx.QueryContext(ctx, `WITH targets AS (SELECT value->>'target_ticker' AS ticker,CAST(value->>'reviewed_at' AS TIMESTAMPTZ) AS reviewed_at FROM json_each(?)), history AS (SELECT identifier_value,count(DISTINCT instrument_id) AS identities FROM core.instrument_identifier WHERE provider='tdx' AND identifier_type='symbol' GROUP BY identifier_value)
 SELECT t.ticker,count(d.instrument_id),min(d.instrument_id),min(i.exchange_mic),min(i.instrument_type),min(i.currency),min(h.identities)
 FROM targets t LEFT JOIN core.instrument_identifier d ON d.provider='tdx' AND d.identifier_type='symbol' AND d.identifier_value=(CASE WHEN starts_with(t.ticker,'SHSE:') THEN 'sh' ELSE 'sz' END)||split_part(t.ticker,':',2)
 AND (d.valid_from IS NULL OR d.valid_from<=CAST(t.reviewed_at AT TIME ZONE 'Asia/Shanghai' AS DATE)) AND (d.valid_to IS NULL OR d.valid_to>CAST(t.reviewed_at AT TIME ZONE 'Asia/Shanghai' AS DATE))
 LEFT JOIN core.instrument i USING(instrument_id) LEFT JOIN history h USING(identifier_value)
 LEFT JOIN reference.security_industry o ON o.release_id=? AND o.exchange_ticker=t.ticker
 WHERE o.observation_id IS NULL AND t.reviewed_at<=? GROUP BY t.ticker`, string(raw), releaseID, asof)
	if err != nil {
		return nil, err
	}
	ids := map[string]int64{}
	for rows.Next() {
		var ticker string
		var n int
		var id, h sql.NullInt64
		var mic, kind, currency sql.NullString
		if err = rows.Scan(&ticker, &n, &id, &mic, &kind, &currency, &h); err != nil {
			rows.Close()
			return nil, err
		}
		expected := "XSHE"
		if len(ticker) > 5 && ticker[:5] == "SHSE:" {
			expected = "XSHG"
		}
		if n == 1 && h.Int64 == 1 && mic.String == expected && kind.String == "equity" && currency.String == "CNY" {
			ids[ticker] = id.Int64
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	seen := map[string]bool{}
	for _, a := range packet.Active {
		if seen[a.TargetTicker] {
			return nil, errors.New("duplicate reviewed issuer target")
		}
		seen[a.TargetTicker] = true
		if a.SourceCompany.Ticker != a.SourceTicker || a.SourceCompany.SourceLocator != a.SourceLocator || a.Relationship != "same_legal_issuer_different_share_class" {
			return nil, errors.New("issuer source row differs")
		}
		if ids[a.TargetTicker] == 0 {
			continue
		}
		a.InstrumentID = ids[a.TargetTicker]
		a.SourceReleaseID = releaseID
		a.AssociationArtifactID = artifactID
		a.AssociationArtifactSHA256 = hash
		a.WorkbookSHA256 = sha
		out = append(out, a)
	}
	return out, nil
}

func issuerAssociationMembership(a IssuerIndustryAssociation, nodeID int64) map[string]any {
	return map[string]any{"source": damodaran.Source, "taxonomy_code": damodaran.BetaTaxonomy, "node_code": a.SourceCompany.Industry, "node_name": a.SourceCompany.Industry, "node_id": nodeID,
		"source_release_id": a.SourceReleaseID, "artifact_sha256": a.WorkbookSHA256, "source_ticker": a.SourceTicker, "source_locator": a.SourceLocator,
		"identity_basis": "reviewed_same_legal_issuer_different_share_class", "review_sha256": a.ReviewSHA256, "reviewer": a.Reviewer, "reviewed_at": a.ReviewedAt.Format(time.RFC3339Nano), "association_artifact_id": a.AssociationArtifactID, "association_artifact_sha256": a.AssociationArtifactSHA256, "document": a.Document}
}

func issuerCompanyRelease(ctx context.Context, tx *sql.Tx, idsJSON string) (int64, string, error) {
	var id int64
	var sha string
	err := tx.QueryRowContext(ctx, `SELECT r.release_id,a.sha256 FROM meta.dataset_release r JOIN meta.dataset_release_artifact l USING(release_id) JOIN meta.artifact a USING(artifact_id) WHERE r.release_id IN (SELECT unnest(CAST(? AS BIGINT[]))) AND r.dataset=? AND l.role='data'`, idsJSON, damodaran.CompanyIndustryDataset).Scan(&id, &sha)
	if err != nil {
		return 0, "", fmt.Errorf("issuer reference company release: %w", err)
	}
	return id, sha, nil
}
