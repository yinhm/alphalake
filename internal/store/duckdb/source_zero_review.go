package duckdb

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

// ReviewedSourceZero binds a human-confirmed zero to one immutable source row
// and semantic mapping. It is supplemental evidence, never a blanket zero rule.
type ReviewedSourceZero struct {
	Field          string `json:"field"`
	ArtifactSHA256 string `json:"artifact_sha256"`
	SourceRow      int    `json:"source_row"`
	MappingSHA256  string `json:"mapping_sha256"`
	Conclusion     string `json:"conclusion"`
}

func sameSourceZero(a, b *ReviewedSourceZero) bool {
	if a == nil || b == nil {
		return a == b
	}
	return *a == *b
}

type sourceZeroValidator struct {
	root, hash string
	pkg        financial.Package
}

func (v *sourceZeroValidator) validate(ctx context.Context, tx *sql.Tx, r ReviewedSupplement, filingID int64, asof time.Time) (map[string]any, error) {
	z := r.SourceZero
	value, ok := new(big.Rat).SetString(r.Value)
	if z == nil || !ok || value.Sign() != 0 || !standardSnapshotName.MatchString(z.Field) || r.Item != "reviewed_source_zero_"+z.Field || r.Unit != "CNY" || r.PeriodBasis != "instant" || r.Scope != "consolidated_statement" || z.SourceRow < 1 || z.Conclusion != "explicit_zero_balance" || r.ReviewedAt == "" {
		return nil, fmt.Errorf("explicit, dated consolidated monetary zero review required")
	}
	for _, s := range []string{z.ArtifactSHA256, z.MappingSHA256} {
		b, e := hex.DecodeString(s)
		if e != nil || len(b) != 32 {
			return nil, fmt.Errorf("invalid source zero fingerprint")
		}
	}
	var id, instrument, size, pdfSize int64
	var index int
	var path, name, mapping, pdfPath, pdfHash string
	var announced time.Time
	// A review cannot repair missing identity, mapping, disclosure coverage or a
	// newer source record. Only the existing ambiguous-zero rejection is eligible.
	err := tx.QueryRowContext(ctx, `SELECT s.source_record_id,s.instrument_id,a.local_path,a.source_locator,a.content_length,
 CAST(substr(m.provider_field,3) AS INTEGER),sha256(CAST(to_json(m) AS VARCHAR)),p.local_path,p.sha256,p.content_length,f.announcement_time
 FROM fundamental.statement_snapshot s JOIN fundamental.source_record sr USING(source_record_id)
 JOIN meta.artifact a USING(artifact_id)
 JOIN fundamental.filing f ON f.filing_id=s.source_filing_id
 JOIN meta.artifact p ON p.artifact_id=f.artifact_id
 JOIN fundamental.statement_field m ON m.source='tdx' AND m.canonical_field=? AND m.valid_from<=s.report_period AND (m.valid_to IS NULL OR s.report_period<m.valid_to)
 JOIN fundamental.provider_field current ON current.source=m.source AND current.provider_field=m.provider_field AND current.valid_from=m.valid_from AND current IS NOT DISTINCT FROM m
 JOIN fundamental.field d ON d.canonical_field=m.canonical_field AND d.unit=m.unit AND d.period_basis=m.period_basis AND d.value_kind=m.value_kind
 WHERE sr.provider_code=? AND sr.report_period=CAST(? AS DATE) AND a.sha256=? AND sr.source_row=?
 AND a.source='tdx' AND a.dataset='professional_financial' AND sr.instrument_id=s.instrument_id
 AND f.filing_id=? AND f.provider_code=sr.provider_code AND f.report_period=sr.report_period
 AND f.instrument_id=s.instrument_id AND f.resolution_status='resolved' AND f.sha256=? AND p.sha256=f.sha256
 AND m.unit=? AND m.period_basis=? AND m.value_kind='monetary' AND m.zero_policy='reject'
 AND m.value_multiplier IN (1,10000) AND s."`+z.Field+`" IS NULL
 AND EXISTS(SELECT 1 FROM fundamental.statement_rejection e WHERE e.source_record_id=s.source_record_id AND e.rule_code='provider_zero_ambiguous' AND list_contains(e.fields,m.canonical_field))
 AND NOT EXISTS(SELECT 1 FROM fundamental.statement_snapshot newer WHERE newer.instrument_id=s.instrument_id AND newer.report_period=s.report_period AND newer.announcement_time<=? AND (newer.announcement_time,newer.source_record_id)>(s.announcement_time,s.source_record_id))`, z.Field, r.Code, r.Period, z.ArtifactSHA256, z.SourceRow, filingID, r.PDFSHA256, r.Unit, r.PeriodBasis, asof).Scan(&id, &instrument, &path, &name, &size, &index, &mapping, &pdfPath, &pdfHash, &pdfSize, &announced)
	if err != nil {
		return nil, err
	}
	if mapping != z.MappingSHA256 {
		return nil, sql.ErrNoRows
	}
	if v.hash != z.ArtifactSHA256 {
		v.pkg, err = ReadFinancialArchive(v.root, path, name, z.ArtifactSHA256, size)
		if err != nil {
			return nil, err
		}
		v.hash = z.ArtifactSHA256
	}
	if z.SourceRow > len(v.pkg.Records) {
		return nil, fmt.Errorf("source row absent")
	}
	row := v.pkg.Records[z.SourceRow-1]
	if row.Code != r.Code || row.ReportPeriod.Format("2006-01-02") != r.Period || index < 1 || index > len(row.Fields) || row.Fields[index-1].Value != 0 {
		return nil, fmt.Errorf("review does not reference a source zero")
	}
	if filepath.IsAbs(pdfPath) || strings.HasPrefix(filepath.Clean(pdfPath), "..") {
		return nil, fmt.Errorf("invalid PDF locator")
	}
	raw, err := os.ReadFile(filepath.Join(v.root, filepath.FromSlash(pdfPath)))
	if err != nil {
		return nil, err
	}
	if int64(len(raw)) != pdfSize || fmt.Sprintf("%x", sha256.Sum256(raw)) != pdfHash || !strings.HasPrefix(string(raw), "%PDF-") {
		return nil, fmt.Errorf("review PDF integrity failure")
	}
	return map[string]any{"code": r.Code, "instrument_id": instrument, "period": r.Period, "field": z.Field, "value": "0", "unit": r.Unit, "period_type": r.PeriodBasis, "statement_scope": r.Scope, "source_record_id": id, "source_filing_id": filingID, "artifact_sha256": z.ArtifactSHA256, "available_at": announced.UTC().Format(time.RFC3339Nano), "review": r}, nil
}

