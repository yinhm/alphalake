package duckdb

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"
)

// ReviewedSupplement 是已有原文人工核验后的源补充，不是模型假设。
type ReviewedSupplement struct {
	Code           string `json:"code"`
	Period         string `json:"period"`
	Item           string `json:"item"`
	Value          string `json:"value"`
	Unit           string `json:"unit"`
	PeriodBasis    string `json:"period_basis"`
	Scope          string `json:"scope"`
	AnnouncementID string `json:"announcement_id"`
	PDFSHA256      string `json:"pdf_sha256"`
	PDFPage        int    `json:"pdf_page"`
	Reviewer       string `json:"reviewer"`
	ReviewNote     string `json:"review_note"`
}

var sixDigitCode = regexp.MustCompile(`^[0-9]{6}$`)
var decimalAmount = regexp.MustCompile(`^-?[0-9]{1,28}(\.[0-9]{1,10})?$`)
var supplementItem = regexp.MustCompile(`^[a-z][a-z0-9_]*$`)

// ImportReviewedSupplements 原子导入；同一原文/项目改值必须先重新审核，不覆盖旧证据。
func ImportReviewedSupplements(ctx context.Context, db *sql.DB, records []ReviewedSupplement) (int, error) {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	inserted := 0
	for _, r := range records {
		if !sixDigitCode.MatchString(r.Code) || !supplementItem.MatchString(r.Item) || !decimalAmount.MatchString(r.Value) || strings.TrimSpace(r.Reviewer) == "" || strings.TrimSpace(r.ReviewNote) == "" || r.PDFPage <= 0 {
			return 0, errors.New("invalid reviewed supplement")
		}
		if _, err := time.Parse("2006-01-02", r.Period); err != nil {
			return 0, err
		}
		hash, err := hex.DecodeString(r.PDFSHA256)
		if err != nil || len(hash) != 32 {
			return 0, errors.New("invalid PDF SHA256")
		}
		if r.Unit != "CNY" && r.Unit != "CNY/share" {
			return 0, errors.New("unsupported supplement unit")
		}
		if r.PeriodBasis != "instant" && r.PeriodBasis != "ytd" {
			return 0, errors.New("unsupported supplement period basis")
		}
		switch r.Scope {
		case "consolidated_note_component", "consolidated_statement", "finance_subsidiary", "convertible_security":
		default:
			return 0, errors.New("unsupported supplement scope")
		}
		var filingID int64
		err = tx.QueryRowContext(ctx, `SELECT f.filing_id FROM fundamental.filing f JOIN meta.artifact a ON a.artifact_id=f.artifact_id
   WHERE f.source='cninfo' AND f.source_filing_id=? AND f.provider_code=? AND f.sha256=? AND a.sha256=f.sha256
    AND f.announcement_time IS NOT NULL AND f.resolution_status='resolved' AND f.instrument_id IS NOT NULL
    AND (f.report_period=CAST(? AS DATE) OR (f.report_period IS NULL AND ?='finance_subsidiary'))`, r.AnnouncementID, r.Code, r.PDFSHA256, r.Period, r.Scope).Scan(&filingID)
		if err != nil {
			return 0, fmt.Errorf("supplement %s/%s/%s has no matching archived filing: %w", r.Code, r.Period, r.Item, err)
		}
		raw, err := json.Marshal(r)
		if err != nil {
			return 0, err
		}
		sum := sha256.Sum256(raw)
		sha := hex.EncodeToString(sum[:])
		var previous string
		err = tx.QueryRowContext(ctx, `SELECT import_sha256 FROM fundamental.reviewed_supplement WHERE provider_code=? AND report_period=CAST(? AS DATE) AND item=? AND source_filing_id=?`, r.Code, r.Period, r.Item, filingID).Scan(&previous)
		if err == nil {
			if previous != sha {
				return 0, fmt.Errorf("conflicting reviewed supplement %s/%s/%s", r.Code, r.Period, r.Item)
			}
			continue
		}
		if !errors.Is(err, sql.ErrNoRows) {
			return 0, err
		}
		_, err = tx.ExecContext(ctx, `INSERT INTO fundamental.reviewed_supplement VALUES (?,CAST(? AS DATE),?,CAST(? AS DECIMAL(38,10)),?,?,?,?,?,?,?,?,?,?)`, r.Code, r.Period, r.Item, r.Value, r.Unit, r.PeriodBasis, r.Scope, filingID, r.PDFSHA256, r.PDFPage, r.Reviewer, r.ReviewNote, sha, string(raw))
		if err != nil {
			return 0, err
		}
		inserted++
	}
	if err = tx.Commit(); err != nil {
		return 0, err
	}
	return inserted, nil
}

