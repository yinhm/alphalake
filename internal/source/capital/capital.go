package capital

import (
	"context"
	"errors"
	"github.com/yinhm/alphalake/internal/source/reference"
	"math/big"
)

const Source = "issuer_disclosure"
const Dataset = "reviewed-share-classes-v1"
const Script = "internal/source/capital/parse.py"
const ParserVersion = "reviewed-issuer-capital-v1"

func URL(code string) string {
	switch code {
	case "300866":
		return "https://disc.static.szse.cn/disc/disk03/finalpage/2026-09-04/2db3ed6a-0a7e-4f37-82eb-a03ec2045ec8.PDF"
	case "600519":
		return "https://static.cninfo.com.cn/finalpage/2026-08-15/1225475868.PDF"
	}
	return ""
}

type Class struct {
	Kind        string `json:"kind"`
	MIC         string `json:"mic"`
	Currency    string `json:"currency"`
	Symbol      string `json:"symbol"`
	Issued      string `json:"issued"`
	Treasury    string `json:"treasury"`
	Outstanding string `json:"outstanding"`
	Locator     string `json:"locator"`
}
type Snapshot struct {
	reference.Header
	Code      string  `json:"code"`
	LegalName string  `json:"legal_name"`
	URL       string  `json:"url"`
	Classes   []Class `json:"classes"`
}

func Parse(ctx context.Context, python, script, path string) (Snapshot, string, error) {
	var s Snapshot
	hash, err := reference.Parse(ctx, python, script, path, nil, &s)
	if err != nil {
		return s, "", err
	}
	return s, hash, Validate(s)
}
func Validate(s Snapshot) error {
	if err := s.Header.Validate("alphalake-share-classes-v1", ParserVersion); err != nil {
		return err
	}
	if URL(s.Code) == "" || URL(s.Code) != s.URL {
		return errors.New("unreviewed capital source")
	}
	want := []Class{{Kind: "A", MIC: "XSHG", Currency: "CNY", Symbol: "sh600519"}}
	day, name := "2026-06-30", "贵州茅台酒股份有限公司"
	if s.Code == "300866" {
		want = []Class{{Kind: "H", MIC: "XHKG", Currency: "HKD", Symbol: "00668"}, {Kind: "A", MIC: "XSHE", Currency: "CNY", Symbol: "sz300866"}}
		day, name = "2026-08-31", "安克创新科技股份有限公司"
	}
	if s.ObservationDate != day || s.LegalName != name || len(s.Classes) != len(want) {
		return errors.New("incomplete/unreviewed share scope")
	}
	for n, c := range s.Classes {
		w := want[n]
		if c.Kind != w.Kind || c.MIC != w.MIC || c.Currency != w.Currency || c.Symbol != w.Symbol || c.Locator == "" {
			return errors.New("share identity mismatch")
		}
		i, ok := new(big.Int).SetString(c.Issued, 10)
		t, tok := new(big.Int).SetString(c.Treasury, 10)
		o, ook := new(big.Int).SetString(c.Outstanding, 10)
		if !ok || !tok || !ook || i.Sign() <= 0 || t.Sign() < 0 || o.Sign() <= 0 || new(big.Int).Sub(i, t).Cmp(o) != 0 {
			return errors.New("invalid share count reconciliation")
		}
	}
	return nil
}
