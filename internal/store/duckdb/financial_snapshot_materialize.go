package duckdb

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"fmt"
	"math"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"time"

	duckdbgo "github.com/duckdb/duckdb-go/v2"
	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

type snapshotRule struct {
	index, column                 int
	name, unit, kind, basis, zero string
	multiplier                    sql.NullInt64
	validDefinition               bool
}

type snapshotFiling struct {
	id, instrument       int64
	period, announcement time.Time
	kind                 string
}

func snapshotPeriodKind(p time.Time) string {
	expected := "unknown"
	if p.AddDate(0, 0, 1).Day() == 1 {
		switch p.Month() {
		case 3:
			expected = "quarterly_q1"
		case 6:
			expected = "semiannual"
		case 9:
			expected = "quarterly_q3"
		case 12:
			expected = "annual"
		}
	}
	return expected
}

func snapshotRejection(r snapshotRule, value float64, record IndexedFinancialRecord) string {
	switch {
	case snapshotPeriodKind(record.Record.ReportPeriod) == "unknown":
		return "canonical_report_period_unknown"
	case r.zero != "allow" && r.zero != "reject":
		return "canonical_zero_policy_unknown"
	case !r.multiplier.Valid || (r.multiplier.Int64 != 1 && r.multiplier.Int64 != 10000):
		return "canonical_scale_unknown"
	case math.IsNaN(value) || math.IsInf(value, 0):
		return "provider_value_not_finite"
	case r.zero == "reject" && value == 0:
		return "provider_zero_ambiguous"
	case r.basis != "report" && r.basis != "instant" && r.basis != "ytd" && r.basis != "quarter" && r.basis != "opening_instant" && r.basis != "ttm":
		return "canonical_period_unknown"
	case !((r.kind == "monetary" && r.unit == "CNY") || (r.kind == "shares" && r.unit == "share") || (r.kind == "per_share" && r.unit == "CNY/share") || (r.kind == "count" && r.unit == "count")):
		return "canonical_unit_unknown"
	case !r.validDefinition:
		return "canonical_definition_mismatch"
	case math.Abs(value) >= 1e28:
		return "canonical_decimal_overflow"
	}
	return ""
}

