package safe

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestHKDConversion(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip(err)
	}
	s, _, err := Parse(t.Context(), python, "parse.py", "testdata/rates.html")
	if err != nil {
		t.Fatal(err)
	}
	if s.Observations[7].Date != "2026-08-31" || s.Observations[7].Value != "0.865100000000" {
		t.Fatal(s)
	}
	for _, bad := range []func(*Snapshot){func(s *Snapshot) { s.Observations[0].Value = "86.458000000000" }, func(s *Snapshot) { s.Observations[0].Date = s.Observations[1].Date }, func(s *Snapshot) { s.Observations[0].SourceLocator = "USD" }, func(s *Snapshot) { s.Observations[0].RawValue = "0"; s.Observations[0].Value = "0.000000000000" }} {
		copy := s
		copy.Observations = append([]Rate(nil), s.Observations...)
		bad(&copy)
		if Validate(copy) == nil {
			t.Fatal("invalid FX accepted")
		}
	}
	body, err := os.ReadFile("testdata/rates.html")
	if err != nil {
		t.Fatal(err)
	}
	p := filepath.Join(t.TempDir(), "bad.html")
	if err := os.WriteFile(p, []byte(strings.ReplaceAll(string(body), "港元", "美元")), 0600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := Parse(t.Context(), python, "parse.py", p); err == nil {
		t.Fatal("wrong currency accepted")
	}
}
