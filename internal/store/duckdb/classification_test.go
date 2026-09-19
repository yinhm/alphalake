package duckdb

import (
	"context"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func classificationSnapshot(members ...string) domain.ClassificationSnapshot {
	taxonomy := domain.ClassificationTaxonomy{Source: "tdx", Code: "tdx_concept", Name: "TDX Concept", Type: "concept"}
	ids := make([]domain.Identifier, 0, len(members))
	for _, symbol := range members {
		ids = append(ids, domain.Identifier{Provider: "tdx", Type: "symbol", Value: symbol})
	}
	return domain.ClassificationSnapshot{
		Taxonomy: taxonomy,
		Nodes: []domain.ClassificationNodeObservation{{
			Taxonomy: taxonomy, SourceNodeCode: "880500", Name: "机器人", Level: 1, SourceSymbol: "880500", Members: ids,
		}},
		Complete: true,
	}
}

func TestApplyClassificationSnapshotTracksTemporalMembership(t *testing.T) {
	ctx := context.Background()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "classification.duckdb"))
	if err != nil {
		t.Fatalf("OpenInitialized() error = %v", err)
	}
	defer db.Close()

	for _, symbol := range []string{"sh600001", "sh600002", "sh600003"} {
		if _, err := UpsertInstrument(ctx, db,
			domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: symbol},
			domain.Identifier{Provider: "tdx", Type: "symbol", Value: symbol},
		); err != nil {
			t.Fatalf("UpsertInstrument(%s) error = %v", symbol, err)
		}
	}

	day1 := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	day2 := time.Date(2026, 9, 4, 0, 0, 0, 0, time.UTC)
	day3 := time.Date(2026, 9, 7, 0, 0, 0, 0, time.UTC)

	run1, _ := StartIngestRun(ctx, db, "tdx", "classification", nil)
	result, err := ApplyClassificationSnapshotForRun(ctx, db, run1, day1, day1.Add(12*time.Hour), classificationSnapshot("sh600001", "sh600002"))
	if err != nil {
		t.Fatalf("first snapshot error = %v", err)
	}
	if result.Opened != 2 || result.Closed != 0 || result.Members != 2 {
		t.Fatalf("first result = %#v", result)
	}

	// Re-observing the same complete snapshot must not create duplicate intervals.
	runSame, _ := StartIngestRun(ctx, db, "tdx", "classification", nil)
	result, err = ApplyClassificationSnapshotForRun(ctx, db, runSame, day1, day1.Add(13*time.Hour), classificationSnapshot("sh600001", "sh600002"))
	if err != nil {
		t.Fatalf("repeat snapshot error = %v", err)
	}
	if result.Opened != 0 || result.Closed != 0 {
		t.Fatalf("repeat result = %#v", result)
	}
	// 未变化的成员也刷新观察时间；首次观察和区间不变。
	var first, latest time.Time
	var latestRun int64
	if err := db.QueryRowContext(ctx, `SELECT observed_at,last_observed_at,last_observed_run_id
		FROM classification.membership ORDER BY instrument_id LIMIT 1`).Scan(&first, &latest, &latestRun); err != nil {
		t.Fatal(err)
	}
	if !first.Equal(day1.Add(12*time.Hour)) || !latest.Equal(day1.Add(13*time.Hour)) || latestRun != runSame {
		t.Fatalf("observation timestamps: %v %v run=%d", first, latest, latestRun)
	}
	partial := classificationSnapshot("sh600001")
	partial.Complete = false
	if _, err := ApplyClassificationSnapshotForRun(ctx, db, runSame, day1, day1.Add(14*time.Hour), partial); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT last_observed_at FROM classification.membership m
		JOIN core.instrument_identifier i USING(instrument_id) WHERE i.identifier_value='sh600002'`).Scan(&latest); err != nil {
		t.Fatal(err)
	}
	if !latest.Equal(day1.Add(13 * time.Hour)) {
		t.Fatal("partial snapshot refreshed absent member", latest)
	}

	// 600001 disappears, 600002 remains, 600003 appears.
	run2, _ := StartIngestRun(ctx, db, "tdx", "classification", nil)
	result, err = ApplyClassificationSnapshotForRun(ctx, db, run2, day2, day2.Add(12*time.Hour), classificationSnapshot("sh600002", "sh600003"))
	if err != nil {
		t.Fatalf("second snapshot error = %v", err)
	}
	if result.Opened != 1 || result.Closed != 1 {
		t.Fatalf("second result = %#v", result)
	}

	var closedTo time.Time
	if err := db.QueryRowContext(ctx, `
		SELECT m.effective_to
		FROM classification.membership m
		JOIN core.instrument_identifier i USING (instrument_id)
		WHERE i.identifier_value='sh600001' AND m.effective_from=?
	`, day1).Scan(&closedTo); err != nil {
		t.Fatalf("query closed interval: %v", err)
	}
	if !closedTo.Equal(day2) {
		t.Fatalf("effective_to = %v, want %v", closedTo, day2)
	}

	// Re-adding 600001 opens a new interval rather than mutating old history.
	run3, _ := StartIngestRun(ctx, db, "tdx", "classification", nil)
	result, err = ApplyClassificationSnapshotForRun(ctx, db, run3, day3, day3.Add(12*time.Hour), classificationSnapshot("sh600001", "sh600002", "sh600003"))
	if err != nil {
		t.Fatalf("third snapshot error = %v", err)
	}
	if result.Opened != 1 || result.Closed != 0 {
		t.Fatalf("third result = %#v", result)
	}

	var intervals int
	if err := db.QueryRowContext(ctx, `
		SELECT count(*)
		FROM classification.membership m
		JOIN core.instrument_identifier i USING (instrument_id)
		WHERE i.identifier_value='sh600001'
	`).Scan(&intervals); err != nil {
		t.Fatalf("count intervals: %v", err)
	}
	if intervals != 2 {
		t.Fatalf("600001 intervals = %d, want 2", intervals)
	}
}

func TestApplyClassificationSnapshotQuarantinesUnknownMemberWithoutClosingHistory(t *testing.T) {
	ctx := context.Background()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "rollback.duckdb"))
	if err != nil {
		t.Fatalf("OpenInitialized() error = %v", err)
	}
	defer db.Close()

	if _, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Known"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"},
	); err != nil {
		t.Fatalf("UpsertInstrument() error = %v", err)
	}
	day1 := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	day2 := time.Date(2026, 9, 4, 0, 0, 0, 0, time.UTC)
	run1, _ := StartIngestRun(ctx, db, "tdx", "classification", nil)
	if _, err := ApplyClassificationSnapshotForRun(ctx, db, run1, day1, day1, classificationSnapshot("sh600001")); err != nil {
		t.Fatalf("seed snapshot error = %v", err)
	}

	run2, _ := StartIngestRun(ctx, db, "tdx", "classification", nil)
	result, err := ApplyClassificationSnapshotForRun(ctx, db, run2, day2, day2, classificationSnapshot("sh699999"))
	if err != nil || result.Unresolved != 1 || result.Closed != 0 {
		t.Fatalf("unknown member not isolated: %+v %v", result, err)
	}

	var open int
	if err := db.QueryRowContext(ctx, `
		SELECT count(*) FROM classification.membership WHERE effective_to IS NULL
	`).Scan(&open); err != nil {
		t.Fatalf("count open memberships: %v", err)
	}
	if open != 1 {
		t.Fatalf("open memberships = %d, want prior interval preserved", open)
	}
	var diagnostics int
	if err := db.QueryRow(`SELECT count(*) FROM meta.validation_result WHERE rule_code='classification.unresolved_member' AND subject_key LIKE '%sh699999' AND NOT passed`).Scan(&diagnostics); err != nil || diagnostics != 1 {
		t.Fatalf("unknown member evidence: %d %v", diagnostics, err)
	}
}

func TestClassificationMarketSnapshotWithUnknownMemberAndBoundedMemory(t *testing.T) {
	t.Setenv("ALPHALAKE_DUCKDB_MEMORY_LIMIT", "128MiB")
	t.Setenv("ALPHALAKE_DUCKDB_THREADS", "1")
	ctx := context.Background()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "market-industry.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = db.Exec(`INSERT INTO core.instrument(instrument_id,instrument_type,exchange_mic,currency,name)
 SELECT i,'equity','XSHG','CNY','synthetic' FROM range(1,6001) r(i);
 INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value)
 SELECT i,'tdx','symbol','sh'||CAST(600000+i AS VARCHAR) FROM range(1,6001) r(i);`)
	if err != nil {
		t.Fatal(err)
	}
	members := make([]string, 6001)
	for i := 0; i < 6000; i++ {
		members[i] = fmt.Sprintf("sh%d", 600001+i)
	}
	members[6000] = "sh699999"
	snapshot := classificationSnapshot(members...)
	day := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	for i := 0; i < 2; i++ {
		run, _ := StartIngestRun(ctx, db, "tdx", "classification", nil)
		result, err := ApplyClassificationSnapshotForRun(ctx, db, run, day, day.Add(time.Duration(i)*time.Hour), snapshot)
		if err != nil || result.Members != 6000 || result.Unresolved != 1 || result.Closed != 0 {
			t.Fatalf("snapshot %d: %+v %v", i, result, err)
		}
	}
	var count int
	if err = db.QueryRow(`SELECT count(*) FROM classification.membership WHERE last_observed_at=?`, day.Add(time.Hour)).Scan(&count); err != nil || count != 6000 {
		t.Fatalf("refreshed %d %v", count, err)
	}
}
