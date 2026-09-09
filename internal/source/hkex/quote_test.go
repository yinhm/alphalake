package hkex

import (
	"bytes"
	"compress/gzip"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestHKEXClosingColumn(t *testing.T) {
	python, e := exec.LookPath("python3")
	if e != nil {
		t.Skip(e)
	}
	b, e := os.ReadFile("testdata/d260908e.html.gz")
	if e != nil {
		t.Fatal(e)
	}
	r, e := gzip.NewReader(bytes.NewReader(b))
	if e != nil {
		t.Fatal(e)
	}
	b, e = io.ReadAll(r)
	if e != nil {
		t.Fatal(e)
	}
	r.Close()
	path := filepath.Join(t.TempDir(), "quote.html")
	if e = os.WriteFile(path, b, 0600); e != nil {
		t.Fatal(e)
	}
	s, _, e := Parse(t.Context(), python, "parse.py", path)
	if e != nil || s.ObservationDate != "2026-09-08" || s.Value != "128.200000000000" {
		t.Fatal(s, e)
	}
	for _, bad := range []string{strings.Replace(string(b), "CLOSING      BID", "BID      CLOSING", 1), strings.ReplaceAll(string(b), "668 ANKER", "668 OTHER"), strings.ReplaceAll(string(b), "08 SEP 2026", "invalid")} {
		if e = os.WriteFile(path, []byte(bad), 0600); e != nil {
			t.Fatal(e)
		}
		if _, _, e = Parse(t.Context(), python, "parse.py", path); e == nil {
			t.Fatal("wrong source/date/header accepted")
		}
	}
}