func exportReviewedSourceZeros(ctx context.Context, tx *sql.Tx, root, dir string, fields []string, from, end, asof time.Time) error {
	rows, err := tx.QueryContext(ctx, `SELECT reviewed_record,source_filing_id,import_sha256 FROM (
 SELECT s.*,row_number() OVER(PARTITION BY s.provider_code,s.report_period,s.item ORDER BY f.announcement_time DESC,f.filing_id DESC) AS rank
 FROM fundamental.reviewed_supplement s JOIN fundamental.filing f ON f.filing_id=s.source_filing_id
 WHERE f.instrument_id IN(SELECT instrument_id FROM _sqlite_universe) AND s.report_period BETWEEN ? AND ? AND f.announcement_time<=?
 AND s.item LIKE 'reviewed_source_zero_%'
 ) WHERE rank=1 AND review_state='active' ORDER BY json_extract_string(reviewed_record,'$.source_zero.artifact_sha256'),provider_code,report_period,item`, from, end, asof)
	if err != nil {
		return err
	}
	type pending struct {
		raw, hash string
		filing    int64
	}
	var records []pending
	for rows.Next() {
		var r pending
		if err = rows.Scan(&r.raw, &r.filing, &r.hash); err != nil {
			rows.Close()
			return err
		}
		records = append(records, r)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	out, err := os.Create(filepath.Join(dir, "reviewed_zeros.jsonl"))
	if err != nil {
		return err
	}
	defer out.Close()
	encoder := json.NewEncoder(out)
	validator := sourceZeroValidator{root: root}
	wanted := map[string]bool{}
	for _, f := range fields {
		wanted[f] = true
	}
	for _, p := range records {
		var r ReviewedSupplement
		if err = json.Unmarshal([]byte(p.raw), &r); err != nil {
			return err
		}
		if r.SourceZero == nil || !wanted[r.SourceZero.Field] {
			continue
		}
		evidence, e := validator.validate(ctx, tx, r, p.filing, asof)
		if errors.Is(e, sql.ErrNoRows) {
			continue
		}
		if e != nil {
			return e
		}
		evidence["import_sha256"] = p.hash
		if err = encoder.Encode(evidence); err != nil {
			return err
		}
	}
	return out.Close()
}
