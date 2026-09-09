package damodaran

import (
	"encoding/json"
	"os"
	"testing"
)

func TestGlobalBetaContract(t *testing.T) {
	body, err := os.ReadFile("testdata/beta-expected.json")
	if err != nil {
		t.Fatal(err)
	}
	var s BetaSnapshot
	if err := json.Unmarshal(body, &s); err != nil {
		t.Fatal(err)
	}
	if err := ValidateBeta(s); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*BetaSnapshot){
		func(s *BetaSnapshot) { s.Observations = s.Observations[:372] },
		func(s *BetaSnapshot) { s.Observations[0].Industry = "invented" },
		func(s *BetaSnapshot) { s.Observations[1] = s.Observations[0] },
		func(s *BetaSnapshot) { s.Observations[0].SampleCount = 0 },
		func(s *BetaSnapshot) { s.Observations[0].Value = "1.000000000000" },
		func(s *BetaSnapshot) { s.Observations[0].SourceLocator = "Industry Averages!H11" },
	} {
		var bad BetaSnapshot
		json.Unmarshal(body, &bad)
		mutate(&bad)
		if ValidateBeta(bad) == nil {
			t.Fatal("invalid beta accepted")
		}
	}
}
