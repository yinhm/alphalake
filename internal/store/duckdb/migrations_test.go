package duckdb

import "testing"

func TestMigrationOrder(t *testing.T) {
	names, err := Names()
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"001_meta.sql",
		"002_ref.sql",
		"003_market.sql",
		"004_fundamental.sql",
		"005_classification.sql",
		"006_adjustment_lineage.sql",
	}
	if len(names) != len(want) {
		t.Fatalf("got %v", names)
	}
	for i := range want {
		if names[i] != want[i] {
			t.Fatalf("migration %d = %q, want %q", i, names[i], want[i])
		}
	}
}
