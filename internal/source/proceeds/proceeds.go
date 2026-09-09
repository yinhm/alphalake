package proceeds

import (
	"context"
	"errors"
	"github.com/yinhm/alphalake/internal/source/reference"
)

const Source = "issuer_disclosure"
const Dataset = "reviewed-equity-proceeds-v1"
const ParserVersion = "reviewed-proceeds-v1"
const Script = "internal/source/proceeds/parse.py"

func URL(event string) string {
	switch event {
	case "ipo":
		return "https://static.cninfo.com.cn/finalpage/2026-07-02/1225406962.PDF"
	case "greenshoe":
		return "https://disc.static.szse.cn/download/disc/disk03/finalpage/2026-07-27/10d2e333-09f5-4b03-a51d-0f043257edff.PDF"
	}
	return ""
}

type Snapshot struct {
	reference.Header
	URL          string `json:"url"`
	Code         string `json:"code"`
	Event        string `json:"event"`
	Shares       string `json:"shares"`
	Currency     string `json:"currency"`
	NetProceeds  string `json:"net_proceeds"`
	AmountStatus string `json:"amount_status"`
	DateStatus   string `json:"date_status"`
	Locator      string `json:"locator"`
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
	if err := s.Header.Validate("alphalake-equity-proceeds-v1", ParserVersion); err != nil {
		return err
	}
	day, shares, net := "2026-07-02", "46632800", "4523000000"
	status := "reported_listing_date_not_cash_settlement"
	if s.Event == "greenshoe" {
		day, shares, net = "2026-07-29", "3443300", "337860000"
		status = "expected_listing_date_not_cash_settlement"
	}
	if URL(s.Event) == "" || s.URL != URL(s.Event) || s.Code != "300866" || s.ObservationDate != day || s.Shares != shares || s.NetProceeds != net || s.Currency != "HKD" || s.AmountStatus != "issuer_estimate_after_estimated_costs" || s.DateStatus != status || s.Locator == "" {
		return errors.New("unreviewed equity proceeds scope/units/status")
	}
	return nil
}
