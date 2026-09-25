package main

import (
	"testing"

	"github.com/yinhm/alphalake/internal/domain"
	f "github.com/yinhm/alphalake/internal/source/tdx/financial"
)

func TestBenchmarkRejectsConflictingRawBits(t *testing.T) {
	a := f.Record{Code: "300866", Fields: []domain.ProviderFloat32{{Bits: 0}}}
	b := f.Record{Code: "300866", Fields: []domain.ProviderFloat32{{Bits: 0}}}
	records, n := uniqueRecords([]f.Record{a, b})
	if len(records) != 1 || n != 1 {
		t.Fatal("identical duplicate not accounted for")
	}
	// +0 and -0 compare numerically equal; raw evidence is still different.
	b.Fields[0].Bits = 0x80000000
	defer func() {
		if recover() == nil {
			t.Fatal("conflicting bit evidence accepted")
		}
	}()
	uniqueRecords([]f.Record{a, b})
}