// ExportValuationData 查询同一事务快照，数值以十进制字符串交付，未知证券返回空数据。
func ExportValuationData(ctx context.Context, db *sql.DB, code string, end, asof time.Time) (map[string]any, error) {
	if !sixDigitCode.MatchString(code) || end.IsZero() || asof.IsZero() {
		return nil, errors.New("code, report period and ASOF are required")
	}
	if end.AddDate(0, 0, 1).Day() != 1 || int(end.Month())%3 != 0 {
		return nil, errors.New("report period must be quarter end")
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	output := map[string]any{"contract_version": "alphalake-valuation-v1", "code": code, "report_period": end.Format("2006-01-02"), "information_as_of": asof.UTC().Format(time.RFC3339Nano)}
	queries := []struct {
		name, query string
		args        []any
	}{
		{"facts", `SELECT CAST(to_json(list(x)) AS VARCHAR) FROM (
    SELECT f.instrument_id,f.provider_code AS code,CAST(f.report_period AS VARCHAR) AS period,f.source_provider_field AS field,
      f.canonical_field,CAST(f.value AS VARCHAR) AS value,f.unit,f.period_type,f.statement_scope,
      f.fact_id,f.revision_key,f.normalization_rule,f.materializer_version,
      CAST(f.announcement_time AS VARCHAR) AS available_at,a.sha256 AS artifact_sha256,
      p.value_float32_bits AS bits,m.value_multiplier AS multiplier,
      d.source_filing_id AS announcement_id,d.sha256 AS pdf_sha256,d.source_url AS pdf_url
    FROM fundamental.fact_asof(CAST(? AS TIMESTAMPTZ)) f
    JOIN fundamental.provider_fact p ON p.provider_fact_id=f.provider_fact_id
    JOIN meta.artifact a ON a.artifact_id=p.artifact_id
    JOIN fundamental.filing d ON d.filing_id=f.source_filing_id
    JOIN fundamental.provider_field m ON m.source=f.primary_source AND m.provider_field=f.source_provider_field
    WHERE f.provider_code=? AND f.primary_source='tdx' AND f.report_period<=CAST(? AS DATE)
      AND f.report_period>=make_date(year(CAST(? AS DATE))-1,1,1)
    ORDER BY f.report_period,f.source_provider_field,f.fact_id) x`, []any{asof, code, end, end}},
		{"windows", `SELECT CAST(to_json(list(x)) AS VARCHAR) FROM (
    SELECT instrument_id,provider_code AS code,source_provider_field AS field,canonical_field,CAST(value AS VARCHAR) AS value,
      unit,statement_scope,period_type,calculation_basis,coverage_status,required_inputs,available_inputs,
      CAST(latest_input_announcement_time AS VARCHAR) AS available_at,input_periods,input_coefficients,source_fact_ids,source_filing_ids,missing_periods
    FROM fundamental.ttm_asof(CAST(? AS TIMESTAMPTZ),CAST(? AS DATE)) WHERE provider_code=? ORDER BY source_provider_field,instrument_id) x`, []any{asof, end, code}},
		{"supplements", `SELECT CAST(to_json(list(x)) AS VARCHAR) FROM (
    SELECT s.provider_code AS code,CAST(s.report_period AS VARCHAR) AS period,s.item,CAST(s.value AS VARCHAR) AS value,
      s.unit,s.period_basis,s.statement_scope AS scope,f.source_filing_id AS announcement_id,s.pdf_sha256,s.pdf_page,
      f.source_url AS pdf_url,CAST(f.announcement_time AS VARCHAR) AS available_at,s.reviewer,s.review_note,s.import_sha256
    FROM fundamental.reviewed_supplement s JOIN fundamental.filing f ON f.filing_id=s.source_filing_id
    WHERE s.provider_code=? AND s.report_period<=CAST(? AS DATE) AND s.report_period>=make_date(year(CAST(? AS DATE))-1,1,1)
      AND f.announcement_time<=CAST(? AS TIMESTAMPTZ) AND f.sha256=s.pdf_sha256 AND f.resolution_status='resolved'
      AND f.provider_code=s.provider_code AND f.instrument_id IS NOT NULL
      AND (s.statement_scope='finance_subsidiary' OR EXISTS (
        SELECT 1 FROM fundamental.fact_asof(CAST(? AS TIMESTAMPTZ)) current
        WHERE current.source_filing_id=f.filing_id AND current.provider_code=s.provider_code AND current.report_period=s.report_period))
    QUALIFY row_number() OVER(PARTITION BY s.provider_code,s.report_period,s.item ORDER BY f.announcement_time DESC,f.filing_id DESC)=1
    ORDER BY s.report_period,s.item) x`, []any{code, end, end, asof, asof}},
	}
	for _, q := range queries {
		var raw sql.NullString
		if err = tx.QueryRowContext(ctx, q.query, q.args...).Scan(&raw); err != nil {
			return nil, fmt.Errorf("export %s: %w", q.name, err)
		}
		if !raw.Valid {
			raw.String = "[]"
		}
		output[q.name] = json.RawMessage(raw.String)
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return output, nil
}