// MaterializeFinancialSnapshotBatch processes each source record once. Source
// values never form a long table, including in temporary storage. All monetary
// values use shortest round-trip float64 text quantized exactly to native DECIMAL.
// Full-cell equivalence with the original SQL conversion is a publication gate.
func MaterializeFinancialSnapshotBatch(ctx context.Context, conn *sql.Conn, runID int64, fields []SnapshotField, records []IndexedFinancialRecord) (result CanonicalFundamentalResult, err error) {
	if len(records) == 0 {
		return result, nil
	}
	if runID <= 0 {
		return result, fmt.Errorf("run ID required")
	}
	period, revision := records[0].Record.ReportPeriod, records[0].Revision
	positions := map[string]int{}
	names := make([]string, len(fields))
	types := make([]string, len(fields))
	casts := make([]string, len(fields))
	for i, f := range fields {
		if !standardSnapshotName.MatchString(f.Name) {
			return result, fmt.Errorf("invalid standard field")
		}
		positions[f.Name] = i
		names[i] = `"` + f.Name + `"`
		types[i] = names[i] + ` DECIMAL(38,10)`
		casts[i] = names[i]
	}
	rows, err := conn.QueryContext(ctx, `SELECT CASE WHEN regexp_full_match(m.provider_field,'FN[1-9][0-9]*') THEN try_cast(substr(m.provider_field,3) AS INTEGER) END,m.canonical_field,coalesce(m.unit,''),coalesce(m.value_kind,''),coalesce(m.period_basis,''),coalesce(m.zero_policy,''),m.value_multiplier,
 EXISTS(SELECT 1 FROM fundamental.field f WHERE f.canonical_field=m.canonical_field AND f.unit=m.unit AND f.value_kind=m.value_kind AND f.period_basis=m.period_basis)
 FROM fundamental.provider_field m WHERE m.source='tdx' AND m.canonical_field IS NOT NULL AND (m.valid_from IS NULL OR m.valid_from<=?) AND (m.valid_to IS NULL OR m.valid_to>?)`, period, period)
	if err != nil {
		return result, err
	}
	rules := []snapshotRule{}
	seen := map[int]bool{}
	columns := map[int]bool{}
	for rows.Next() {
		var r snapshotRule
		var index sql.NullInt64
		if err = rows.Scan(&index, &r.name, &r.unit, &r.kind, &r.basis, &r.zero, &r.multiplier, &r.validDefinition); err != nil {
			rows.Close()
			return result, err
		}
		col, ok := positions[r.name]
		if !index.Valid || index.Int64 < 1 || index.Int64 > 4096 {
			rows.Close()
			return result, fmt.Errorf("invalid source position")
		}
		r.index = int(index.Int64) - 1
		r.column = col
		if seen[r.index] || (ok && columns[col]) {
			rows.Close()
			return result, fmt.Errorf("overlapping source/standard mapping")
		}
		seen[r.index] = true
		if !ok {
			r.column = -1
			r.validDefinition = false
		} else {
			columns[col] = true
		}
		rules = append(rules, r)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return result, err
	}
	// Rejected field subsets inherit this order; do not sort the same catalogue
	// again for every source record.
	sort.Slice(rules, func(i, j int) bool { return rules[i].name < rules[j].name })
	rows, err = conn.QueryContext(ctx, `SELECT l.provider_code,f.filing_id,f.instrument_id,CASE WHEN f.filing_type='prospectus' THEN c.report_period ELSE f.report_period END,f.announcement_time,CASE WHEN f.filing_type='prospectus' AND c.filing_id IS NOT NULL THEN 'annual' ELSE coalesce(f.filing_type,'') END FROM fundamental.provider_filing_link l JOIN fundamental.filing f USING(filing_id) LEFT JOIN fundamental.active_filing_coverage c ON c.filing_id=f.filing_id AND c.report_period=l.report_period
 WHERE l.provider_source='tdx' AND l.provider_revision_key=? AND l.status='linked' AND f.resolution_status='resolved' AND (f.filing_type!='prospectus' OR c.filing_id IS NOT NULL)`, revision)
	if err != nil {
		return result, err
	}
	filings := map[string]snapshotFiling{}
	for rows.Next() {
		var code string
		var f snapshotFiling
		var announcement sql.NullTime
		if err = rows.Scan(&code, &f.id, &f.instrument, &f.period, &announcement, &f.kind); err != nil {
			rows.Close()
			return result, err
		}
		if announcement.Valid {
			f.announcement = announcement.Time
		}
		filings[code] = f
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return result, err
	}

	catalog, err := financial.FieldCatalog()
	if err != nil {
		return result, err
	}
	announcementIndex := -1
	for _, field := range catalog {
		if field.Name == "financial_report_announcement_date" {
			announcementIndex = field.Index - 1
		}
	}
	if announcementIndex < 0 {
		return result, fmt.Errorf("announcement date definition missing")
	}

	if _, err = conn.ExecContext(ctx, `CREATE OR REPLACE TEMP TABLE _snapshot_values(source_record_id BIGINT,instrument_id BIGINT,source_filing_id BIGINT,report_period DATE,announcement_time TIMESTAMPTZ,ingest_run_id BIGINT,announcement_source VARCHAR,`+strings.Join(types, ",")+`)`); err != nil {
		return result, err
	}
	if _, err = conn.ExecContext(ctx, `CREATE OR REPLACE TEMP TABLE _snapshot_rejections AS SELECT * FROM fundamental.statement_rejection WHERE false;
 CREATE OR REPLACE TEMP TABLE _snapshot_records(id BIGINT)`); err != nil {
		return result, err
	}
	ids := make([]string, len(records))
	for i, r := range records {
		if r.ID <= 0 {
			return result, fmt.Errorf("invalid source record ID")
		}
		ids[i] = fmt.Sprintf("(%d)", r.ID)
	}
	if _, err = conn.ExecContext(ctx, `INSERT INTO _snapshot_records VALUES `+strings.Join(ids, ",")); err != nil {
		return result, err
	}
	defer conn.ExecContext(context.WithoutCancel(ctx), `DROP TABLE IF EXISTS temp.main._snapshot_values; DROP TABLE IF EXISTS temp.main._snapshot_rejections; DROP TABLE IF EXISTS temp.main._snapshot_records; DROP TABLE IF EXISTS temp.main._snapshot_existing`)
	if _, err = conn.ExecContext(ctx, `CREATE OR REPLACE TEMP TABLE _snapshot_existing AS
 SELECT source_record_id FROM fundamental.statement_snapshot WHERE source_record_id IN(SELECT id FROM _snapshot_records)`); err != nil {
		return result, err
	}
	rows, err = conn.QueryContext(ctx, `SELECT source_record_id FROM _snapshot_existing`)
	if err != nil {
		return result, err
	}
	existing := map[int64]bool{}
	for rows.Next() {
		var id int64
		if err = rows.Scan(&id); err != nil {
			rows.Close()
			return result, err
		}
		existing[id] = true
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return result, err
	}
	err = conn.Raw(func(raw any) error {
		app, e := duckdbgo.NewAppender(raw.(driver.Conn), "temp", "main", "_snapshot_values")
		if e != nil {
			return e
		}
		rejected, e := duckdbgo.NewAppender(raw.(driver.Conn), "temp", "main", "_snapshot_rejections")
		if e != nil {
			_ = app.Clear()
			_ = app.Close()
			return e
		}
		done := false
		defer func() {
			if !done {
				_ = app.Clear()
				_ = rejected.Clear()
				_ = app.Close()
				_ = rejected.Close()
			}
		}()
		for _, record := range records {
			if err := ctx.Err(); err != nil {
				return err
			}
			if record.Record.Provider != "tdx" || record.ID <= 0 || record.Record.InstrumentID <= 0 || !record.Record.ReportPeriod.Equal(period) || record.Revision != revision {
				return fmt.Errorf("mixed/incomplete report batch")
			}
			filing := filings[record.Record.ProviderCode]
			if filing.instrument != record.Record.InstrumentID || !filing.period.Equal(period) || filing.kind != snapshotPeriodKind(period) {
				filing = snapshotFiling{}
			}
			row := make([]driver.Value, 7+len(fields))
			row[0] = record.ID
			row[1] = record.Record.InstrumentID
			if filing.id != 0 {
				row[2] = filing.id
			}
			row[3] = period
			row[6] = "unknown"
			if !filing.announcement.IsZero() && !filing.announcement.Before(period) {
				row[4], row[6] = filing.announcement, "cninfo"
			}
			if announcementIndex < len(record.Record.ProviderFields) {
				if date, ok := financial.ParseAnnouncementDate(record.Record.ProviderFields[announcementIndex].Value, period); ok {
					row[4], row[6] = date.AddDate(0, 0, 1).Add(-8*time.Hour), "tdx"
				}
			}
			row[5] = runID
			failures := map[string][]any{}
			valid := 0
			for _, rule := range rules {
				if rule.index >= len(record.Record.ProviderFields) {
					continue
				}
				result.Candidates++
				value := record.Record.ProviderFields[rule.index].Value * float64(rule.multiplier.Int64)
				reason := snapshotRejection(rule, value, record)
				if reason != "" {
					failures[reason] = append(failures[reason], rule.name)
					result.Rejected++
					continue
				}
				row[7+rule.column] = snapshotDecimal(value)
				valid++
				result.Materialized++
			}
			if valid > 0 {
				if !existing[record.ID] {
					result.Inserted += valid
				}
				if e = app.AppendRow(row...); e != nil {
					return e
				}
			}
			for reason, names := range failures {
				if e = rejected.AppendRow(record.ID, reason, names); e != nil {
					return e
				}
			}
		}
		if e = app.CloseWithCancel(ctx); e != nil {
			return e
		}
		if e = rejected.CloseWithCancel(ctx); e != nil {
			return e
		}
		done = true
		return nil
	})
	if err != nil {
		return result, err
	}

	if len(existing) == 0 {
		_, err = conn.ExecContext(ctx, snapshotRejectionChangesSQL+`; INSERT INTO fundamental.statement_snapshot BY NAME SELECT * FROM _snapshot_values`)
		return result, err
	}

	newValues, oldValues, labels := make([]string, len(names)), make([]string, len(names)), make([]string, len(names))
	for i, n := range names {
		newValues[i] = "s." + n
		oldValues[i] = "f." + n
		labels[i] = duckdbStringLiteral(fields[i].Name)
	}
	if _, err = conn.ExecContext(ctx, `CREATE OR REPLACE TEMP TABLE _snapshot_changes AS
 SELECT coalesce(s.source_record_id,f.source_record_id) AS source_record_id,s.source_record_id IS NOT NULL AS has_new,f.source_record_id IS NOT NULL AS has_old,
 s.ingest_run_id,f.ingest_run_id AS old_ingest_run_id,[`+strings.Join(newValues, ",")+`] AS new_values,[`+strings.Join(oldValues, ",")+`] AS old_values,
 s.instrument_id IS DISTINCT FROM f.instrument_id OR s.source_filing_id IS DISTINCT FROM f.source_filing_id OR s.report_period IS DISTINCT FROM f.report_period OR s.announcement_time IS DISTINCT FROM f.announcement_time OR s.announcement_source IS DISTINCT FROM f.announcement_source AS metadata_changed
 FROM (SELECT * FROM fundamental.statement_snapshot WHERE source_record_id IN(SELECT source_record_id FROM _snapshot_existing)) f
 FULL JOIN (SELECT * FROM _snapshot_values WHERE source_record_id IN(SELECT source_record_id FROM _snapshot_existing)) s USING(source_record_id)
 WHERE s.source_record_id IS NULL OR f.source_record_id IS NULL OR metadata_changed OR [`+strings.Join(newValues, ",")+`] IS DISTINCT FROM [`+strings.Join(oldValues, ",")+`]`); err != nil {
		return result, err
	}
	defer conn.ExecContext(context.WithoutCancel(ctx), `DROP TABLE IF EXISTS temp.main._snapshot_changes`)
	var insertedExisting int
	var changedExisting bool
	if err = conn.QueryRowContext(ctx, `SELECT
 coalesce(sum(list_count(list_filter(list_zip(new_values,old_values),x->x[1] IS NOT NULL AND x[2] IS NULL))),0),
 coalesce(sum(list_count(list_filter(list_zip(new_values,old_values),x->x[1] IS NOT NULL AND x[2] IS NOT NULL AND x[1] IS DISTINCT FROM x[2]))),0),
 coalesce(sum(list_count(list_filter(list_zip(new_values,old_values),x->x[1] IS NULL AND x[2] IS NOT NULL))),0),
 coalesce(bool_or(has_old AND new_values IS DISTINCT FROM old_values),false)
 FROM _snapshot_changes`).Scan(&insertedExisting, &result.Updated, &result.Removed, &changedExisting); err != nil {
		return result, err
	}
	result.Inserted += insertedExisting
	if changedExisting {
		if _, err = conn.ExecContext(ctx, `INSERT INTO fundamental.statement_field_run
 SELECT source_record_id,list_extract([`+strings.Join(labels, ",")+`],n),ingest_run_id FROM _snapshot_changes CROSS JOIN range(1,`+strconv.Itoa(len(names)+1)+`) index(n)
 WHERE has_old AND new_values[n] IS NOT NULL AND new_values[n] IS DISTINCT FROM old_values[n]
 ON CONFLICT(source_record_id,canonical_field) DO UPDATE SET ingest_run_id=excluded.ingest_run_id`); err != nil {
			return result, err
		}
	}
	if _, err = conn.ExecContext(ctx, `DELETE FROM fundamental.statement_snapshot WHERE source_record_id IN(SELECT source_record_id FROM _snapshot_changes WHERE NOT has_new);
 `+snapshotRejectionChangesSQL); err != nil {
		return result, err
	}
	// Disclosure-only changes touch narrow record metadata, not every numeric column.
	if _, err = conn.ExecContext(ctx, `UPDATE fundamental.statement_snapshot AS f SET
 instrument_id=s.instrument_id,source_filing_id=s.source_filing_id,report_period=s.report_period,
 announcement_time=s.announcement_time,announcement_source=s.announcement_source
 FROM _snapshot_values s JOIN _snapshot_changes c USING(source_record_id)
 WHERE f.source_record_id=s.source_record_id AND c.has_old AND c.metadata_changed AND c.new_values IS NOT DISTINCT FROM c.old_values`); err != nil {
		return result, err
	}
	// Keep new rows out of the wide ON CONFLICT update plan. New coverage is
	// predominantly inserts; it must not materialize the existing wide table.
	if _, err = conn.ExecContext(ctx, `INSERT INTO fundamental.statement_snapshot BY NAME
 SELECT s.* FROM _snapshot_values s WHERE source_record_id NOT IN(SELECT source_record_id FROM _snapshot_existing)`); err != nil {
		return result, err
	}
	if !changedExisting {
		return result, nil
	}
	// Replace only changed wide rows inside the caller's transaction. A wide
	// UPDATE retains buffers for every column even when very few rows change.
	_, err = conn.ExecContext(ctx, `DELETE FROM fundamental.statement_snapshot
 WHERE source_record_id IN(SELECT source_record_id FROM _snapshot_changes WHERE has_old AND new_values IS DISTINCT FROM old_values);
 INSERT INTO fundamental.statement_snapshot BY NAME
 SELECT s.* REPLACE(c.old_ingest_run_id AS ingest_run_id)
 FROM _snapshot_values s JOIN _snapshot_changes c USING(source_record_id)
 WHERE c.has_old AND c.new_values IS DISTINCT FROM c.old_values`)

	return result, err
}

