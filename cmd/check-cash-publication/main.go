// check-cash-publication 默认只读复核；--publish 保留原库硬链接备份后原子发布已验收副本。
package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"flag"
	"fmt"
	duck "github.com/yinhm/alphalake/internal/store/duckdb"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const newFactSourceMismatchSQL = `SELECT count(*) FROM candidate.fundamental.fact f LEFT JOIN candidate.fundamental.provider_fact p ON f.provider_fact_id=p.provider_fact_id LEFT JOIN candidate.fundamental.provider_field m ON m.source=f.primary_source AND m.provider_field=f.source_provider_field WHERE NOT EXISTS(SELECT 1 FROM baseline.fundamental.fact b WHERE b.fact_id=f.fact_id) AND (p.provider_fact_id IS NULL OR m.provider_field IS NULL OR f.value IS DISTINCT FROM CAST(p.value*m.value_multiplier AS DECIMAL(38,10)) OR f.primary_source IS DISTINCT FROM p.source OR f.report_period IS DISTINCT FROM p.report_period OR f.provider_code IS DISTINCT FROM p.provider_code OR f.instrument_id IS DISTINCT FROM p.instrument_id OR f.source_provider_field IS DISTINCT FROM p.provider_field OR f.revision_key IS DISTINCT FROM p.revision_key OR f.canonical_field IS DISTINCT FROM m.canonical_field OR f.unit IS DISTINCT FROM m.unit)`

func fileHash(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err = io.Copy(h, f); err != nil {
		return "", err
	}
	return fmt.Sprintf("%x", h.Sum(nil)), nil
}

