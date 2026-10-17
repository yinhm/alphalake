package eastmoney

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math/big"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

const Source = "eastmoney"
const GuidanceVersion = "eastmoney-earnings-guidance-v3"
const GuidanceReport = "RPT_PUBLIC_OP_NEWPREDICT"

type Row struct {
	Security    string             `json:"SECUCODE"`
	Code        string             `json:"SECURITY_CODE"`
	Name        string             `json:"SECURITY_NAME_ABBR"`
	Notice      string             `json:"NOTICE_DATE"`
	Report      string             `json:"REPORT_DATE"`
	MetricCode  string             `json:"PREDICT_FINANCE_CODE"`
	MetricLabel string             `json:"PREDICT_FINANCE"`
	Lower       json.RawMessage    `json:"PREDICT_AMT_LOWER"`
	Upper       json.RawMessage    `json:"PREDICT_AMT_UPPER"`
	GrowthLower json.RawMessage    `json:"ADD_AMP_LOWER"`
	GrowthUpper json.RawMessage    `json:"ADD_AMP_UPPER"`
	Prior       json.RawMessage    `json:"PREYEAR_SAME_PERIOD"`
	Content     string             `json:"PREDICT_CONTENT"`
	Reason      string             `json:"CHANGE_REASON_EXPLAIN"`
	Type        string             `json:"PREDICT_TYPE"`
	Latest      string             `json:"IS_LATEST"`
	Ref         domain.GuidanceRef `json:"-"`
}

type Page struct {
	Count int
	Pages int
	Rows  []Row
}

type Issue struct {
	Subject string `json:"subject"`
	Reason  string `json:"reason"`
}

