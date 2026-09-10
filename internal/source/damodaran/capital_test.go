package damodaran

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"testing"
)

func TestCapitalRealSnapshot(t *testing.T) {
	raw, err := os.ReadFile("testdata/capexGlobal.xls")
	if err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile("testdata/capital-snapshot.json")
	if err != nil {
		t.Fatal(err)
	}
	var s CapitalSnapshot
	if err = json.Unmarshal(body, &s); err != nil {
		t.Fatal(err)
	}
	const hash = "2470cd4d62a330575132c1bf7d9ccc001a56dbd97bd374fa7ceec7930eb30955"
	if fmt.Sprintf("%x", sha256.Sum256(raw)) != hash || s.SHA256 != hash || s.ObservationDate != "2026-01-05" {
		t.Fatal("real capital evidence changed")
	}
	if err = ValidateCapital(s); err != nil {
		t.Fatal(err)
	}
	found := false
	for _, o := range s.Observations {
		if o.Industry == "Electronics (Consumer & Office)" {
			found = true
			if o.SampleCount != 122 || o.SourceLocator != "Industry Averages!J36" || o.Value != "1.905898248522" {
				t.Fatal(o)
			}
		}
	}
	if !found {
		t.Fatal("missing independently inspected source row")
	}
	for _, change := range []func(*CapitalSnapshot){
		func(v *CapitalSnapshot) { v.Observations = v.Observations[:93] },
		func(v *CapitalSnapshot) { v.Observations[0].SourceLocator = "Industry Averages!I9" },
		func(v *CapitalSnapshot) { v.Observations[0].Value = "3.000000000000" },
		func(v *CapitalSnapshot) { v.Observations[0].Industry = v.Observations[1].Industry },
		func(v *CapitalSnapshot) { v.Observations[0].MetricCode = "net_capex_to_sales" },
		func(v *CapitalSnapshot) { v.Observations[0].RawValue = "0"; v.Observations[0].Value = "0.000000000000" },
	} {
		var bad CapitalSnapshot
		if err = json.Unmarshal(body, &bad); err != nil {
			t.Fatal(err)
		}
		change(&bad)
		if ValidateCapital(bad) == nil {
			t.Fatal("bad capital evidence accepted")
		}
	}
}
