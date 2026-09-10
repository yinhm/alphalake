package duckdb

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/bse"
)

type bseIdentifier struct {
	id                  int64
	mic, kind, currency string
	from, to            sql.NullTime
}
type bseIdentityEvidence struct {
	releaseID   int64
	transitions map[string]bse.Transition
	identifiers map[string][]bseIdentifier
}

// 当前数据库知识中的官方关系及身份快照；不制造历史上市区间。
func loadBSEIdentityEvidence(ctx context.Context, db *sql.DB) (*bseIdentityEvidence, error) {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	candidates, err := tx.QueryContext(ctx, `SELECT release_id FROM (SELECT release_id,dense_rank() OVER(ORDER BY recorded_at DESC) AS rank FROM meta.dataset_release WHERE source=? AND dataset=?) WHERE rank=1`, bse.Source, bse.Dataset)
	if err != nil {
		return nil, err
	}
	var releaseID int64
	count := 0
	for candidates.Next() {
		if err = candidates.Scan(&releaseID); err != nil {
			candidates.Close()
			return nil, err
		}
		count++
	}
	err = candidates.Err()
	candidates.Close()
	if err != nil {
		return nil, err
	}
	if count == 0 {
		return nil, nil
	}
	if count != 1 {
		return nil, errors.New("ambiguous BSE transition release")
	}
	var parser, normalization, key string
	var available time.Time
	err = tx.QueryRowContext(ctx, `SELECT r.parser_version,r.normalization_version,r.content_key,r.available_at FROM meta.dataset_release r
 JOIN meta.checkpoint c ON c.source=r.source AND c.dataset=r.dataset AND c.checkpoint_key=r.content_key AND c.checkpoint_value=CAST(r.release_id AS VARCHAR)
 JOIN meta.ingest_run i ON i.ingest_run_id=r.ingest_run_id AND i.source=r.source AND i.dataset=r.dataset
 WHERE r.release_id=? AND r.source_version IS NULL AND r.source_published_at IS NULL AND r.publication_precision='unknown'
 AND r.availability_basis='first_seen' AND r.available_at=r.first_seen_at AND r.available_at<=current_timestamp AND r.recorded_at<=current_timestamp
 AND i.status='completed' AND i.finished_at<=current_timestamp`, releaseID).Scan(&parser, &normalization, &key, &available)
	if err != nil {
		return nil, fmt.Errorf("BSE publication lineage: %w", err)
	}
	parts := strings.SplitN(normalization, ";", 3)
	if len(parts) != 3 || len(parts[1]) != 64 || parser != bse.ParserVersion {
		return nil, errors.New("unsupported BSE interpretation")
	}
	if _, err = hex.DecodeString(parts[1]); err != nil {
		return nil, err
	}
	snapshot := bse.Snapshot{Contract: bse.ParserVersion, ParserVersion: parser, Runtime: parts[2], Sources: map[string]string{}}
	proofs, err := tx.QueryContext(ctx, `SELECT a.artifact_id,l.role,a.source_locator,a.sha256,a.fetched_at,a.source,a.dataset FROM meta.dataset_release_artifact l JOIN meta.artifact a USING(artifact_id) WHERE l.release_id=?`, releaseID)
	if err != nil {
		return nil, err
	}
	var mainID int64
	var last time.Time
	proofCount := 0
	for proofs.Next() {
		var id int64
		var kind, url, sha, source, dataset string
		var fetched time.Time
		if err = proofs.Scan(&id, &kind, &url, &sha, &fetched, &source, &dataset); err != nil {
			proofs.Close()
			return nil, err
		}
		role := ""
		for k, u := range bse.URLs {
			if url == u {
				role = k
			}
		}
		expected := "timing"
		if role == "mapping" {
			expected = "data"
		}
		if role == "pilot-list" {
			expected = "publication"
		}
		if role == "" || snapshot.Sources[role] != "" || kind != expected || source != bse.Source || dataset != bse.Dataset {
			proofs.Close()
			return nil, errors.New("invalid BSE supporting evidence")
		}
		snapshot.Sources[role] = sha
		if role == "mapping" {
			mainID = id
		}
		if fetched.After(last) {
			last = fetched
		}
		proofCount++
	}
	err = proofs.Err()
	proofs.Close()
	if err != nil {
		return nil, err
	}
	if proofCount != 4 || !last.Equal(available) {
		return nil, errors.New("incomplete BSE evidence availability")
	}
	if fmt.Sprintf("%x", sha256.Sum256([]byte(snapshot.Sources["mapping"]+"\n"+parser+"\n"+normalization))) != key {
		return nil, errors.New("BSE release digest mismatch")
	}
	rows, err := tx.QueryContext(ctx, `SELECT artifact_id,source_row,old_code,new_code,source_name,source_listing_date,CAST(switch_date AS VARCHAR),exchange_mic FROM reference.security_code_transition WHERE release_id=? ORDER BY source_row`, releaseID)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var r bse.Transition
		var artifactID int64
		var mic string
		if err = rows.Scan(&artifactID, &r.SourceRow, &r.OldCode, &r.NewCode, &r.SourceName, &r.SourceListingDate, &r.SwitchDate, &mic); err != nil {
			rows.Close()
			return nil, err
		}
		if artifactID != mainID || mic != "XBSE" {
			rows.Close()
			return nil, errors.New("BSE transition provenance mismatch")
		}
		snapshot.Transitions = append(snapshot.Transitions, r)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	if err = snapshot.Validate(); err != nil {
		return nil, err
	}
	if parts[0] != "bse-code-transitions-undated-v1:"+bse.Digest(snapshot) {
		return nil, errors.New("BSE transition interpretation changed")
	}
	result := &bseIdentityEvidence{releaseID: releaseID, transitions: map[string]bse.Transition{}, identifiers: map[string][]bseIdentifier{}}
	for _, r := range snapshot.Transitions {
		result.transitions[r.OldCode], result.transitions[r.NewCode] = r, r
	}
	identifiers, err := tx.QueryContext(ctx, `SELECT right(x.identifier_value,6),x.instrument_id,coalesce(i.exchange_mic,''),i.instrument_type,coalesce(i.currency,''),x.valid_from,x.valid_to FROM ref.instrument_identifier x JOIN ref.instrument i USING(instrument_id)
 WHERE x.provider='tdx' AND x.identifier_type='symbol' AND starts_with(x.identifier_value,'bj')
 AND EXISTS (SELECT 1 FROM reference.security_code_transition t WHERE t.release_id=? AND x.identifier_value IN ('bj'||t.old_code,'bj'||t.new_code))`, releaseID)
	if err != nil {
		return nil, err
	}
	for identifiers.Next() {
		var code string
		var i bseIdentifier
		if err = identifiers.Scan(&code, &i.id, &i.mic, &i.kind, &i.currency, &i.from, &i.to); err != nil {
			identifiers.Close()
			return nil, err
		}
		result.identifiers[code] = append(result.identifiers[code], i)
	}
	err = identifiers.Err()
	identifiers.Close()
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return result, nil
}

