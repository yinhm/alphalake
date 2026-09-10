package main

import (
	"crypto/md5"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestAdditionalFieldsRealArchiveAndRejection(t *testing.T) {
	dir := t.TempDir()
	source, err := filepath.Abs("../../internal/ingest/testdata/annual-2025/gpcw20251231.zip")
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(source)
	if err != nil {
		t.Fatal(err)
	}
	put := func(name string, v any) string {
		t.Helper()
		p := filepath.Join(dir, name)
		b, e := json.Marshal(v)
		if e != nil {
			t.Fatal(e)
		}
		if e = os.WriteFile(p, b, 0600); e != nil {
			t.Fatal(e)
		}
		return p
	}
	list := filepath.Join(dir, "gpcw.txt")
	if err = os.WriteFile(list, []byte(fmt.Sprintf("gpcw20251231.zip,%x,%d\n", md5.Sum(raw), len(raw))), 0600); err != nil {
		t.Fatal(err)
	}
	manifest := put("manifest.json", []map[string]string{{"file": "gpcw20251231.zip", "path": source, "list": list, "fetched_at": time.Now().UTC().Format(time.RFC3339Nano)}})
	study := put("study.json", map[string]any{"samples": []map[string]string{{"code": "000975"}}, "additional_source_fields": []int{234, 114}})
	old := os.Args
	t.Cleanup(func() { os.Args = old })
	output := filepath.Join(dir, "archive")
	os.Args = []string{"prepare", study, manifest, output}
	main()
	b, err := os.ReadFile(filepath.Join(output, "snapshot.json"))
	if err != nil {
		t.Fatal(err)
	}
	var snapshot struct {
		Records []struct {
			Bits map[string]uint32 `json:"bits"`
		} `json:"records"`
	}
	if err = json.Unmarshal(b, &snapshot); err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Records) != 1 {
		t.Fatalf("records: %d", len(snapshot.Records))
	}
	bits := snapshot.Records[0].Bits
	// 金额来自已独立核对的annual-2025/values.csv，年包FN234仍是Q4。
	if len(bits) != 14 || bits["FN234"] != math.Float32bits(float32(987713236.25)) || bits["FN230"] != math.Float32bits(float32(2102790187.57)) {
		t.Fatalf("unexpected source fields: %v", bits)
	}
	bad := put("bad.json", map[string]any{"samples": []map[string]string{{"code": "000975"}}, "additional_source_fields": []int{234, 234}})
	rejected := filepath.Join(dir, "rejected")
	os.Args = []string{"prepare", bad, manifest, rejected}
	func() {
		defer func() {
			if r := recover(); fmt.Sprint(r) != "invalid or duplicate additional source field" {
				t.Fatalf("unexpected rejection: %v", r)
			}
		}()
		main()
	}()
	if _, err = os.Stat(rejected); !os.IsNotExist(err) {
		t.Fatalf("invalid fields created archive: %v", err)
	}
	// 实际原包对应清单被篡改时，不发布成功快照。
	if err = os.WriteFile(list, []byte(fmt.Sprintf("gpcw20251231.zip,%032d,%d\n", 0, len(raw))), 0600); err != nil {
		t.Fatal(err)
	}
	damaged := filepath.Join(dir, "damaged")
	os.Args = []string{"prepare", study, manifest, damaged}
	func() {
		defer func() {
			if r := recover(); fmt.Sprint(r) != "source size/MD5 mismatch: gpcw20251231.zip" {
				t.Fatalf("unexpected integrity rejection: %v", r)
			}
		}()
		main()
	}()
	if _, err = os.Stat(filepath.Join(damaged, "snapshot.json")); !os.IsNotExist(err) {
		t.Fatalf("damaged package published snapshot: %v", err)
	}
}