func run() error {
	if len(os.Args) < 4 {
		return fmt.Errorf("usage: check-cash-publication BASE CANDIDATE ACCEPTANCE_JSON [--publish] [--earnings]")
	}
	flags := flag.NewFlagSet("check-cash-publication", flag.ContinueOnError)
	publishFlag := flags.Bool("publish", false, "publish with backup")
	earnings := flags.Bool("earnings", false, "fixed schema38 to39 Anker history acceptance")
	if err := flags.Parse(os.Args[4:]); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("unexpected arguments")
	}
	publish := *publishFlag
	kind := "cash"
	if *earnings {
		kind = "earnings"
	}
	base, candidate := os.Args[1], os.Args[2]
	var receipt struct {
		BaseHash      string `json:"base_sha256"`
		CandidateHash string `json:"candidate_sha256"`
	}
	raw, err := os.ReadFile(os.Args[3])
	if err != nil {
		return err
	}
	if err = json.Unmarshal(raw, &receipt); err != nil {
		return err
	}
	if publish && *earnings && receipt.CandidateHash == "" {
		return fmt.Errorf("earnings publication requires a prepublication receipt with candidate_sha256")
	}
	baseHash, err := fileHash(base)
	if err != nil {
		return err
	}
	if baseHash != receipt.BaseHash {
		return fmt.Errorf("base changed since acceptance")
	}
	for _, path := range []string{base, candidate} {
		if stat, err := os.Stat(path + ".wal"); err == nil && stat.Size() > 0 {
			return fmt.Errorf("uncheckpointed WAL: %s", path)
		} else if err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	ctx := context.Background()
	db, err := duck.Open(ctx, ":memory:")
	if err != nil {
		return err
	}
	defer db.Close()
	for alias, path := range map[string]string{"baseline": base, "candidate": candidate} {
		if _, err = db.ExecContext(ctx, "ATTACH '"+strings.ReplaceAll(path, "'", "''")+"' AS "+alias+" (READ_ONLY)"); err != nil {
			return err
		}
	}
	// Recheck after native read locks are held, before trusting the baseline hash.
	lockedHash, err := fileHash(base)
	if err != nil {
		return err
	}
	if lockedHash != baseHash {
		return fmt.Errorf("base changed while acquiring read locks")
	}
	checks := map[string]int{}
	check := func(name, query string, want int) error {
		var n int
		if err := db.QueryRowContext(ctx, query).Scan(&n); err != nil {
			return fmt.Errorf("%s: %w", name, err)
		}
		checks[name] = n
		if n != want {
			return fmt.Errorf("%s: %d, want %d", name, n, want)
		}
		return nil
	}
	for _, table := range []string{"core.instrument", "core.instrument_identifier", "classification.membership", "fundamental.reviewed_supplement", "meta.checkpoint"} {
		q := fmt.Sprintf(`SELECT count(*) FROM ((SELECT * FROM baseline.%s EXCEPT ALL SELECT * FROM candidate.%s) UNION ALL (SELECT * FROM candidate.%s EXCEPT ALL SELECT * FROM baseline.%s))`, table, table, table, table)
		if err = check(table, q, 0); err != nil {
			return err
		}
	}
	for _, schema := range []string{"reference"} {
		rows, err := db.QueryContext(ctx, `SELECT table_name FROM information_schema.tables WHERE table_catalog='baseline' AND table_schema=? AND table_type='BASE TABLE' ORDER BY table_name`, schema)
		if err != nil {
			return err
		}
		var tables []string
		for rows.Next() {
			var name string
			if err = rows.Scan(&name); err != nil {
				rows.Close()
				return err
			}
			tables = append(tables, name)
		}
		if err = rows.Err(); err != nil {
			rows.Close()
			return err
		}
		rows.Close()
		for _, name := range tables {
			table := schema + `."` + strings.ReplaceAll(name, `"`, `""`) + `"`
			q := fmt.Sprintf(`SELECT count(*) FROM ((SELECT * FROM baseline.%s EXCEPT ALL SELECT * FROM candidate.%s) UNION ALL (SELECT * FROM candidate.%s EXCEPT ALL SELECT * FROM baseline.%s))`, table, table, table, table)
			if err = check(table, q, 0); err != nil {
				return err
			}
		}
	}
	queries := []struct {
		name, sql string
		want      int
	}{
		{"base_schema", `SELECT max(version) FROM baseline.meta.schema_version`, 37},
		{"candidate_schema", `SELECT max(version) FROM candidate.meta.schema_version`, 38},
		{"candidate_facts", `SELECT count(*) FROM candidate.fundamental.fact`, 2023185},
		{"old_facts_changed_including_runtime", `SELECT count(*) FROM (SELECT * FROM baseline.fundamental.fact EXCEPT ALL SELECT * FROM candidate.fundamental.fact)`, 0},
		{"old_artifacts_changed", `SELECT count(*) FROM (SELECT * FROM baseline.meta.artifact EXCEPT ALL SELECT * FROM candidate.meta.artifact)`, 0},
		{"old_runs_changed", `SELECT count(*) FROM (SELECT * FROM baseline.meta.ingest_run EXCEPT ALL SELECT * FROM candidate.meta.ingest_run)`, 0},
		{"old_anker_source_facts_changed", `SELECT count(*) FROM (SELECT * FROM baseline.fundamental.provider_fact WHERE provider_code='300866' EXCEPT ALL SELECT * FROM candidate.fundamental.provider_fact WHERE provider_code='300866')`, 0},
		{"other_mappings_changed", `SELECT count(*) FROM ((SELECT * FROM baseline.fundamental.provider_field WHERE provider_field<>'FN114' EXCEPT ALL SELECT * FROM candidate.fundamental.provider_field WHERE provider_field<>'FN114') UNION ALL (SELECT * FROM candidate.fundamental.provider_field WHERE provider_field<>'FN114' EXCEPT ALL SELECT * FROM baseline.fundamental.provider_field WHERE provider_field<>'FN114'))`, 0},
		{"capex_other_mapping_columns_changed", `SELECT count(*) FROM ((SELECT * EXCLUDE(valid_from,notes) FROM baseline.fundamental.provider_field WHERE provider_field='FN114' EXCEPT ALL SELECT * EXCLUDE(valid_from,notes) FROM candidate.fundamental.provider_field WHERE provider_field='FN114') UNION ALL (SELECT * EXCLUDE(valid_from,notes) FROM candidate.fundamental.provider_field WHERE provider_field='FN114' EXCEPT ALL SELECT * EXCLUDE(valid_from,notes) FROM baseline.fundamental.provider_field WHERE provider_field='FN114'))`, 0},
		{"other_filings_content_changed", `SELECT count(*) FROM ((SELECT * EXCLUDE(ingest_run_id,last_seen_at) FROM baseline.fundamental.filing WHERE NOT(source='cninfo' AND source_filing_id IN ('1221057646','1221558710','1223379891')) EXCEPT ALL SELECT * EXCLUDE(ingest_run_id,last_seen_at) FROM candidate.fundamental.filing WHERE NOT(source='cninfo' AND source_filing_id IN ('1221057646','1221558710','1223379891'))) UNION ALL (SELECT * EXCLUDE(ingest_run_id,last_seen_at) FROM candidate.fundamental.filing WHERE NOT(source='cninfo' AND source_filing_id IN ('1221057646','1221558710','1223379891')) EXCEPT ALL SELECT * EXCLUDE(ingest_run_id,last_seen_at) FROM baseline.fundamental.filing WHERE NOT(source='cninfo' AND source_filing_id IN ('1221057646','1221558710','1223379891'))))`, 0},
		{"other_filings_runtime_refreshed", `SELECT count(*) FROM baseline.fundamental.filing b JOIN candidate.fundamental.filing c USING(filing_id) WHERE NOT(b.source='cninfo' AND b.source_filing_id IN ('1221057646','1221558710','1223379891')) AND (b.ingest_run_id IS DISTINCT FROM c.ingest_run_id OR b.last_seen_at IS DISTINCT FROM c.last_seen_at)`, 3664},
		{"scope_date", `SELECT count(*) FROM candidate.fundamental.provider_field WHERE source='tdx' AND provider_field='FN114' AND valid_from=DATE '2024-06-30'`, 1},
		{"unexpected_added_facts", `SELECT count(*) FROM candidate.fundamental.fact f WHERE NOT EXISTS(SELECT 1 FROM baseline.fundamental.fact b WHERE b.fact_id=f.fact_id) AND NOT(provider_code='300866' AND report_period IN (DATE '2024-06-30',DATE '2024-09-30',DATE '2024-12-31') AND source_provider_field IN ('FN114','FN230','FN231','FN232','FN233','FN234','FN235','FN236','FN237','FN238'))`, 0},
		{"added_facts", `SELECT count(*) FROM candidate.fundamental.fact f WHERE NOT EXISTS(SELECT 1 FROM baseline.fundamental.fact b WHERE b.fact_id=f.fact_id)`, 30},
	}
	if *earnings {
		const fields = "('FN86','FN305','FN306','FN83','FN82','FN301')"
		for i := range queries {
			q := &queries[i]
			q.sql = strings.ReplaceAll(q.sql, "provider_field<>'FN114'", "provider_field NOT IN "+fields)
			q.sql = strings.ReplaceAll(q.sql, "provider_field='FN114'", "provider_field IN "+fields)
			q.sql = strings.ReplaceAll(q.sql, "'1221057646','1221558710','1223379891'", "'1219865739'")
			switch q.name {
			case "base_schema":
				q.want = 38
			case "candidate_schema":
				q.want = 39
			case "candidate_facts":
				q.want = 2023212
			case "scope_date":
				q.want = 6
			case "added_facts":
				q.want = 27
			case "capex_other_mapping_columns_changed":
				q.name = "earnings_other_mapping_columns_changed"
			case "unexpected_added_facts":
				q.sql = `SELECT count(*) FROM candidate.fundamental.fact f WHERE NOT EXISTS(SELECT 1 FROM baseline.fundamental.fact b WHERE b.fact_id=f.fact_id) AND NOT(provider_code='300866' AND ((report_period IN (DATE '2024-06-30',DATE '2024-09-30',DATE '2024-12-31') AND source_provider_field IN ` + fields + `) OR (report_period=DATE '2024-03-31' AND source_provider_field IN ('FN230','FN231','FN232','FN233','FN234','FN235','FN236','FN237','FN238'))))`
			}
		}
	}
	for _, q := range queries {
		if err = check(q.name, q.sql, q.want); err != nil {
			return err
		}
	}
	if *earnings {
		if err = check("new_fact_source_mismatch", newFactSourceMismatchSQL, 0); err != nil {
			return err
		}
	}
	rows, err := db.QueryContext(ctx, `SELECT local_path,sha256 FROM candidate.meta.artifact a WHERE NOT EXISTS(SELECT 1 FROM baseline.meta.artifact b WHERE b.artifact_id=a.artifact_id) ORDER BY local_path`)
	if err != nil {
		return err
	}
	type proof struct {
		Path string `json:"path"`
		SHA  string `json:"sha256"`
	}
	proofs := []proof{}
	for rows.Next() {
		var p proof
		if err = rows.Scan(&p.Path, &p.SHA); err != nil {
			rows.Close()
			return err
		}
		if !filepath.IsLocal(p.Path) {
			rows.Close()
			return fmt.Errorf("nonlocal artifact path")
		}
		digest, err := fileHash(filepath.Join(filepath.Dir(candidate), "raw", p.Path))
		if err != nil {
			rows.Close()
			return err
		}
		if digest != p.SHA {
			rows.Close()
			return fmt.Errorf("artifact hash differs: %s", p.Path)
		}
		proofs = append(proofs, p)
	}
	if err = rows.Err(); err != nil {
		rows.Close()
		return err
	}
	rows.Close()
	candidateHash, err := fileHash(candidate)
	if err != nil {
		return err
	}
	if receipt.CandidateHash != "" && candidateHash != receipt.CandidateHash {
		return fmt.Errorf("candidate changed since prepublication checks")
	}
	status := "prepublication_checks_passed"
	backup := ""
	if publish {
		backup = base + ".pre-" + kind + "-history-20260911"
		target := base + "." + kind + "-publish-new"
		for _, path := range []string{backup, target} {
			if _, err = os.Lstat(path); !os.IsNotExist(err) {
				return fmt.Errorf("refuse existing publication path: %s", path)
			}
		}
		// The READ_ONLY attachments keep both database files protected from writers through publication.
		for _, proof := range proofs {
			src := filepath.Join(filepath.Dir(candidate), "raw", proof.Path)
			dst := filepath.Join(filepath.Dir(base), "raw", proof.Path)
			if digest, err := fileHash(dst); err == nil {
				if digest != proof.SHA {
					return fmt.Errorf("existing archive differs: %s", dst)
				}
				continue
			} else if !os.IsNotExist(err) {
				return err
			}
			if err = os.MkdirAll(filepath.Dir(dst), 0755); err != nil {
				return err
			}
			if err = copyNew(src, dst, proof.SHA); err != nil {
				return err
			}
		}
		if err = copyNew(candidate, target, candidateHash); err != nil {
			return err
		}
		if _, err = db.ExecContext(ctx, "ATTACH '"+strings.ReplaceAll(target, "'", "''")+"' AS prepared (READ_ONLY)"); err != nil {
			return err
		}
		if err = os.Link(base, backup); err != nil {
			return err
		}
		if err = syncDir(filepath.Dir(base)); err != nil {
			return err
		}
		if err = os.Rename(target, base); err != nil {
			return err
		}
		if err = syncDir(filepath.Dir(base)); err != nil {
			return err
		}
		status = "published_with_backup"
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"status": status, "backup": backup, "completed_at": time.Now().UTC(), "base_sha256": baseHash, "candidate_sha256": candidateHash, "checks": checks, "new_artifacts": proofs, "scope": kind + "_history_2024_anker_only; not_full_market_historical_review"})
}
func syncDir(path string) error {
	dir, err := os.Open(path)
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}
func copyNew(src, dst, want string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer out.Close()
	if _, err = io.Copy(out, in); err != nil {
		return err
	}
	if err = out.Sync(); err != nil {
		return err
	}
	if err = out.Close(); err != nil {
		return err
	}
	digest, err := fileHash(dst)
	if err != nil {
		return err
	}
	if digest != want {
		return fmt.Errorf("copy hash differs: %s", dst)
	}
	return syncDir(filepath.Dir(dst))
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
