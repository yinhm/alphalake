package bse

import (
	"bytes"
	"crypto/sha256"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestOfficialCodeTransitionEvidence(t *testing.T) {
	python := os.Getenv("ALPHALAKE_TEST_PYTHON")
	if python == "" {
		t.Skip("set ALPHALAKE_TEST_PYTHON; required in CI")
	}
	root := filepath.Join("..", "..", "ingest", "testdata", "bse-code-transition-2025")
	paths := map[string]string{}
	metadata, err := os.ReadFile(filepath.Join(root, "sources.json"))
	if err != nil {
		t.Fatal(err)
	}
	var sources map[string]struct {
		URL string `json:"url"`
		SHA string `json:"sha256"`
	}
	if err = json.Unmarshal(metadata, &sources); err != nil {
		t.Fatal(err)
	}
	for role, url := range URLs {
		paths[role] = filepath.Join(root, role+".html")
		raw, err := os.ReadFile(paths[role])
		if err != nil {
			t.Fatal(err)
		}
		if sources[role].URL != url || sources[role].SHA != fmt.Sprintf("%x", sha256.Sum256(raw)) {
			t.Fatal("frozen evidence changed", role)
		}
	}
	s, hash, err := Parse(t.Context(), python, "parse.py", paths)
	if err != nil {
		t.Fatal(err)
	}
	if len(hash) != 64 || len(Digest(s)) != 64 {
		t.Fatal("parser/snapshot digest missing")
	}
	for role, source := range sources {
		if s.Sources[role] != source.SHA {
			t.Fatal("source digest mismatch", role)
		}
	}
	f, err := os.Open(filepath.Join(root, "transitions.csv"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 249 {
		t.Fatal("incomplete frozen CSV")
	}
	for i, r := range s.Transitions {
		want := rows[i+1]
		if r.OldCode != want[0] || r.NewCode != want[1] || r.SourceName != want[2] || r.SourceListingDate != want[3] || r.SwitchDate != want[4] || r.SourceRow != i+1 {
			t.Fatal("source semantic drift", r)
		}
	}
	for _, mutation := range []string{"footnote", "date", "incomplete"} {
		t.Run(mutation, func(t *testing.T) {
			changed := map[string]string{}
			for k, v := range paths {
				changed[k] = v
			}
			role := "mapping"
			old, replacement := "原精选层的挂牌日期", "北交所上市日期"
			if mutation == "date" {
				role = "rollout"
				old = "2025年10月9日"
				replacement = "2025年10月8日"
			}
			if mutation == "incomplete" {
				old = "920992"
				replacement = ""
			}
			raw, err := os.ReadFile(paths[role])
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Contains(raw, []byte(old)) {
				t.Fatal("negative mutation missed source")
			}
			raw = bytes.ReplaceAll(raw, []byte(old), []byte(replacement))
			changed[role] = filepath.Join(t.TempDir(), role+".html")
			if err = os.WriteFile(changed[role], raw, 0600); err != nil {
				t.Fatal(err)
			}
			// 即使优化环境禁用Python assert，生产校验仍必须拒绝。
			t.Setenv("PYTHONOPTIMIZE", "1")
			if _, _, err = Parse(t.Context(), python, "parse.py", changed); err == nil {
				t.Fatal("invalid evidence accepted")
			}
		})
	}
}
