// financial-storage-bench compares source-storage prototypes, not production ingestion.
// It preserves every source bit and deliberately excludes standard materialization.
package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"database/sql/driver"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	d "github.com/duckdb/duckdb-go/v2"
	"github.com/yinhm/alphalake/internal/domain"
	f "github.com/yinhm/alphalake/internal/source/tdx/financial"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

func must(e error) {
	if e != nil {
		panic(e)
	}
}
func exec(db *sql.DB, q string) { _, e := db.Exec(q); must(e) }
func main() {
	input := flag.String("package", "", "existing local gpcw ZIP (source maintenance only)")
	output := flag.String("output", "", "new empty temporary output directory")
	layout := flag.String("layout", "all", "all, eav_indexed, wide_bits, or packed_blob")
	limit := flag.Int("records", 512, "maximum source records; 0 means all")
	flag.Parse()
	if *input == "" || *output == "" || *limit < 0 {
		flag.Usage()
		os.Exit(2)
	}
	if *layout != "all" && *layout != "eav_indexed" && *layout != "wide_bits" && *layout != "packed_blob" {
		panic("invalid layout")
	}
	must(os.Mkdir(*output, 0700))
	ctx := context.Background()
	raw, e := os.ReadFile(*input)
	must(e)
	start := time.Now()
	p, e := f.ParsePackage(filepath.Base(*input), raw)
	must(e)
	parse := time.Since(start).Seconds()
	records, duplicates := uniqueRecords(p.Records)
	sort.Slice(records, func(i, j int) bool { return records[i].Code < records[j].Code })
	if *limit > 0 && len(records) > *limit {
		records = records[:*limit]
	}
	if len(records) == 0 {
		panic("empty package")
	}
	for _, r := range records {
		if len(r.Fields) != len(records[0].Fields) {
			panic("nonuniform field count")
		}
	}
	n := len(records[0].Fields)
	sum := sha256.Sum256(raw)
	hash := fmt.Sprintf("%x", sum)
	for _, mode := range []string{"eav_indexed", "wide_bits", "packed_blob"} {
		if *layout != "all" && mode != *layout {
			continue
		}
		path := filepath.Join(*output, mode+".duckdb")
		db, e := store.Open(ctx, path)
		must(e)
		db.SetMaxOpenConns(1)
		exec(db, "CREATE SCHEMA fundamental")
		exec(db, "CREATE SCHEMA core")
		exec(db, "CREATE TABLE core.instrument(instrument_id BIGINT,exchange_mic VARCHAR)")
		if mode == "eav_indexed" {
			schema, e := os.ReadFile("internal/store/duckdb/schema.sql")
			must(e)
			exec(db, "CREATE SEQUENCE fundamental.provider_fact_id_seq")
			for _, line := range strings.Split(string(schema), "\n") {
				if strings.HasPrefix(line, "CREATE TABLE fundamental.provider_fact(") {
					exec(db, line)
				}
			}
		} else {
			cols := "artifact_id BIGINT,provider_code VARCHAR,report_period DATE,market_marker UTINYINT"
			if mode == "packed_blob" {
				cols += ",bits BLOB"
			} else {
				for j := 0; j < n; j++ {
					cols += fmt.Sprintf(",b%d UINTEGER", j+1)
				}
			}
			exec(db, "CREATE TABLE fundamental.records("+cols+", UNIQUE(artifact_id,provider_code))")
		}
		start = time.Now()
		if mode == "eav_indexed" {
			rr := make([]domain.ProviderFinancialRecord, len(records))
			for i, r := range records {
				rr[i] = domain.ProviderFinancialRecord{InstrumentID: int64(i + 1), Provider: "tdx", ProviderCode: r.Code, MarketMarker: r.MarketMarker, ReportPeriod: r.ReportPeriod, ProviderFields: r.Fields, SourceFile: p.Filename, ArtifactID: 1}
			}
			_, e := store.ReconcileProviderFinancialRecordsForArtifact(ctx, db, 1, "tdx", hash, rr)
			must(e)
		} else {
			conn, e := db.Conn(ctx)
			must(e)
			must(conn.Raw(func(raw any) error {
				a, e := d.NewAppender(raw.(driver.Conn), "alphalake", "fundamental", "records")
				if e != nil {
					return e
				}
				for _, r := range records {
					row := []driver.Value{int64(1), r.Code, r.ReportPeriod, r.MarketMarker}
					if mode == "packed_blob" {
						b := make([]byte, n*4)
						for j, v := range r.Fields {
							binary.LittleEndian.PutUint32(b[j*4:], v.Bits)
						}
						row = append(row, b)
					} else {
						for _, v := range r.Fields {
							row = append(row, v.Bits)
						}
					}
					if e = a.AppendRow(row...); e != nil {
						return e
					}
				}
				return a.Close()
			}))
			must(conn.Close())
		}
		exec(db, "CHECKPOINT")
		must(db.Close())
		write := time.Since(start).Seconds()
		st, e := os.Stat(path)
		must(e)
		db, e = store.OpenReadOnly(ctx, path)
		must(e)
		q := "SELECT provider_code,provider_field,value_float32_bits FROM fundamental.provider_fact ORDER BY provider_code,provider_field"
		if mode != "eav_indexed" {
			q = "SELECT * FROM fundamental.records ORDER BY provider_code"
		}
		start = time.Now()
		rows, e := db.Query(q)
		must(e)
		cols, e := rows.Columns()
		must(e)
		count := 0
		bits := map[string][]uint32{}
		for rows.Next() {
			vs := make([]any, len(cols))
			ptr := make([]any, len(cols))
			for j := range vs {
				ptr[j] = &vs[j]
			}
			must(rows.Scan(ptr...))
			if mode == "eav_indexed" {
				code := vs[0].(string)
				var pos int
				fmt.Sscanf(vs[1].(string), "FN%d", &pos)
				if bits[code] == nil {
					bits[code] = make([]uint32, n)
				}
				bits[code][pos-1] = uint32(vs[2].(uint64))
			} else {
				code := vs[1].(string)
				b := make([]uint32, n)
				for j := range b {
					if mode == "packed_blob" {
						b[j] = binary.LittleEndian.Uint32(vs[4].([]byte)[j*4:])
					} else {
						b[j] = vs[4+j].(uint32)
					}
				}
				bits[code] = b
			}
			count++
		}
		must(rows.Err())
		must(rows.Close())
		read := time.Since(start).Seconds()
		must(db.Close())
		if len(bits) != len(records) {
			panic("record count differs")
		}
		for _, r := range records {
			for j, v := range r.Fields {
				if bits[r.Code][j] != v.Bits {
					panic("bits differ")
				}
			}
		}
		must(json.NewEncoder(os.Stdout).Encode(map[string]any{"mode": mode, "records": len(records), "fields": n, "zip_sha256": hash, "parse_seconds": parse, "write_checkpoint_close_seconds": write, "full_read_decode_seconds": read, "rows": count, "bytes": st.Size(), "all_bits_equal": true, "identical_duplicates": duplicates}))
	}
}

// The benchmark operates on distinct source codes, as the production reconciler
// does after identity resolution. Never select an arbitrary conflicting record.
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
