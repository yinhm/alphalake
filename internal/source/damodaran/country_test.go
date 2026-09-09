package damodaran

import (
	"encoding/json"
	"os"
	"testing"
)

func TestSelectedCountryContract(t *testing.T) {
	body, err := os.ReadFile("testdata/expected.json")
	if err != nil {
		t.Fatal(err)
	}
	var s Snapshot
	if err := json.Unmarshal(body, &s); err != nil {
		t.Fatal(err)
	}
	if err := Validate(s); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*Snapshot){
		func(s *Snapshot) { s.Observations = s.Observations[:9] },
		func(s *Snapshot) { s.Observations[1] = s.Observations[0] },
		func(s *Snapshot) { s.Observations[1].Value = "0.561459216202" },
		func(s *Snapshot) { s.Observations[1].Value = "NaN" },
		func(s *Snapshot) { s.Observations[1].RawValue = "0" },
		func(s *Snapshot) { s.Observations[1].SubjectCode = "ZZ" },
	} {
		var bad Snapshot
		json.Unmarshal(body, &bad)
		mutate(&bad)
		if Validate(bad) == nil {
			t.Fatal("invalid snapshot accepted")
		}
	}
	if Method(s.Observations[0]) != "implied_mature" || Method(s.Observations[1]) != "rating" {
		t.Fatal("methods conflated")
	}
}
