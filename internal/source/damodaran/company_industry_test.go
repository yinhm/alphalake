package damodaran

import (
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"testing"
)

func TestCompanyIndustryRealArchiveParser(t *testing.T) {
	python := os.Getenv("ALPHALAKE_TEST_PYTHON")
	if python == "" {
		t.Skip("set ALPHALAKE_TEST_PYTHON to run archived xlrd parser; mandatory in CI")
	}
	f, err := os.Open("testdata/indname.xls.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	z, err := gzip.NewReader(f)
	if err != nil {
		t.Fatal(err)
	}
	defer z.Close()
	raw, err := io.ReadAll(io.LimitReader(z, 64<<20))
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(raw)
	var source struct {
		URL       string `json:"url"`
		SHA       string `json:"sha256"`
		FetchedAt string `json:"fetched_at"`
	}
	metadata, err := os.ReadFile("testdata/indname.source.json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(metadata, &source); err != nil {
		t.Fatal(err)
	}
	if hex.EncodeToString(sum[:]) != source.SHA || source.URL != CompanyIndustryURL || source.FetchedAt == "" {
		t.Fatal("raw source identity changed")
	}
	path := filepath.Join(t.TempDir(), "indname.xls")
	if err = os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	s, parserHash, err := ParseCompanyIndustries(t.Context(), python, filepath.Join("..", "..", "..", CompanyIndustryScript), path)
	if err != nil {
		t.Fatal(err)
	}
	if s.SHA256 != source.SHA || len(parserHash) != 64 {
		t.Fatal("parser/raw lineage lost")
	}
	want := map[string]string{"SZSE:300866": "Computers/Peripherals", "SHSE:600519": "Beverage (Alcoholic)", "SHSE:603288": "Food Processing", "SZSE:002959": "Furn/Home Furnishings", "SZSE:300124": "Machinery"}
	for _, c := range s.Companies {
		if industry, ok := want[c.Ticker]; ok {
			if c.Industry != industry {
				t.Fatal(c)
			}
			delete(want, c.Ticker)
		}
		if c.Ticker == "SZSE:000553" && c.Country != "Israel" {
			t.Fatal("source country rewritten", c)
		}
		if c.Ticker == "SHSE:601390" && c.Country != "Hong Kong" {
			t.Fatal("source country rewritten", c)
		}
	}
	if len(want) != 0 {
		t.Fatal("missing source companies", want)
	}
	baseline, err := json.Marshal(s)
	if err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*CompanyIndustrySnapshot){
		func(s *CompanyIndustrySnapshot) { s.Companies = s.Companies[:5099] },
		func(s *CompanyIndustrySnapshot) { s.Companies[1] = s.Companies[0] },
		func(s *CompanyIndustrySnapshot) { s.Companies[0].Ticker = "BSE:920992" },
		func(s *CompanyIndustrySnapshot) { s.Companies[0].Industry = "invented" },
		func(s *CompanyIndustrySnapshot) { s.Companies[0].SourceLocator = "By company name!A2:H3" },
		func(s *CompanyIndustrySnapshot) { s.Industries[1] = s.Industries[0] },
		func(s *CompanyIndustrySnapshot) { date := "2026-01-05"; s.SourceObservationDate = &date },
	} {
		var bad CompanyIndustrySnapshot
		if err = json.Unmarshal(baseline, &bad); err != nil {
			t.Fatal(err)
		}
		mutate(&bad)
		if ValidateCompanyIndustries(bad) == nil {
			t.Fatal("invalid company snapshot accepted")
		}
	}
}
