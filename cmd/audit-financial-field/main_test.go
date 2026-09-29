package main

import (
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
