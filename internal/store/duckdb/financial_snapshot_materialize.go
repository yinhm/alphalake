package duckdb

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"time"

	duckdbgo "github.com/duckdb/duckdb-go/v2"
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
	covered              map[string]bool
}

func snapshotRejection(r snapshotRule, value float64, record IndexedFinancialRecord, f snapshotFiling) string {
	switch {
	case record.Record.InstrumentID != f.instrument:
		return "filing_instrument_mismatch"
	case !record.Record.ReportPeriod.Equal(f.period):
		return "filing_report_period_mismatch"
	case f.announcement.Before(record.Record.ReportPeriod):
		return "announcement_before_report_period"
	}
	expected := "unknown"
	p := record.Record.ReportPeriod
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
	switch {
	case f.covered != nil && !f.covered[r.name]:
		return "field_not_covered_by_disclosure"
	case f.kind != expected:
		return "filing_type_mismatch"
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
	rows, err = conn.QueryContext(ctx, `SELECT l.provider_code,f.filing_id,f.instrument_id,CASE WHEN f.filing_type='prospectus' THEN c.report_period ELSE f.report_period END,f.announcement_time,CASE WHEN f.filing_type='prospectus' AND c.filing_id IS NOT NULL THEN 'annual' ELSE coalesce(f.filing_type,'') END,CAST(to_json(c.fields) AS VARCHAR) FROM fundamental.provider_filing_link l JOIN fundamental.filing f USING(filing_id) LEFT JOIN fundamental.active_filing_coverage c ON c.filing_id=f.filing_id AND c.report_period=l.report_period
 WHERE l.provider_source='tdx' AND l.provider_revision_key=? AND l.status='linked' AND f.resolution_status='resolved' AND (f.filing_type!='prospectus' OR c.filing_id IS NOT NULL)`, revision)
	if err != nil {
		return result, err
	}
	filings := map[string]snapshotFiling{}
	for rows.Next() {
		var code string
		var f snapshotFiling
		var covered sql.NullString
		if err = rows.Scan(&code, &f.id, &f.instrument, &f.period, &f.announcement, &f.kind, &covered); err != nil {
			rows.Close()
			return result, err
		}
		if covered.Valid {
			var names []string
			if err = json.Unmarshal([]byte(covered.String), &names); err != nil {
				rows.Close()
				return result, err
			}
			f.covered = map[string]bool{}
			for _, name := range names {
				f.covered[name] = true
			}
		}
		filings[code] = f
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return result, err
	}

	if len(filings) == 0 {
		ids := make([]string, len(records))
		for i, r := range records {
			if r.ID <= 0 {
				return result, fmt.Errorf("invalid source record ID")
			}
			ids[i] = strconv.FormatInt(r.ID, 10)
		}
		var existing int
		if err = conn.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_snapshot WHERE source_record_id IN (`+strings.Join(ids, ",")+`)`).Scan(&existing); err != nil {
			return result, err
		}
		if existing == 0 {
			_, err = conn.ExecContext(ctx, `DELETE FROM fundamental.statement_rejection WHERE source_record_id IN (`+strings.Join(ids, ",")+`)`)
			return result, err
		}
	}
	if _, err = conn.ExecContext(ctx, `CREATE OR REPLACE TEMP TABLE _snapshot_values(source_record_id BIGINT,instrument_id BIGINT,source_filing_id BIGINT,report_period DATE,announcement_time TIMESTAMPTZ,ingest_run_id BIGINT,`+strings.Join(types, ",")+`)`); err != nil {
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
	defer conn.ExecContext(context.WithoutCancel(ctx), `DROP TABLE IF EXISTS temp.main._snapshot_values; DROP TABLE IF EXISTS temp.main._snapshot_rejections; DROP TABLE IF EXISTS temp.main._snapshot_records`)
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
			filing, ok := filings[record.Record.ProviderCode]
			if !ok {
				continue
			}
			row := make([]driver.Value, 6+len(fields))
			row[0] = record.ID
			row[1] = record.Record.InstrumentID
			row[2] = filing.id
			row[3] = period
			row[4] = filing.announcement
			row[5] = runID
			failures := map[string][]any{}
			valid := 0
			for _, rule := range rules {
				if rule.index >= len(record.Record.ProviderFields) || (filing.covered != nil && !filing.covered[rule.name]) {
					continue
				}
				result.Candidates++
				value := record.Record.ProviderFields[rule.index].Value * float64(rule.multiplier.Int64)
				reason := snapshotRejection(rule, value, record, filing)
				if reason != "" {
					failures[reason] = append(failures[reason], rule.name)
					result.Rejected++
					continue
				}
				row[6+rule.column] = snapshotDecimal(value)
				valid++
				result.Materialized++
			}
			if valid > 0 {
				if e = app.AppendRow(row...); e != nil {
					return e
				}
			}
			for reason, names := range failures {
				sort.Slice(names, func(i, j int) bool { return names[i].(string) < names[j].(string) })
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

	var existing int
	if err = conn.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_snapshot WHERE source_record_id IN(SELECT id FROM _snapshot_records)`).Scan(&existing); err != nil {
		return result, err
	}
	if existing == 0 {
		_, err = conn.ExecContext(ctx, `DELETE FROM fundamental.statement_rejection WHERE source_record_id IN(SELECT id FROM _snapshot_records); INSERT INTO fundamental.statement_rejection SELECT * FROM _snapshot_rejections; INSERT INTO fundamental.statement_snapshot SELECT * FROM _snapshot_values`)
		result.Inserted = result.Materialized
		return result, err
	}

	newValues, oldValues, labels := make([]string, len(names)), make([]string, len(names)), make([]string, len(names))
	updates := []string{"instrument_id=excluded.instrument_id", "source_filing_id=excluded.source_filing_id", "report_period=excluded.report_period", "announcement_time=excluded.announcement_time"}
	for i, n := range names {
		newValues[i] = "s." + n
		oldValues[i] = "f." + n
		labels[i] = duckdbStringLiteral(fields[i].Name)
		updates = append(updates, n+"=excluded."+n)
	}
	if _, err = conn.ExecContext(ctx, `CREATE OR REPLACE TEMP TABLE _snapshot_changes AS
 SELECT coalesce(s.source_record_id,f.source_record_id) AS source_record_id,s.source_record_id IS NOT NULL AS has_new,f.source_record_id IS NOT NULL AS has_old,
 s.ingest_run_id,[`+strings.Join(newValues, ",")+`] AS new_values,[`+strings.Join(oldValues, ",")+`] AS old_values,
 s.instrument_id IS DISTINCT FROM f.instrument_id OR s.source_filing_id IS DISTINCT FROM f.source_filing_id OR s.report_period IS DISTINCT FROM f.report_period OR s.announcement_time IS DISTINCT FROM f.announcement_time AS metadata_changed
 FROM (SELECT * FROM fundamental.statement_snapshot WHERE source_record_id IN(SELECT id FROM _snapshot_records)) f FULL JOIN _snapshot_values s USING(source_record_id)
 WHERE s.source_record_id IS NULL OR f.source_record_id IS NULL OR metadata_changed OR [`+strings.Join(newValues, ",")+`] IS DISTINCT FROM [`+strings.Join(oldValues, ",")+`]`); err != nil {
		return result, err
	}
	defer conn.ExecContext(context.WithoutCancel(ctx), `DROP TABLE IF EXISTS temp.main._snapshot_changes`)
	if err = conn.QueryRowContext(ctx, `SELECT
 coalesce(sum(list_count(list_filter(list_zip(new_values,old_values),x->x[1] IS NOT NULL AND x[2] IS NULL))),0),
 coalesce(sum(list_count(list_filter(list_zip(new_values,old_values),x->x[1] IS NOT NULL AND x[2] IS NOT NULL AND (x[1] IS DISTINCT FROM x[2] OR metadata_changed)))),0),
 coalesce(sum(list_count(list_filter(list_zip(new_values,old_values),x->x[1] IS NULL AND x[2] IS NOT NULL))),0)
 FROM _snapshot_changes`).Scan(&result.Inserted, &result.Updated, &result.Removed); err != nil {
		return result, err
	}
	if _, err = conn.ExecContext(ctx, `INSERT INTO fundamental.statement_field_run
 SELECT source_record_id,list_extract([`+strings.Join(labels, ",")+`],n),ingest_run_id FROM _snapshot_changes CROSS JOIN range(1,`+strconv.Itoa(len(names)+1)+`) index(n)
 WHERE has_old AND new_values[n] IS NOT NULL AND (new_values[n] IS DISTINCT FROM old_values[n] OR metadata_changed)
 ON CONFLICT(source_record_id,canonical_field) DO UPDATE SET ingest_run_id=excluded.ingest_run_id`); err != nil {
		return result, err
	}
	if _, err = conn.ExecContext(ctx, `DELETE FROM fundamental.statement_snapshot WHERE source_record_id IN(SELECT source_record_id FROM _snapshot_changes WHERE NOT has_new);
 DELETE FROM fundamental.statement_rejection WHERE source_record_id IN(SELECT id FROM _snapshot_records);
 INSERT INTO fundamental.statement_rejection SELECT * FROM _snapshot_rejections`); err != nil {
		return result, err
	}
	_, err = conn.ExecContext(ctx, `INSERT INTO fundamental.statement_snapshot SELECT s.* FROM _snapshot_values s JOIN _snapshot_changes c USING(source_record_id)
 WHERE NOT c.has_old OR c.metadata_changed OR c.new_values IS DISTINCT FROM c.old_values
 ON CONFLICT(source_record_id) DO UPDATE SET `+strings.Join(updates, ","))

	return result, err
}

// snapshotDecimal preserves the established shortest-decimal-text conversion,
// rounding half away from zero at scale 10. Converting each value through a SQL
// VARCHAR cast dominated real wide-table ingestion; native decimals avoid it.
func snapshotDecimal(value float64) duckdbgo.Decimal {
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
