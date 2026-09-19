package duckdb

import (
	"math"
	"path/filepath"
	"testing"
	"time"
)

func TestSourceFieldCatalogMatchesReviewedDefinitionsAndKeepsUnknownValues(t *testing.T) {
	ctx := t.Context()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "source.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var rows, defined, reviewed, mismatch int
	err = db.QueryRowContext(ctx, `SELECT (SELECT count(*) FROM fundamental.source_field),(SELECT count(*) FROM fundamental.source_field WHERE name IS NOT NULL),(SELECT count(*) FROM fundamental.provider_field),
 (SELECT count(*) FROM fundamental.provider_field p LEFT JOIN fundamental.source_field s USING(source,provider_field)
 WHERE s.name IS DISTINCT FROM p.canonical_field OR s.unit IS DISTINCT FROM p.unit OR s.value_multiplier IS DISTINCT FROM p.value_multiplier OR s.period_basis IS DISTINCT FROM p.period_basis)`).Scan(&rows, &defined, &reviewed, &mismatch)
	if err != nil || rows != 584 || defined != 462 || reviewed != 81 || mismatch != 0 {
		t.Fatal(rows, defined, reviewed, mismatch, err)
	}
	// All revisions are retained; no implicit latest selection or standard fact.
	for _, r := range []struct {
		key, field string
		v          float32
	}{{"a", "FN439", 309.8}, {"b", "FN439", 310}, {"a", "FN585", 7}, {"a", "FN438", 0}, {"a", "FN314", 260828}} {
		_, err = db.ExecContext(ctx, `INSERT INTO fundamental.provider_fact(instrument_id,source,provider_code,provider_field,report_period,value,value_float32_bits,revision_key) VALUES (1,'tdx','300866',?,'2026-06-30',?,?,?)`, r.field, float64(r.v), math.Float32bits(r.v), r.key)
		if err != nil {
			t.Fatal(err)
		}
	}
	out, err := ExportSourceFinancialData(ctx, db, "300866", time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if len(out.Observations) != 5 || out.States["undefined_source_field"] != 1 || out.States["source_date_encoding_only"] != 1 || out.States["zero_requires_review"] != 1 {
		t.Fatal(out)
	}
	for _, o := range out.Observations {
		if o.Field == "lease_liabilities" && (o.Value == nil || *o.Value != *o.Evidence.RawValue*10000) {
			t.Fatal(o)
		}
	}
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact`).Scan(&rows); err != nil || rows != 0 {
		t.Fatal("source query promoted facts", rows, err)
	}
	if _, err = ExportSourceFinancialData(ctx, db, "FN439", time.Now()); err == nil {
		t.Fatal("invalid code accepted")
	}
	if _, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET zero_policy='guess'`); err == nil {
		t.Fatal("invalid zero policy accepted")
	}
}
