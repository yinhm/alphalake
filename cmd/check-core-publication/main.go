// check-core-publication 验收固定结构升级或两家公司已审核资产，并可原子发布副本。
package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"

	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

func digest(path string) (string, error) {
	f, e := os.Open(path)
	if e != nil {
		return "", e
	}
	defer f.Close()
	h := sha256.New()
	_, e = io.Copy(h, f)
	return fmt.Sprintf("%x", h.Sum(nil)), e
}
func literal(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }
func ident(s string) string   { return `"` + strings.ReplaceAll(s, `"`, `""`) + `"` }

type summary struct {
	Rows int64  `json:"rows"`
	XOR  string `json:"xor64"`
	Sum  string `json:"sum128"`
}

func run(args []string) error {
	if len(args) < 2 {
		return fmt.Errorf("usage: check-core-publication BASE CANDIDATE [--publish --base-sha256 HASH --candidate-sha256 HASH]")
	}
	base, candidate := args[0], args[1]
	fs := flag.NewFlagSet("check-core-publication", flag.ContinueOnError)
	assets := fs.Bool("reviewed-assets", false, "fixed two-company schema44 asset publication")
	rawRoot := fs.String("raw-root", "", "artifact root for reviewed asset publication")
	publish := fs.Bool("publish", false, "publish validated candidate with original backup")
	wantBase := fs.String("base-sha256", "", "required publication baseline hash")
	wantCandidate := fs.String("candidate-sha256", "", "required publication candidate hash")
	if e := fs.Parse(args[2:]); e != nil {
		return e
	}
	if fs.NArg() != 0 {
		return fmt.Errorf("unexpected arguments")
	}
	if *publish && (*wantBase == "" || *wantCandidate == "") {
		return fmt.Errorf("publication requires both accepted file hashes")
	}
	for _, p := range []string{base, candidate} {
		if st, e := os.Stat(p + ".wal"); e == nil && st.Size() > 0 {
			return fmt.Errorf("uncheckpointed WAL: %s", p)
		} else if e != nil && !os.IsNotExist(e) {
			return e
		}
	}
	// 同现有刷新入口的协作锁；原生只读连接同时阻止DuckDB写入。
	lock, e := os.OpenFile(base+".valuation.lock", os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return e
	}
	defer lock.Close()
	if e = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); e != nil {
		return e
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	ctx := context.Background()
	db, e := duck.Open(ctx, ":memory:")
	if e != nil {
		return e
	}
	defer db.Close()
	for alias, p := range map[string]string{"baseline": base, "candidate": candidate} {
		if _, e = db.ExecContext(ctx, "ATTACH "+literal(p)+" AS "+alias+" (READ_ONLY)"); e != nil {
			return e
		}
	}
	bh, e := digest(base)
	if e != nil {
		return e
	}
	ch, e := digest(candidate)
	if e != nil {
		return e
	}
	if *wantBase != "" && bh != *wantBase {
		return fmt.Errorf("baseline changed")
	}
	if *wantCandidate != "" && ch != *wantCandidate {
		return fmt.Errorf("candidate changed")
	}
	var bv, cv int
	if e = db.QueryRowContext(ctx, `SELECT max(version) FROM baseline.meta.schema_version`).Scan(&bv); e != nil {
		return e
	}
	if e = db.QueryRowContext(ctx, `SELECT max(version) FROM candidate.meta.schema_version`).Scan(&cv); e != nil {
		return e
	}
	checks := map[string]summary{}
	if *assets {
		if bv != 44 || cv != 44 || *rawRoot == "" {
			return fmt.Errorf("reviewed assets require schema44 and raw root")
		}
		checks, e = checkReviewedAssets(ctx, db, *rawRoot)
		if e != nil {
			return e
		}
	} else {
		if (bv != 33 && bv != 39) || cv != 44 {
			return fmt.Errorf("only schema33/39 to44 supported, got %d/%d", bv, cv)
		}
		// 在临时关系复算本次确实应改变的目录行，绝不修改基线。
		if _, e = db.ExecContext(ctx, `CREATE TEMP TABLE expected_fields AS SELECT * FROM baseline.fundamental.provider_field`); e != nil {
			return e
		}
		migrations, e := duck.Migrations()
		if e != nil {
			return e
		}
		for _, m := range migrations {
			if m.Version > bv && m.Version >= 38 && m.Version <= 40 {
				body, err := duck.Read(m.Name)
				if err != nil {
					return err
				}
				if _, e = db.ExecContext(ctx, strings.ReplaceAll(string(body), "fundamental.provider_field", "temp.main.expected_fields")); e != nil {
					return e
				}
			}
		}
		rows, e := db.QueryContext(ctx, `SELECT table_schema,table_name FROM information_schema.tables WHERE table_catalog='baseline' AND table_type='BASE TABLE' ORDER BY table_schema,table_name`)
		if e != nil {
			return e
		}
		var tables [][2]string
		for rows.Next() {
			var s, n string
			if e = rows.Scan(&s, &n); e != nil {
				rows.Close()
				return e
			}
			tables = append(tables, [2]string{s, n})
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return e
		}
		checks = map[string]summary{}
		summarize := func(q string) (summary, error) {
			var r summary
			err := db.QueryRowContext(ctx, `SELECT count(*),CAST(coalesce(bit_xor(hash(t)),0) AS VARCHAR),CAST(coalesce(sum(CAST(hash(t) AS HUGEINT)),0) AS VARCHAR) FROM (`+q+`) t`).Scan(&r.Rows, &r.XOR, &r.Sum)
			return r, err
		}
		for _, t := range tables {
			name := t[0] + "." + t[1]
			old := "SELECT * FROM baseline." + ident(t[0]) + "." + ident(t[1])
			next := "SELECT * FROM candidate." + ident(t[0]) + "." + ident(t[1])
			switch {
			case t[0] == "ref":
				target := "core"
				if t[1] == "trading_calendar" {
					target = "market"
				}
				next = "SELECT * FROM candidate." + target + "." + ident(t[1])
			case name == "reference.country_risk":
				next = "SELECT * FROM candidate.reference.risk_observation"
			case name == "fundamental.provider_field":
				old = "SELECT * FROM temp.main.expected_fields"
			case name == "fundamental.reviewed_supplement":
				next = "SELECT * EXCLUDE(review_state) FROM candidate.fundamental.reviewed_supplement"
			case name == "meta.schema_version":
				next += fmt.Sprintf(" WHERE version<=%d", bv)
			}
			a, err := summarize(old)
			if err != nil {
				return fmt.Errorf("%s baseline: %w", name, err)
			}
			b, err := summarize(next)
			if err != nil {
				return fmt.Errorf("%s candidate: %w", name, err)
			}
			if a != b {
				return fmt.Errorf("%s content summary differs: %+v / %+v", name, a, b)
			}
			checks[name] = a
			fmt.Fprintln(os.Stderr, "checked", name, a.Rows)
		}
		// 本次实际库没有镜像审核，不能把其迁移遗漏藏在新表检查之外。
		var n int
		for _, q := range []string{
			`SELECT count(*) FROM baseline.meta.validation_result WHERE source='document-review' AND rule_code='reviewed_mirror_binding' AND passed`,
			`SELECT count(*) FROM candidate.fundamental.document_review`,
			`SELECT count(*) FROM candidate.fundamental.reviewed_supplement WHERE review_state IS DISTINCT FROM 'active'`,
			`SELECT count(*) FROM candidate.fundamental.supplement_review_history WHERE action<>'publish' OR reviewed_at IS NOT NULL OR recorded_at IS NOT NULL OR supersedes_sha256 IS NOT NULL`,
			`SELECT count(*) FROM ((SELECT import_sha256,reviewed_record FROM baseline.fundamental.reviewed_supplement EXCEPT ALL SELECT import_sha256,reviewed_record FROM candidate.fundamental.supplement_review_history) UNION ALL (SELECT import_sha256,reviewed_record FROM candidate.fundamental.supplement_review_history EXCEPT ALL SELECT import_sha256,reviewed_record FROM baseline.fundamental.reviewed_supplement))`,
		} {
			if e = db.QueryRowContext(ctx, q).Scan(&n); e != nil {
				return e
			}
			if n != 0 {
				return fmt.Errorf("unexpected review migration content: %s", q)
			}
		}
		// 其余新增物理表在本轮两个实际库中应为空，不能漏查新增数据。
		known := map[string]bool{"fundamental.document_review": true, "fundamental.supplement_review_history": true, "reference.equity_risk_premium": true}
		for _, t := range tables {
			name := t[0] + "." + t[1]
			if t[0] == "ref" {
				name = "core." + t[1]
				if t[1] == "trading_calendar" {
					name = "market.trading_calendar"
				}
			}
			known[name] = true
		}
		newRows, err := db.QueryContext(ctx, `SELECT table_schema||'.'||table_name FROM information_schema.tables WHERE table_catalog='candidate' AND table_type='BASE TABLE'`)
		if err != nil {
			return err
		}
		var added []string
		for newRows.Next() {
			var name string
			if err = newRows.Scan(&name); err != nil {
				newRows.Close()
				return err
			}
			if !known[name] {
				added = append(added, name)
			}
		}
		err = newRows.Err()
		newRows.Close()
		if err != nil {
			return err
		}
		for _, name := range added {
			parts := strings.SplitN(name, ".", 2)
			if e = db.QueryRowContext(ctx, "SELECT count(*) FROM candidate."+ident(parts[0])+"."+ident(parts[1])).Scan(&n); e != nil {
				return e
			}
			if n != 0 {
				return fmt.Errorf("unexpected rows in new table %s: %d", name, n)
			}
		}
		if e = db.QueryRowContext(ctx, `SELECT count(*) FROM candidate.meta.schema_version`).Scan(&n); e != nil {
			return e
		}
		if n != 44 {
			return fmt.Errorf("incomplete migration history: %d", n)
		}

	}
	status := "prepublication_checked"
	backup := ""
	if *publish {
		backup = base + ".pre-core-risk-20260918"
		if *assets {
			backup = base + ".pre-reviewed-assets-20260918"
		}
		if _, e = os.Lstat(backup); !os.IsNotExist(e) {
			return fmt.Errorf("backup path already exists or unreadable: %s", backup)
		}
		if e = os.Link(base, backup); e != nil {
			return e
		}
		if e = syncDir(filepath.Dir(base)); e != nil {
			return e
		}
		// 候选文件与旧库均持有原生读锁；替换后原库inode由备份保留。
		if e = os.Rename(candidate, base); e != nil {
			return e
		}
		if e = syncDir(filepath.Dir(base)); e != nil {
			return e
		}
		status = "published_with_backup"
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"status": status, "base_schema": bv, "candidate_schema": cv, "base_sha256": bh, "candidate_sha256": ch, "backup": backup, "checks": checks, "comparison": "row counts plus unordered all-column hash XOR/sum; not cryptographic proof of row equality"})
}
func syncDir(p string) error {
	f, e := os.Open(p)
	if e != nil {
		return e
	}
	defer f.Close()
	return f.Sync()
}
func main() {
	if e := run(os.Args[1:]); e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
}
