package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

// benchmarkReplay copies a closed source and invalidates only the requested
// package signatures. Optional snapshot removal models a controlled backfill;
// no source records, rejection evidence or authoritative database are removed.
func benchmarkReplay(ctx context.Context, source, output, periods, codesFile string) {
	for _, period := range strings.Split(periods, ",") {
		_, err := time.Parse("2006-01-02", period)
		must(err)
	}
	codes := []string{}
	if codesFile != "" {
		raw, err := os.ReadFile(codesFile)
		must(err)
		codes = strings.Fields(string(raw))
		if len(codes) == 0 {
			panic("empty restore codes")
		}
		for _, code := range codes {
			if len(code) != 6 || strings.Trim(code, "0123456789") != "" {
				panic("invalid restore code")
			}
		}
	}
	if _, err := os.Stat(source + ".wal"); !os.IsNotExist(err) {
		panic("benchmark requires a closed source without WAL")
	}
	original, err := store.OpenReadOnly(ctx, source)
	must(err)
	defer original.Close()
	in, err := os.Open(source)
	must(err)
	defer in.Close()
	path := filepath.Join(output, "benchmark.duckdb")
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	must(err)
	hash := sha256.New()
	_, err = io.Copy(io.MultiWriter(file, hash), in)
	must(err)
	must(file.Close())
	must(in.Close())
	must(original.Close())
	db, err := store.OpenInitialized(ctx, path)
	must(err)
	defer db.Close()
	db.SetMaxOpenConns(1)
	_, err = db.ExecContext(ctx, `DELETE FROM fundamental.materialization_state WHERE artifact_id IN
 (SELECT DISTINCT artifact_id FROM fundamental.source_record WHERE report_period IN (SELECT unnest(string_split(?,','))::DATE))`, periods)
	must(err)
	if len(codes) > 0 {
		_, err = db.ExecContext(ctx, `CREATE TEMP TABLE _bench_restore AS SELECT source_record_id FROM fundamental.source_record
 WHERE provider_code IN (SELECT unnest(string_split(?,','))) AND report_period IN (SELECT unnest(string_split(?,','))::DATE)`, strings.Join(codes, ","), periods)
		must(err)
		_, err = db.ExecContext(ctx, `DELETE FROM fundamental.statement_snapshot WHERE source_record_id IN(SELECT source_record_id FROM _bench_restore);
 DELETE FROM fundamental.statement_field_run WHERE source_record_id IN(SELECT source_record_id FROM _bench_restore)`)
		must(err)
	}
	_, err = db.ExecContext(ctx, `SET enable_profiling='json'; SET profiling_mode='detailed'`)
	must(err)
	start := time.Now()
	// This synthetic run belongs only to the disposable benchmark, never main.
	result, err := store.MaterializeCanonicalFundamentals(ctx, db, 999999, "tdx")
	must(err)
	seconds := time.Since(start).Seconds()
	_, err = db.ExecContext(ctx, `PRAGMA disable_profiling`)
	must(err)
	must(db.Close())
	stat, err := os.Stat(path)
	must(err)
	must(json.NewEncoder(os.Stdout).Encode(map[string]any{
		"source_sha256": fmt.Sprintf("%x", hash.Sum(nil)), "periods": strings.Split(periods, ","), "restore_codes": codes,
		"materialize_seconds": seconds, "bytes": stat.Size(), "counts": result,
		"scope": "disposable_copy_replay_not_publication; synthetic_run_999999",
	}))
}
