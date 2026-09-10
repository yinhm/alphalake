package damodaran

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/yinhm/alphalake/internal/source/reference"
)

const CompanyIndustryDataset = "shse-szse-company-industries-v1"
const CompanyIndustryURL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/indname.xls"
const CompanyIndustryParserVersion = "damodaran-company-industries-v1"
const CompanyIndustryScript = "valuation/backend/data_sources/damodaran_parsers/company_industry_parser.py"

type CompanyIndustry struct {
	Name          string          `json:"name"`
	Ticker        string          `json:"ticker"`
	Industry      string          `json:"industry"`
	Sector        string          `json:"sector"`
	SICCode       json.RawMessage `json:"sic_code"`
	Country       string          `json:"country"`
	BroadGroup    string          `json:"broad_group"`
	SubGroup      string          `json:"sub_group"`
	SourceLocator string          `json:"source_locator"`
}

type CompanyIndustrySnapshot struct {
	Contract      string `json:"contract"`
	SHA256        string `json:"sha256"`
	ParserVersion string `json:"parser_version"`
	Runtime       string `json:"runtime"`
	// 与带来源日期的统计工作簿不同，本名单没有可验证的日期。
	SourceObservationDate *string           `json:"source_observation_date"`
	TotalRows             int               `json:"total_rows"`
	UnidentifiedRows      int               `json:"unidentified_rows"`
	OutsideScopeRows      int               `json:"outside_scope_rows"`
	Industries            []string          `json:"industries"`
	Companies             []CompanyIndustry `json:"companies"`
}

func ParseCompanyIndustries(ctx context.Context, python, script, path string) (CompanyIndustrySnapshot, string, error) {
	var s CompanyIndustrySnapshot
	hash, err := reference.Parse(ctx, python, script, path, nil, &s)
	if err != nil {
		return s, "", err
	}
	return s, hash, ValidateCompanyIndustries(s)
}

func ValidateCompanyIndustries(s CompanyIndustrySnapshot) error {
	if s.Contract != "alphalake-company-industries-v1" || s.ParserVersion != CompanyIndustryParserVersion || s.Runtime == "" || !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(s.SHA256) || s.SourceObservationDate != nil {
		return errors.New("invalid/dated company industry header")
	}
	if s.TotalRows != 48156 || s.UnidentifiedRows != 12 || s.OutsideScopeRows != 43056 || len(s.Companies) != 5100 || len(s.Industries) != 94 {
		return errors.New("unsupported audited company scope")
	}
	names := map[string]bool{}
	for _, name := range strings.Split(strings.TrimSpace(betaIndustries), "\n") {
		names[name] = true
	}
	seen := map[string]bool{}
	for _, name := range s.Industries {
		if !names[name] || seen[name] {
			return errors.New("unsupported/duplicate company industry")
		}
		seen[name] = true
	}
	tickers, rows := map[string]bool{}, map[int]bool{}
	ticker := regexp.MustCompile(`^(SHSE|SZSE):[0-9]{6}$`)
	locator := regexp.MustCompile(`^By company name!A([0-9]+):H([0-9]+)$`)
	for _, c := range s.Companies {
		location := locator.FindStringSubmatch(c.SourceLocator)
		if !ticker.MatchString(c.Ticker) || tickers[c.Ticker] || !names[c.Industry] || len(location) != 3 || location[1] != location[2] || strings.TrimSpace(c.Name) == "" || strings.TrimSpace(c.Country) == "" || strings.TrimSpace(c.Sector) == "" {
			return fmt.Errorf("invalid company identity/industry: %s", c.Ticker)
		}
		var sic string
		if (len(c.SICCode) == 0 || c.SICCode[0] != '"' || json.Unmarshal(c.SICCode, &sic) != nil) && string(c.SICCode) != "0" && string(c.SICCode) != "0.0" {
			return errors.New("unsupported raw SIC code")
		}
		row, _ := strconv.Atoi(location[1])
		if row < 2 || row > 48157 || rows[row] {
			return errors.New("invalid company row")
		}
		tickers[c.Ticker], rows[row] = true, true
	}
	return nil
}

// CompanyIndustryDigest 锁定已解析内容；不是独立的行业语义核验。
func CompanyIndustryDigest(s CompanyIndustrySnapshot) string {
	raw, _ := json.Marshal(s)
	return fmt.Sprintf("%x", sha256.Sum256(raw))
}
