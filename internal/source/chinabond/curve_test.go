package chinabond

import (
	"encoding/json"
	"os"
	"testing"
)

func TestCurveContract(t *testing.T) {
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
		func(s *Snapshot) { s.Observations = s.Observations[:7] },
		func(s *Snapshot) { s.Observations[6].Value = "1.681600000000" },
		func(s *Snapshot) { s.Observations[6].TenorMonths = 12 },
		func(s *Snapshot) { s.Observations[6].SourceLocator = "#gjqxData/tr[3]/td[8]" },
	} {
		var bad Snapshot
		json.Unmarshal(body, &bad)
		mutate(&bad)
		if Validate(bad) == nil {
			t.Fatal("invalid curve accepted")
		}
	}
	s.Observations[0].RawValue = "-0.0100"
	s.Observations[0].Value = "-0.000100000000"
	if err := Validate(s); err != nil {
		t.Fatal("negative rates must remain valid", err)
	}
}
