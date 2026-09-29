package main

import (
	f "github.com/yinhm/alphalake/internal/source/tdx/financial"
	"math"
	"testing"
)

func TestSourceZeroDistinctFromAbsentAndInvalid(t *testing.T) {
	c := counts{}
	for _, v := range []struct {
		size, index int
		value       float64
		state       string
	}{{56, 55, 0, "zero"}, {56, 55, 123, "nonzero"}, {55, 55, 0, "missing_position"}, {56, 55, math.NaN(), "invalid_value"}} {
		if got := c.add(v.size, v.index, v.value); got != v.state {
			t.Fatal(got, v.state)
		}
	}
	if c != (counts{Records: 4, Zero: 1, Nonzero: 1, Missing: 1, Invalid: 1}) {
		t.Fatal(c)
	}
}

func TestBatchFields(t *testing.T) {
	catalog, err := f.FieldCatalog()
	if err != nil {
		t.Fatal(err)
	}
	fields, err := selectFields(catalog, "short_term_borrowings,long_term_borrowings")
	if err != nil || len(fields) != 2 || fields[0].Name != "short_term_borrowings" {
		t.Fatal(fields, err)
	}
	for _, names := range []string{"", "short_term_borrowings,", "short_term_borrowings,short_term_borrowings", "unknown", "financial_loans_and_advances"} {
		if _, err := selectFields(catalog, names); err == nil {
			t.Fatal("invalid selection accepted", names)
		}
	}
}
