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
	rows, err = conn.QueryContext(ctx, `SELECT l.provider_code,f.filing_id,f.instrument_id,f.report_period,f.announcement_time,coalesce(f.filing_type,'') FROM fundamental.provider_filing_link l JOIN fundamental.filing f USING(filing_id)
 WHERE l.provider_source='tdx' AND l.provider_revision_key=? AND l.status='linked' AND f.resolution_status='resolved'`, revision)
	if err != nil {
		return result, err
	}
	filings := map[string]snapshotFiling{}
	for rows.Next() {
		var code string
		var f snapshotFiling
		if err = rows.Scan(&code, &f.id, &f.instrument, &f.period, &f.announcement, &f.kind); err != nil {
			rows.Close()
			return result, err
		}
		filings[code] = f
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return result, err
	}
	if len(filings) == 0 {
		return result, nil
	}
	if _, err = conn.ExecContext(ctx, `CREATE OR REPLACE TEMP TABLE _snapshot_values(source_record_id BIGINT,instrument_id BIGINT,source_filing_id BIGINT,report_period DATE,announcement_time TIMESTAMPTZ,ingest_run_id BIGINT,`+strings.Join(types, ",")+`)`); err != nil {
		return result, err
	}
	defer conn.ExecContext(context.WithoutCancel(ctx), `DROP TABLE IF EXISTS temp.main._snapshot_values`)
	err = conn.Raw(func(raw any) error {
		app, e := duckdbgo.NewAppender(raw.(driver.Conn), "temp", "main", "_snapshot_values")
		if e != nil {
			return e
		}
		rejected, e := duckdbgo.NewAppender(raw.(driver.Conn), PersistentCatalog, "fundamental", "statement_rejection")
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
				if rule.index >= len(record.Record.ProviderFields) {
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
	_, err = conn.ExecContext(ctx, `INSERT INTO fundamental.statement_snapshot SELECT source_record_id,instrument_id,source_filing_id,report_period,announcement_time,ingest_run_id,`+strings.Join(casts, ",")+` FROM temp.main._snapshot_values`)
	if err == nil {
		result.Inserted = result.Materialized
	}
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
