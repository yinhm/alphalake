package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCopyNewRejectsOverwriteAndBadHash(t *testing.T) {
	dir := t.TempDir()
	src := filepath.Join(dir, "source")
	dst := filepath.Join(dir, "copy")
	if err := os.WriteFile(src, []byte("accepted evidence"), 0600); err != nil {
		t.Fatal(err)
	}
	hash, err := fileHash(src)
	if err != nil {
		t.Fatal(err)
	}
	if err = copyNew(src, dst, hash); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(src, []byte("changed evidence"), 0600); err != nil {
		t.Fatal(err)
	}
	if err = copyNew(src, dst, hash); err == nil {
		t.Fatal("existing backup overwritten")
	}
	after, err := fileHash(dst)
	if err != nil || after != hash {
		t.Fatal("existing copy changed", err)
	}
	if err = copyNew(src, filepath.Join(dir, "bad-copy"), hash); err == nil || !strings.Contains(err.Error(), "hash differs") {
		t.Fatal("corrupt copy accepted", err)
	}
}

func TestChangedBaselineRefusesBeforePublication(t *testing.T) {
	dir := t.TempDir()
	base := filepath.Join(dir, "base")
	receipt := filepath.Join(dir, "receipt.json")
	if err := os.WriteFile(base, []byte("changed database"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(receipt, []byte(`{"base_sha256":"wrong"}`), 0600); err != nil {
		t.Fatal(err)
	}
	before := os.Args
	defer func() { os.Args = before }()
	os.Args = []string{"check-cash-publication", base, filepath.Join(dir, "absent-candidate"), receipt, "--publish"}
	if err := run(); err == nil || !strings.Contains(err.Error(), "base changed") {
		t.Fatal("changed baseline accepted", err)
	}
	if _, err := os.Stat(base + ".pre-cash-history-20260911"); !os.IsNotExist(err) {
		t.Fatal("publication started", err)
	}
}