func (e *bseIdentityEvidence) resolve(code string, day time.Time) (int64, string) {
	r, ok := e.transitions[code]
	if !ok {
		return 0, "code outside verified BSE transitions"
	}
	day = dateUTC(day)
	cutover, _ := time.Parse("2006-01-02", r.SwitchDate)
	if code == r.OldCode && !day.Before(cutover) || code == r.NewCode && day.Before(cutover) {
		return 0, "BSE code inconsistent with announcement date"
	}
	identities := map[int64]bool{}
	for _, c := range []string{r.OldCode, r.NewCode} {
		for _, i := range e.identifiers[c] {
			if i.mic != "XBSE" || i.kind != "equity" || i.currency != "CNY" {
				return 0, "BSE identifier metadata conflict"
			}
			identities[i.id] = true
		}
	}
	if len(identities) != 1 {
		return 0, "BSE transition lacks unique known historical instrument"
	}
	active := func(c string, d time.Time) (int64, int) {
		var id int64
		n := 0
		for _, i := range e.identifiers[c] {
			if (!i.from.Valid || !i.from.Time.After(d)) && (!i.to.Valid || i.to.Time.After(d)) {
				id = i.id
				n++
			}
		}
		return id, n
	}
	id, n := active(code, day)
	if n == 0 {
		// 只从官方切换边界的另一端锚定同一证券，不回填或改写TDX区间。
		other, at := r.NewCode, cutover
		if code == r.NewCode {
			other, at = r.OldCode, cutover.AddDate(0, 0, -1)
		}
		id, n = active(other, at)
	}
	if n != 1 {
		return 0, "BSE transition has missing or overlapping temporal identity anchor"
	}
	return id, fmt.Sprintf("bse-transition-v1:release=%d;row=%d;announcement=%s", e.releaseID, r.SourceRow, day.Format("2006-01-02"))
}

// ValidateCNINFOCodeQuery 保留原代码；跨代码须同时满足官方关系、公告日期、机构及唯一身份。
func ValidateCNINFOCodeQuery(ctx context.Context, db *sql.DB, code, org string, filings []domain.FilingObservation) error {
	var evidence *bseIdentityEvidence
	var sourceErr error
	for _, f := range filings {
		if f.ExchangeMIC == "XBSE" {
			evidence, sourceErr = loadBSEIdentityEvidence(ctx, db)
			break
		}
	}
	for _, f := range filings {
		if f.ProviderOrgID != org {
			return fmt.Errorf("CNINFO code query %s returned another organization %q", code, f.ProviderOrgID)
		}
		if f.ProviderCode == code {
			if evidence != nil && f.ExchangeMIC == "XBSE" {
				if r, ok := evidence.transitions[code]; ok {
					cutover, _ := time.Parse("2006-01-02", r.SwitchDate)
					day := filingResolutionDate(f)
					if code == r.OldCode && !day.Before(cutover) || code == r.NewCode && day.Before(cutover) {
						return errors.New("CNINFO code inconsistent with official announcement-date transition")
					}
				}
			}
			continue
		}
		if sourceErr != nil {
			return fmt.Errorf("BSE code query evidence: %w", sourceErr)
		}
		accepted := false
		if evidence != nil && f.ExchangeMIC == "XBSE" {
			if r, ok := evidence.transitions[code]; ok && (f.ProviderCode == r.OldCode || f.ProviderCode == r.NewCode) {
				id, _ := evidence.resolve(f.ProviderCode, filingResolutionDate(f))
				accepted = id > 0
			}
		}
		if !accepted {
			return fmt.Errorf("CNINFO code query %s returned another security %q without verified temporal identity", code, f.ProviderCode)
		}
	}
	return nil
}