func ParsePage(raw []byte) (Page, error) {
	var envelope struct {
		Success *bool  `json:"success"`
		Message string `json:"message"`
		Result  *struct {
			Count *int   `json:"count"`
			Pages int    `json:"pages"`
			Data  *[]Row `json:"data"`
		} `json:"result"`
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	if err := decoder.Decode(&envelope); err != nil {
		return Page{}, fmt.Errorf("decode Eastmoney guidance: %w", err)
	}
	if envelope.Success == nil || !*envelope.Success {
		return Page{}, fmt.Errorf("Eastmoney guidance business failure: %s", envelope.Message)
	}
	if envelope.Result == nil || envelope.Result.Count == nil || envelope.Result.Data == nil || *envelope.Result.Count < 0 {
		return Page{}, fmt.Errorf("Eastmoney guidance missing result/count/data")
	}
	return Page{Count: *envelope.Result.Count, Pages: envelope.Result.Pages, Rows: *envelope.Result.Data}, nil
}

var numberPattern = regexp.MustCompile(`^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$`)

// Decimal refuses precision loss rather than rounding provider evidence.
func Decimal(raw json.RawMessage) (*string, error) {
	s := strings.TrimSpace(string(raw))
	if s == "" || s == "null" {
		return nil, nil
	}
	if strings.HasPrefix(s, `"`) {
		if err := json.Unmarshal(raw, &s); err != nil {
			return nil, err
		}
	}
	if len(s) > 80 || !numberPattern.MatchString(s) {
		return nil, fmt.Errorf("invalid decimal %q", s)
	}
	// Bound exponent before big.Rat allocates arbitrary powers of ten.
	if p := strings.IndexAny(s, "eE"); p >= 0 {
		exponent, ok := new(big.Int).SetString(strings.TrimPrefix(s[p+1:], "+"), 10)
		if !ok || !exponent.IsInt64() || exponent.Int64() < -38 || exponent.Int64() > 38 {
			return nil, fmt.Errorf("decimal exponent out of bounds")
		}
	}
	r, ok := new(big.Rat).SetString(s)
	if !ok {
		return nil, fmt.Errorf("invalid decimal %q", s)
	}
	value := r.FloatString(10)
	rounded, _ := new(big.Rat).SetString(value)
	if rounded.Cmp(r) != 0 || len(strings.TrimLeft(strings.Split(value, ".")[0], "-+")) > 28 {
		return nil, fmt.Errorf("decimal exceeds DECIMAL(38,10): %s", s)
	}
	value = strings.TrimRight(strings.TrimRight(value, "0"), ".")
	if value == "-0" {
		value = "0"
	}
	return &value, nil
}

func Normalize(rows []Row, expectedPeriod string) ([]domain.EarningsGuidance, []Issue, error) {
	groups := map[string]*domain.EarningsGuidance{}
	var issues []Issue
	for _, row := range rows {
		if len(row.Report) < 10 || row.Report[:10] != expectedPeriod {
			return nil, nil, fmt.Errorf("Eastmoney response period %q differs from requested %s", row.Report, expectedPeriod)
		}
		mic, ticker := "", ""
		parts := strings.Split(row.Security, ".")
		if len(parts) == 2 && regexp.MustCompile(`^[0-9]{6}$`).MatchString(parts[0]) && parts[0] == row.Code {
			switch parts[1] {
			case "SH":
				mic, ticker = "XSHG", "SHSE:"+row.Code
			case "SZ":
				mic, ticker = "XSHE", "SZSE:"+row.Code
			case "BJ":
				mic, ticker = "XBSE", "BSE:"+row.Code
			}
		}
		date, precision := "", "unknown"
		if len(row.Notice) >= 10 {
			if _, err := time.Parse("2006-01-02", row.Notice[:10]); err == nil {
				date, precision = row.Notice[:10], "date"
			}
		}
		start, basis := domain.GuidanceOperatingPeriod(row.Content, row.Report)
		key := strings.Join([]string{row.Security, row.Code, expectedPeriod, date, "forecast", start, basis}, "|")
		g := groups[key]
		if g == nil {
			g = &domain.EarningsGuidance{Source: Source, EventKey: key, NormalizationVersion: GuidanceVersion, Ticker: ticker, ProviderCode: row.Code, ExchangeMIC: mic, Name: row.Name, Kind: "forecast", PeriodStart: start, PeriodEnd: expectedPeriod, PeriodBasis: basis, AnnouncementDate: date, AnnouncementPrecision: precision, PredictType: row.Type, Metrics: map[string]domain.GuidanceMetric{}, SemanticStatus: "valid", Verification: "unlinked", IdentityStatus: "pending"}
			groups[key] = g
		}
		g.SourceRefs = append(g.SourceRefs, row.Ref)
		if g.PredictType != row.Type {
			g.PredictType = "mixed_metric_types"
		}
		g.Contents = append(g.Contents, row.Content)
		g.Reasons = append(g.Reasons, row.Reason)
		g.LatestHint = g.LatestHint || row.Latest == "T"
		metricName, unit := "", "CNY"
		switch {
		case row.MetricCode == "004" && row.MetricLabel == "归属于上市公司股东的净利润":
			metricName = "forecast_parent_net_income"
		case row.MetricCode == "005" && row.MetricLabel == "扣除非经常性损益后的净利润":
			metricName = "forecast_adjusted_net_income"
		case row.MetricCode == "003" && row.MetricLabel == "每股收益":
			metricName, unit = "forecast_eps", "CNY/share"
		case row.MetricCode == "006" && row.MetricLabel == "营业收入":
			metricName = "forecast_revenue"
		}
		if metricName == "" {
			issues = append(issues, Issue{key, "unreviewed metric: " + row.MetricCode + "/" + row.MetricLabel})
			g.SemanticStatus = "partial"
			continue
		}
		m := domain.GuidanceMetric{PredictType: row.Type, Unit: unit, Representation: "missing", Status: "valid"}
		m.Scope = "unspecified"
		if metricName == "forecast_parent_net_income" {
			m.Scope = "parent_owners"
		}
		numbers := []json.RawMessage{row.Lower, row.Upper, row.GrowthLower, row.GrowthUpper, row.Prior}
		destinations := []**string{&m.Lower, &m.Upper, &m.GrowthLower, &m.GrowthUpper, &m.Prior}
		for i, n := range numbers {
			value, err := Decimal(n)
			if err != nil {
				m.Status = "unresolved"
				issues = append(issues, Issue{key, metricName + ": " + err.Error()})
			} else {
				*destinations[i] = value
			}
		}
		switch {
		case m.Lower != nil && m.Upper != nil:
			m.Representation = "interval"
		case m.Lower != nil:
			m.Representation = "lower_bound"
		case m.Upper != nil:
			m.Representation = "upper_bound"
		case row.Content != "":
			m.Representation = "qualitative"
		}
		for _, bounds := range [][2]*string{{m.Lower, m.Upper}, {m.GrowthLower, m.GrowthUpper}} {
			if bounds[0] != nil && bounds[1] != nil {
				lo, _ := new(big.Rat).SetString(*bounds[0])
				hi, _ := new(big.Rat).SetString(*bounds[1])
				if lo.Cmp(hi) > 0 {
					m.Status = "unresolved"
					issues = append(issues, Issue{key, metricName + ": reversed interval retained for review"})
				}
			}
		}
		if previous, exists := g.Metrics[metricName]; exists && previous != m {
			// Pointer identity is irrelevant; compare canonical decimal contents.
			a, _ := json.Marshal(previous)
			b, _ := json.Marshal(m)
			if !bytes.Equal(a, b) {
				m = domain.GuidanceMetric{Unit: unit, Representation: "missing", Status: "conflicting"}
				issues = append(issues, Issue{key, metricName + ": conflicting duplicate source rows"})
			}
		}
		g.Metrics[metricName] = m
		if m.Status != "valid" {
			g.SemanticStatus = "partial"
		}
	}
	keys := make([]string, 0, len(groups))
	for key := range groups {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	out := make([]domain.EarningsGuidance, 0, len(keys))
	for _, key := range keys {
		g := groups[key]
		g.Contents, g.Reasons = uniqueSorted(g.Contents), uniqueSorted(g.Reasons)
		if g.PeriodBasis == "unknown" || g.ExchangeMIC == "" || len(g.Metrics) == 0 {
			g.SemanticStatus = "unresolved"
			issues = append(issues, Issue{key, "unresolved period/identity/metric"})
		}
		if err := g.Seal(); err != nil {
			return nil, nil, err
		}
		out = append(out, *g)
	}
	return out, issues, nil
}

func uniqueSorted(items []string) []string {
	set := map[string]bool{}
	for _, s := range items {
		if s != "" {
			set[s] = true
		}
	}
	items = make([]string, 0, len(set))
	for s := range set {
		items = append(items, s)
	}
	sort.Strings(items)
	return items
}
