package macro

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
)

func fiscalPeriod(title string) (int, int, error) {
	m := regexp.MustCompile(`^(20[0-9]{2})年(.*)财政收支情况$`).FindStringSubmatch(title)
	if m == nil {
		return 0, 0, fmt.Errorf("fiscal reporting title missing")
	}
	year, _ := strconv.Atoi(m[1])
	month := 0
	if p := regexp.MustCompile(`^(?:1[—-])?([0-9]{1,2})月$`).FindStringSubmatch(m[2]); p != nil {
		month, _ = strconv.Atoi(p[1])
	}
	switch m[2] {
	case "":
		month = 12
	case "上半年":
		month = 6
	case "一季度":
		month = 3
	case "前三季度":
		month = 9
	}
	if month < 1 || month > 12 {
		return 0, 0, fmt.Errorf("unreviewed fiscal report period")
	}
	return year, month, nil
}
func parseFiscal(b *builder, d Document) error {
	title, err := metaContent(d.HTML, "ArticleTitle")
	if err != nil {
		return err
	}
	year, month, err := fiscalPeriod(title)
	if err != nil {
		return err
	}
	b.s.Title = title
	stamp, err := metaContent(d.HTML, "PubDate")
	if err != nil {
		return err
	}
	published, err := time.ParseInLocation("2006-01-02 15:04:05", stamp, time.FixedZone("Asia/Shanghai", 8*3600))
	if err != nil {
		return err
	}
	utc := published.UTC()
	b.s.PublishedAt = &utc
	b.s.PublicationPrecision = "timestamp"
	for _, v := range []struct{ code, label string }{
		{"fiscal_general_revenue_ytd", "全国一般公共预算收入"}, {"fiscal_general_expenditure_ytd", "全国一般公共预算支出"},
		{"fiscal_tax_revenue_ytd", "全国税收收入"}, {"fiscal_nontax_revenue_ytd", "非税收入"},
		{"fiscal_fund_revenue_ytd", "全国政府性基金预算收入"}, {"fiscal_fund_expenditure_ytd", "全国政府性基金预算支出"},
	} {
		pattern := regexp.MustCompile(regexp.QuoteMeta(v.label) + `([0-9]+(?:\.[0-9]+)?)亿元，同比(增长|下降)([0-9]+(?:\.[0-9]+)?)%`)
		for _, m := range pattern.FindAllStringSubmatch(strings.ReplaceAll(d.Text, "&nbsp;", ""), -1) {
			definition := "全国累计财政收支，政府性基金与一般公共预算分别统计；不将收入减支出直接命名官方财政赤字，不与中央本级数据混用。"
			b.add(v.code, v.label, "ytd", "CNY_100_million", "current_prices", "provider_unspecified", definition, year, month, Cell{m[1], "prose/" + v.code})
			growth := m[3]
			if m[2] == "下降" {
				growth = "-" + growth
			}
			b.add(v.code+"_yoy", v.label+"官方同比", "ytd", "percent", "current_prices", "provider_unspecified", definition, year, month, Cell{growth, "prose/" + v.code + "_yoy"})
		}
	}
	return b.require("fiscal_general_revenue_ytd", "fiscal_general_expenditure_ytd", "fiscal_fund_revenue_ytd", "fiscal_fund_expenditure_ytd")
}
