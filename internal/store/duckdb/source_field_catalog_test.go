package duckdb

import (
	"github.com/yinhm/alphalake/internal/domain"
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
	if err != nil || rows != 584 || defined != 462 || reviewed != 365 || mismatch != 0 {
		t.Fatal(rows, defined, reviewed, mismatch, err)
	}
	var date string
	if err = db.QueryRowContext(ctx, `SELECT CAST(valid_from AS VARCHAR) FROM fundamental.provider_field WHERE canonical_field='top_ten_tradable_a_shares'`).Scan(&date); err != nil || date != "2019-06-30" {
		t.Fatal("historical A/B/H share ambiguity lost", date, err)
	}
	period := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	for _, values := range []map[int]float32{{439: 309.8, 585: 7, 314: 260828}, {439: 310}} {
		r, sha, _ := archiveFinancialFixture(t, ctx, db, "300866", 1, period, values)
		if _, err = ReconcileFinancialSourceRecords(ctx, db, 1, "tdx", sha, []domain.ProviderFinancialRecord{r}); err != nil {
			t.Fatal(err)
		}
	}
	out, err := ExportSourceFinancialData(ctx, db, "300866", time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if len(out.Observations) != 1169 || out.States["undefined_source_field"] == 0 || out.States["source_date_encoding_only"] != 1 || out.States["zero_requires_review"] == 0 {
		t.Fatal(out)
	}
	for _, o := range out.Observations {
		if o.Field == "lease_liabilities" && (o.Value == nil || *o.Value != *o.Evidence.RawValue*10000) {
			t.Fatal(o)
		}
	}
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.financial_observations(NULL,NULL,NULL,NULL)`).Scan(&rows); err != nil || rows != 0 {
		t.Fatal("source query promoted facts", rows, err)
	}
	if _, err = ExportSourceFinancialData(ctx, db, "FN439", time.Now()); err == nil {
		t.Fatal("invalid code accepted")
	}
	if _, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET zero_policy='guess'`); err == nil {
		t.Fatal("invalid zero policy accepted")
	}
}
