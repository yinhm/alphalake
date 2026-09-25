// financial-storage-bench measures the current ZIP-to-standard-wide-table path.
// It never persists a second raw numeric representation.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"runtime/pprof"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	f "github.com/yinhm/alphalake/internal/source/tdx/financial"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

func must(e error) {
	if e != nil {
		panic(e)
	}
}
func main() {
	source := flag.String("database", "", "wide candidate database, read-only")
	root := flag.String("root", "", "immutable archive root")
	output := flag.String("output", "", "new temporary directory")
	flag.Parse()
	if *source == "" || *root == "" || *output == "" {
		flag.Usage()
		os.Exit(2)
	}
	must(os.Mkdir(*output, 0700))
	ctx := context.Background()
	db, e := store.Open(ctx, filepath.Join(*output, "benchmark.duckdb"))
	must(e)
	db.SetMaxOpenConns(1)
	absolute, e := filepath.Abs(*source)
	must(e)
	// Paths are SQL literals, never shell commands.
	quote := func(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }
	_, e = db.ExecContext(ctx, `ATTACH `+quote(absolute)+` AS original (READ_ONLY); CREATE SCHEMA fundamental`)
	must(e)
	for _, table := range []string{"field", "provider_field", "provider_filing_link", "filing"} {
		_, e = db.ExecContext(ctx, `CREATE TABLE fundamental.`+table+` AS SELECT * FROM original.fundamental.`+table)
		must(e)
	}
	var id int64
	var path, hash, name string
	must(db.QueryRowContext(ctx, `SELECT a.artifact_id,a.local_path,a.sha256,a.source_locator FROM original.meta.artifact a JOIN (SELECT r.artifact_id,count(*) n FROM original.fundamental.statement_snapshot s JOIN original.fundamental.source_record r USING(source_record_id) GROUP BY r.artifact_id ORDER BY n DESC LIMIT 1) p USING(artifact_id)`).Scan(&id, &path, &hash, &name))
	raw, e := os.ReadFile(filepath.Join(*root, path))
	must(e)
	if fmt.Sprintf("%x", sha256.Sum256(raw)) != hash {
		panic("archive digest mismatch")
	}
	parseStart := time.Now()
	pkg, e := f.ParsePackage(filepath.Base(name), raw)
	must(e)
	unique, duplicates := uniqueRecords(pkg.Records)
	identities := map[string]int64{}
	rs, e := db.QueryContext(ctx, `SELECT provider_code,instrument_id FROM fundamental.provider_filing_link WHERE provider_revision_key=? AND status='linked' AND instrument_id IS NOT NULL`, hash)
	must(e)
	for rs.Next() {
		var code string
		var instrument int64
		must(rs.Scan(&code, &instrument))
		identities[code] = instrument
	}
	must(rs.Err())
	must(rs.Close())
	records := []store.IndexedFinancialRecord{}
	for i, r := range unique {
		if identities[r.Code] == 0 {
			continue
		}
		records = append(records, store.IndexedFinancialRecord{ID: int64(i + 1), Revision: hash, Record: domain.ProviderFinancialRecord{InstrumentID: identities[r.Code], Provider: "tdx", ProviderCode: r.Code, ReportPeriod: r.ReportPeriod, ProviderFields: r.Fields}})
	}
	parseSeconds := time.Since(parseStart).Seconds()
	fields, e := store.LoadSnapshotFields(ctx, db)
	must(e)
	tx, e := db.BeginTx(ctx, nil)
	must(e)
	must(store.CreateFinancialSnapshotTables(ctx, tx, fields))
	must(tx.Commit())
	profile, e := os.Create(filepath.Join(*output, "cpu.pprof"))
	must(e)
	must(pprof.StartCPUProfile(profile))
	start := time.Now()
	conn, e := db.Conn(ctx)
	must(e)
	_, e = conn.ExecContext(ctx, "BEGIN")
	must(e)
	_, e = conn.ExecContext(ctx, `SET enable_profiling='json'; SET profiling_mode='detailed'; SET profiling_output=`+quote(filepath.Join(*output, "sql-profile.json")))
	must(e)
	result, e := store.MaterializeFinancialSnapshotBatch(ctx, conn, 1, fields, records)
	must(e)
	_, e = conn.ExecContext(ctx, "PRAGMA disable_profiling")
	must(e)
	_, e = conn.ExecContext(ctx, "COMMIT")
	must(e)
	must(conn.Close())
	_, e = db.ExecContext(ctx, "CHECKPOINT")
	must(e)
	must(db.Close())
	duration := time.Since(start).Seconds()
	pprof.StopCPUProfile()
	must(profile.Close())
	stat, e := os.Stat(filepath.Join(*output, "benchmark.duckdb"))
	must(e)
	must(json.NewEncoder(os.Stdout).Encode(map[string]any{"artifact_sha256": hash, "records": len(records), "identical_duplicates": duplicates, "parse_seconds": parseSeconds, "materialize_checkpoint_close_seconds": duration, "bytes": stat.Size(), "counts": result}))
}

func uniqueRecords(input []f.Record) ([]f.Record, int) {
	out := make([]f.Record, 0, len(input))
	seen := map[string]f.Record{}
	duplicates := 0
	for _, r := range input {
		if old, ok := seen[r.Code]; ok {
			if old.MarketMarker != r.MarketMarker || !old.ReportPeriod.Equal(r.ReportPeriod) || len(old.Fields) != len(r.Fields) {
				panic("conflicting duplicate source record")
			}
			for i, v := range r.Fields {
				if old.Fields[i].Bits != v.Bits {
					panic("conflicting duplicate source bits")
				}
			}
			duplicates++
			continue
		}
		seen[r.Code] = r
		out = append(out, r)
	}
	return out, duplicates
}
