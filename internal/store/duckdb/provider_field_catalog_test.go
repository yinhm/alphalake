package duckdb

import (
	"context"
	"path/filepath"
	"testing"
)

func TestTDXCoreProviderFieldCatalog(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "field-catalog.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	want := map[string]string{
		"FN230": "revenue",
		"FN231": "operating_profit",
		"FN232": "net_income_parent",
		"FN233": "adjusted_net_income",
		"FN234": "operating_cash_flow",
		"FN235": "investing_cash_flow",
		"FN236": "financing_cash_flow",
		"FN237": "net_cash_increase",
		"FN238": "total_shares",
	}
	rows, err := db.QueryContext(ctx, `
		SELECT provider_field, canonical_field
		FROM fundamental.provider_field
		WHERE source='tdx' AND provider_field BETWEEN 'FN230' AND 'FN238'
	`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	got := map[string]string{}
	for rows.Next() {
		var provider, canonical string
		if err := rows.Scan(&provider, &canonical); err != nil {
			t.Fatal(err)
		}
		got[provider] = canonical
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	if len(got) != len(want) {
		t.Fatalf("catalog=%#v", got)
	}
	for provider, canonical := range want {
		if got[provider] != canonical {
			t.Fatalf("%s=%q, want %q", provider, got[provider], canonical)
		}
	}
}
