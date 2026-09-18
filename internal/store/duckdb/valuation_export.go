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
	Code             string `json:"code"`
	Period           string `json:"period"`
	Item             string `json:"item"`
	Value            string `json:"value"`
	Unit             string `json:"unit"`
	PeriodBasis      string `json:"period_basis"`
	Scope            string `json:"scope"`
	AnnouncementID   string `json:"announcement_id"`
	PDFSHA256        string `json:"pdf_sha256"`
	PDFPage          int    `json:"pdf_page"`
	Reviewer         string `json:"reviewer"`
	ReviewNote       string `json:"review_note"`
	Action           string `json:"action,omitempty"`
	SupersedesSHA256 string `json:"supersedes_sha256,omitempty"`
	ReviewedAt       string `json:"reviewed_at,omitempty"`
}

var sixDigitCode = regexp.MustCompile(`^[0-9]{6}$`)
var decimalAmount = regexp.MustCompile(`^-?[0-9]{1,28}(\.[0-9]{1,10})?$`)
var supplementItem = regexp.MustCompile(`^[a-z][a-z0-9_]*$`)

// ImportReviewedSupplements 原子发布审核动作；显式修订或撤销保留旧记录，旧动作重放不覆盖当前头部。
func ImportReviewedSupplements(ctx context.Context, db *sql.DB, records []ReviewedSupplement) (int, error) {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	inserted := 0
	for _, r := range records {
		action := r.Action
		if action == "" {
			action = "publish"
		}
		if action != "publish" && action != "replace" && action != "revoke" {
			return 0, errors.New("unsupported supplement review action")
		}
		var reviewedAt any
		if r.ReviewedAt != "" {
			at, e := time.Parse(time.RFC3339Nano, r.ReviewedAt)
			if e != nil || at.After(time.Now()) {
				return 0, errors.New("invalid supplement review time")
			}
			reviewedAt = at
		}
		if action == "publish" && r.SupersedesSHA256 != "" {
			return 0, errors.New("initial supplement cannot supersede a review")
		}
		if action != "publish" {
			h, e := hex.DecodeString(r.SupersedesSHA256)
			if e != nil || len(h) != 32 || reviewedAt == nil {
				return 0, errors.New("replacement/revocation requires previous hash and review time")
			}
		}
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
		// 已发布的旧动作重放也不重新激活被替代或撤销的记录。
		var exists bool
		if err = tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM fundamental.supplement_review_history WHERE import_sha256=?)`, sha).Scan(&exists); err != nil {
			return 0, err
		}
		if exists {
			continue
		}
		var previous, previousRecord string
		err = tx.QueryRowContext(ctx, `SELECT import_sha256,reviewed_record FROM fundamental.reviewed_supplement WHERE provider_code=? AND report_period=CAST(? AS DATE) AND item=? AND source_filing_id=?`, r.Code, r.Period, r.Item, filingID).Scan(&previous, &previousRecord)
		if err != nil && !errors.Is(err, sql.ErrNoRows) {
			return 0, err
		}
		if action == "publish" && err == nil {
			return 0, fmt.Errorf("conflicting reviewed supplement %s/%s/%s", r.Code, r.Period, r.Item)
		}
		if action != "publish" {
			if err != nil || previous != r.SupersedesSHA256 {
				return 0, errors.New("supplement predecessor is not the current review")
			}
			var prior ReviewedSupplement
			if err = json.Unmarshal([]byte(previousRecord), &prior); err != nil {
				return 0, err
			}
			if prior.ReviewedAt != "" {
				priorTime, e := time.Parse(time.RFC3339Nano, prior.ReviewedAt)
				if e != nil || reviewedAt.(time.Time).Before(priorTime) {
					return 0, errors.New("supplement review precedes predecessor")
				}
			}
			if action == "revoke" && (r.Value != prior.Value || r.Unit != prior.Unit || r.PeriodBasis != prior.PeriodBasis || r.Scope != prior.Scope || r.PDFSHA256 != prior.PDFSHA256 || r.PDFPage != prior.PDFPage) {
				return 0, errors.New("revocation must retain previous evidence and value")
			}
		}
		var predecessor any
		if r.SupersedesSHA256 != "" {
			predecessor = r.SupersedesSHA256
		}
		_, err = tx.ExecContext(ctx, `INSERT INTO fundamental.supplement_review_history VALUES (?, ?,CAST(? AS DATE),?,?,?,?,?,current_timestamp,?)`, sha, r.Code, r.Period, r.Item, filingID, action, predecessor, reviewedAt, string(raw))
		if err != nil {
			return 0, err
		}
		state := "active"
		if action == "revoke" {
			state = "revoked"
		}
		_, err = tx.ExecContext(ctx, `INSERT INTO fundamental.reviewed_supplement
 (provider_code,report_period,item,value,unit,period_basis,statement_scope,source_filing_id,pdf_sha256,pdf_page,reviewer,review_note,import_sha256,reviewed_record,review_state)
 VALUES (?,CAST(? AS DATE),?,CAST(? AS DECIMAL(38,10)),?,?,?,?,?,?,?,?,?,?,?)
 ON CONFLICT(provider_code,report_period,item,source_filing_id) DO UPDATE SET
 value=excluded.value,unit=excluded.unit,period_basis=excluded.period_basis,statement_scope=excluded.statement_scope,
 pdf_sha256=excluded.pdf_sha256,pdf_page=excluded.pdf_page,reviewer=excluded.reviewer,review_note=excluded.review_note,
 import_sha256=excluded.import_sha256,reviewed_record=excluded.reviewed_record,review_state=excluded.review_state`, r.Code, r.Period, r.Item, r.Value, r.Unit, r.PeriodBasis, r.Scope, filingID, r.PDFSHA256, r.PDFPage, r.Reviewer, r.ReviewNote, sha, string(raw), state)
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

// v2依赖通用期间语义；只读出口不能把尚未迁移的旧库冒充新契约。
func requireStandardFinancialSchema(ctx context.Context, db *sql.DB) error {
	version, err := CurrentSchemaVersion(ctx, db)
	if err != nil {
		return err
	}
	if version < 46 {
		return errors.New("standard financial contract v2 requires schema46; upgrade a backed-up database first")
	}
	return nil
}

// ExportValuationData 查询同一事务快照，数值以十进制字符串交付，未知证券返回空数据。
func ExportValuationData(ctx context.Context, db *sql.DB, code string, end, asof time.Time) (map[string]any, error) {
	if err := requireStandardFinancialSchema(ctx, db); err != nil {
		return nil, err
	}
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
	// 物化已拒绝重叠映射；只读导出也需保护尚未重新物化的目录变更。
	var overlapping bool
	if err := tx.QueryRowContext(ctx, `SELECT EXISTS (
 SELECT 1 FROM fundamental.provider_field a JOIN fundamental.provider_field b
 ON a.source=b.source AND a.provider_field=b.provider_field AND a.valid_from<b.valid_from
 WHERE a.source='tdx' AND a.canonical_field IS NOT NULL AND b.canonical_field IS NOT NULL
 AND (a.valid_to IS NULL OR b.valid_from<a.valid_to)
 AND (b.valid_to IS NULL OR a.valid_from<b.valid_to))`).Scan(&overlapping); err != nil {
		return nil, fmt.Errorf("validate export mapping intervals: %w", err)
	}
	if overlapping {
		return nil, errors.New("overlapping canonical field mappings; repair catalogue before valuation export")
	}
	output := map[string]any{"contract_version": "alphalake-valuation-v2", "code": code, "report_period": end.Format("2006-01-02"), "information_as_of": asof.UTC().Format(time.RFC3339Nano)}
	// 以窗口分区键限定候选证券，避免逐公司重排全市场事实。不能提前按代码
	// 筛选版本：同一证券的其他代码/来源可能已取代旧事实；代码复用须保留全部身份。
	var first, last sql.NullInt64
	if err := tx.QueryRowContext(ctx, `SELECT min(instrument_id),max(instrument_id) FROM fundamental.fact WHERE provider_code=?`, code).Scan(&first, &last); err != nil {
		return nil, fmt.Errorf("export candidate instruments: %w", err)
	}
	if !first.Valid {
		first.Int64, last.Int64 = 1, 0
	}
	queries := []struct {
		name, query string
		args        []any
	}{
		{"source_conflicts", `SELECT CAST(to_json(list(x)) AS VARCHAR) FROM (
    SELECT provider_code AS code,CAST(report_period AS VARCHAR) AS period,artifact_id,artifact_sha256,
    CAST(observed_at AS VARCHAR) AS available_at,reason
    FROM fundamental.provider_conflicts_asof(CAST(? AS TIMESTAMPTZ))
    WHERE source='tdx' AND provider_code=? AND report_period<=CAST(? AS DATE)
      AND report_period>=make_date(year(CAST(? AS DATE))-1,1,1)
    ORDER BY report_period,artifact_id) x`, []any{asof, code, end, end}},
		{"facts", `SELECT CAST(to_json(list(x)) AS VARCHAR) FROM (
    SELECT f.instrument_id,f.provider_code AS code,CAST(f.report_period AS VARCHAR) AS period,f.canonical_field AS field,f.primary_source AS source,f.source_provider_field,
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
      AND m.canonical_field=f.canonical_field
      AND m.valid_from<=f.report_period AND (m.valid_to IS NULL OR f.report_period<m.valid_to)
    WHERE f.instrument_id BETWEEN ? AND ? AND f.provider_code=? AND f.primary_source='tdx' AND f.report_period<=CAST(? AS DATE)
      AND f.report_period>=make_date(year(CAST(? AS DATE))-1,1,1)
    ORDER BY f.report_period,f.canonical_field,f.fact_id) x`, []any{asof, first.Int64, last.Int64, code, end, end}},
		{"windows", `SELECT CAST(to_json(list(x)) AS VARCHAR) FROM (
    SELECT instrument_id,provider_code AS code,canonical_field AS field,canonical_field,CAST(value AS VARCHAR) AS value,
      unit,statement_scope,period_type,calculation_basis,coverage_status,required_inputs,available_inputs,
      CAST(latest_input_announcement_time AS VARCHAR) AS available_at,input_periods,input_coefficients,source_fact_ids,source_filing_ids,missing_periods
    FROM fundamental.ttm_asof(CAST(? AS TIMESTAMPTZ),CAST(? AS DATE), min_instrument_id := ?, max_instrument_id := ?) WHERE provider_code=? ORDER BY canonical_field,instrument_id) x`, []any{asof, end, first.Int64, last.Int64, code}},
		{"supplements", `SELECT CAST(to_json(list(x)) AS VARCHAR) FROM (
 SELECT * EXCLUDE(review_state) FROM (
    SELECT s.provider_code AS code,CAST(s.report_period AS VARCHAR) AS period,s.item,CAST(s.value AS VARCHAR) AS value,
      s.unit,s.period_basis,s.statement_scope AS scope,f.source_filing_id AS announcement_id,s.pdf_sha256,s.pdf_page,
      f.source_url AS pdf_url,CAST(f.announcement_time AS VARCHAR) AS available_at,s.reviewer,s.review_note,s.import_sha256,s.review_state
    FROM fundamental.reviewed_supplement s JOIN fundamental.filing f ON f.filing_id=s.source_filing_id
    WHERE s.provider_code=? AND s.report_period<=CAST(? AS DATE) AND s.report_period>=make_date(year(CAST(? AS DATE))-1,1,1)
      AND f.announcement_time<=CAST(? AS TIMESTAMPTZ) AND f.sha256=s.pdf_sha256 AND f.resolution_status='resolved'
      AND f.provider_code=s.provider_code AND f.instrument_id IS NOT NULL
      AND (s.statement_scope='finance_subsidiary' OR EXISTS (
        SELECT 1 FROM fundamental.fact_asof(CAST(? AS TIMESTAMPTZ)) current
        WHERE current.source_filing_id=f.filing_id AND current.provider_code=s.provider_code AND current.report_period=s.report_period))
    QUALIFY row_number() OVER(PARTITION BY s.provider_code,s.report_period,s.item ORDER BY f.announcement_time DESC,f.filing_id DESC)=1
     ) selected WHERE review_state='active' ORDER BY period,item) x`, []any{code, end, end, asof, asof}},
	}
	// Mirror metadata is added only for independently reviewed documents, so
	// existing CNINFO-only exported evidence remains byte-compatible.
	mirrors := map[string]json.RawMessage{}
	rows, err := tx.QueryContext(ctx, `SELECT f.source_filing_id,CASE WHEN proof.artifact_id IS NOT NULL THEN v.reviewed_record END
 FROM fundamental.filing f JOIN meta.artifact a ON a.artifact_id=f.artifact_id
 LEFT JOIN fundamental.document_review v ON v.filing_id=f.filing_id
 AND v.document_artifact_id=f.artifact_id AND v.pdf_sha256=f.sha256
 LEFT JOIN meta.artifact proof ON proof.artifact_id=v.review_artifact_id
 AND proof.source='document-review' AND proof.dataset='filing_document_binding'
 AND proof.sha256=sha256(v.reviewed_record) AND proof.source_locator=f.source_url
 WHERE f.source='cninfo' AND f.provider_code=? AND f.report_period<=CAST(? AS DATE)
 AND f.report_period>=make_date(year(CAST(? AS DATE))-1,1,1) AND a.source<>'cninfo'`, code, end, end)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var id string
		var details sql.NullString
		if err = rows.Scan(&id, &details); err != nil {
			rows.Close()
			return nil, err
		}
		if !details.Valid || !json.Valid([]byte(details.String)) {
			rows.Close()
			return nil, errors.New("mirror document lacks explicit review")
		}
		if _, exists := mirrors[id]; exists {
			rows.Close()
			return nil, errors.New("ambiguous mirror document review")
		}
		mirrors[id] = json.RawMessage(details.String)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	for _, q := range queries {
		var raw sql.NullString
		if err = tx.QueryRowContext(ctx, q.query, q.args...).Scan(&raw); err != nil {
			return nil, fmt.Errorf("export %s: %w", q.name, err)
		}
		if !raw.Valid {
			raw.String = "[]"
		}
		if len(mirrors) > 0 && (q.name == "facts" || q.name == "supplements") {
			var records []map[string]json.RawMessage
			if err = json.Unmarshal([]byte(raw.String), &records); err != nil {
				return nil, err
			}
			for _, r := range records {
				var id string
				if err = json.Unmarshal(r["announcement_id"], &id); err != nil {
					return nil, err
				}
				if review, ok := mirrors[id]; ok {
					r["document_provenance"] = review
				}
			}
			encoded, err := json.Marshal(records)
			if err != nil {
				return nil, err
			}
			raw.String = string(encoded)
		}
		output[q.name] = json.RawMessage(raw.String)
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return output, nil
}

// ExportSupplementReviewHistory 返回审核原文和前序关系，不把审核时间当公告可用时间。
func ExportSupplementReviewHistory(ctx context.Context, db *sql.DB, code string) (json.RawMessage, error) {
	if !sixDigitCode.MatchString(code) {
		return nil, errors.New("six-digit security code required")
	}
	var raw sql.NullString
	err := db.QueryRowContext(ctx, `SELECT CAST(to_json(list(x)) AS VARCHAR) FROM (
 SELECT h.*,s.import_sha256=h.import_sha256 AS is_current
 FROM fundamental.supplement_review_history h
 LEFT JOIN fundamental.reviewed_supplement s USING(provider_code,report_period,item,source_filing_id)
 WHERE h.provider_code=? ORDER BY h.report_period,h.item,h.recorded_at NULLS FIRST,h.import_sha256) x`, code).Scan(&raw)
	if err != nil {
		return nil, err
	}
	if !raw.Valid {
		return json.RawMessage("[]"), nil
	}
	return json.RawMessage(raw.String), nil
}