// Rejections are content evidence. Replacing identical lists forces DuckDB to
// reallocate all their string children for each package despite no new evidence.
const snapshotRejectionChangesSQL = `
 DELETE FROM fundamental.statement_rejection WHERE (source_record_id,rule_code) IN (
 WITH existing AS MATERIALIZED (
 SELECT * FROM fundamental.statement_rejection WHERE source_record_id IN(SELECT id FROM _snapshot_records))
 SELECT r.source_record_id,r.rule_code FROM existing r LEFT JOIN _snapshot_rejections s USING(source_record_id,rule_code)
 WHERE s.source_record_id IS NULL OR r.fields IS DISTINCT FROM s.fields);
 INSERT INTO fundamental.statement_rejection SELECT s.* FROM _snapshot_rejections s
 WHERE NOT EXISTS(SELECT 1 FROM fundamental.statement_rejection r WHERE r.source_record_id=s.source_record_id AND r.rule_code=s.rule_code)`

// snapshotDecimal preserves the established shortest-decimal-text conversion,
// rounding half away from zero at scale 10. Converting each value through a SQL
// VARCHAR cast dominated real wide-table ingestion; native decimals avoid it.
func snapshotDecimal(value float64) duckdbgo.Decimal {
	// In this range an integral binary64 has exactly one round-tripping integer
	// decimal. This also handles audited source zeros without rational parsing.
	if math.Abs(value) < 1<<53 && math.Trunc(value) == value {
		return duckdbgo.Decimal{Width: 38, Scale: 10, Value: new(big.Int).Mul(big.NewInt(int64(value)), big.NewInt(10000000000))}
	}
	r, ok := new(big.Rat).SetString(strconv.FormatFloat(value, 'g', -1, 64))
	if !ok {
		panic("validated finite value has invalid decimal representation")
	}
	numerator := new(big.Int).Mul(r.Num(), big.NewInt(10000000000))
	quotient, remainder := new(big.Int), new(big.Int)
	quotient.QuoRem(numerator, r.Denom(), remainder)
	remainder.Abs(remainder).Lsh(remainder, 1)
	if remainder.Cmp(r.Denom()) >= 0 {
		quotient.Add(quotient, big.NewInt(int64(r.Sign())))
	}
	return duckdbgo.Decimal{Width: 38, Scale: 10, Value: quotient}
}
