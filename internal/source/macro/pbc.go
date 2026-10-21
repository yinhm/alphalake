package macro

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var pbcMonthPattern = regexp.MustCompile(`^(20[0-9]{2})\.([0-9]{1,2})$`)
var afreComponents = []struct{ label, code, name string }{
	{"社会融资规模", "afre", "社会融资规模"}, {"人民币贷款", "afre_rmb_loans", "对实体经济发放的人民币贷款"},
	{"外币贷款", "afre_fx_loans", "对实体经济发放的外币贷款折人民币"}, {"委托贷款", "afre_entrusted_loans", "委托贷款"},
	{"信托贷款", "afre_trust_loans", "信托贷款"}, {"未贴现银行承兑汇票", "afre_undiscounted_acceptances", "未贴现银行承兑汇票"},
	{"企业债券", "afre_corporate_bonds", "企业债券融资"}, {"政府债券", "afre_government_bonds", "政府债券融资"},
	{"非金融企业境内股票", "afre_equity_financing", "非金融企业境内股票融资"},
	{"存款类金融机构资产支持证券", "afre_asset_backed_securities", "存款类金融机构资产支持证券"}, {"贷款核销", "afre_writeoffs", "贷款核销"},
}

func parsePBC(b *builder, d Document, key string) error {
	if key == "financial_report" {
		return pbcFinancialReport(b, d)
	}
	if !strings.Contains(d.Text, "单位：") || !strings.Contains(d.Text, "人民币") {
		return fmt.Errorf("PBC currency/unit missing")
	}
	if key == "money" {
		return pbcMoney(b, d)
	}
	if key == "afre_flow" {
		return pbcFlow(b, d)
	}
	if key == "afre_stock" {
		return pbcStock(b, d)
	}
	return fmt.Errorf("unreviewed PBC table")
}

type monthColumn struct{ index, year, month int }

func monthColumns(t Table) []monthColumn {
	for _, row := range t {
		columns := []monthColumn{}
		seen := map[string]bool{}
		for i, c := range row {
			m := pbcMonthPattern.FindStringSubmatch(c.Text)
			if m == nil || seen[c.Locator] {
				continue
			}
			year, _ := strconv.Atoi(m[1])
			month, _ := strconv.Atoi(m[2])
			columns = append(columns, monthColumn{i, year, month})
			seen[c.Locator] = true
		}
		if len(columns) >= 1 {
			return columns
		}
	}
	return nil
}
func pbcMoney(b *builder, d Document) error {
	if !strings.Contains(d.Text, "货币供应量") || !strings.Contains(d.Text, "单位：亿元人民币") || !strings.Contains(d.Text, "2025年1月") || !strings.Contains(d.Text, "个人活期存款") {
		return fmt.Errorf("money table identity/unit/M1 definition missing")
	}
	b.s.Title = "货币供应量（官方年度月表）"
	for _, t := range d.Tables {
		columns := monthColumns(t)
		if len(columns) == 0 {
			continue
		}
		for _, row := range t {
			label := rowLabel(row)
			m := regexp.MustCompile(`^(?:货币和准货币|货币|流通中货币)[（(]M([012])[）)]$`).FindStringSubmatch(label)
			if m == nil {
				continue
			}
			code, name := "money_m"+m[1]+"_stock", "货币供应量M"+m[1]+"余额"
			definition := officialScope + "月末存量，不累加，不从不同版本余额自行重算官方同比。"
			if m[1] == "1" {
				code = "money_m1_2025_stock"
				definition += "M1为自2025年1月启用的新定义，包含个人活期存款及非银行支付机构客户备付金，不与旧定义拼接。"
			}
			for _, column := range columns {
				b.add(code, name, "point_in_time", "CNY_100_million", "current_prices", "provider_unspecified", definition, column.year, column.month, b.cell(row, column.index))
			}
		}
	}
	return b.require("money_m0_stock", "money_m1_2025_stock", "money_m2_stock")
}
func pbcFlow(b *builder, d Document) error {
	if !strings.Contains(d.Text, "社会融资规模增量统计表") || !strings.Contains(d.Text, "单位：亿元人民币") {
		return fmt.Errorf("AFRE flow identity/unit changed")
	}
	b.s.Title = "社会融资规模增量统计表"
	for _, t := range d.Tables {
		columns := map[int]int{}
		for _, row := range t {
			for column, c := range row {
				for i, component := range afreComponents {
					if c.Text == component.label || i == 0 && c.Text == "社会融资规模增量" || strings.HasPrefix(c.Text, component.label+"（") || component.code == "afre_equity_financing" && c.Text == component.label+"融资" {
						columns[i] = column
					}
				}
			}
			if len(columns) == len(afreComponents) {
				break
			}
		}
		if len(columns) != len(afreComponents) {
			continue
		}
		for _, row := range t {
			m := pbcMonthPattern.FindStringSubmatch(rowLabel(row))
			if m == nil {
				continue
			}
			year, _ := strconv.Atoi(m[1])
			month, _ := strconv.Atoi(m[2])
			for i, v := range afreComponents {
				b.add(v.code+"_flow", v.name+"当月增量", "month", "CNY_100_million", "current_prices", "provider_unspecified", officialScope+"当月流量，不是累计新增或月末余额；可为负值，不机械加总分项替代总量。", year, month, b.cell(row, columns[i]))
			}
		}
	}
	return b.require("afre_flow", "afre_government_bonds_flow", "afre_rmb_loans_flow")
}
func pbcStock(b *builder, d Document) error {
	if !strings.Contains(d.Text, "社会融资规模存量统计表") || !strings.Contains(d.Text, "单位：万亿元人民币") {
		return fmt.Errorf("AFRE stock identity/unit changed")
	}
	b.s.Title = "社会融资规模存量统计表"
	for _, t := range d.Tables {
		columns := monthColumns(t)
		if len(columns) == 0 {
			continue
		}
		if !strings.Contains(d.Text, "增速（%）") {
			return fmt.Errorf("AFRE stock growth header missing")
		}
		for _, row := range t {
			label := rowLabel(row)
			for _, v := range afreComponents {
				if !strings.HasPrefix(label, v.label) || strings.Contains(label, "统计表") {
					continue
				}
				for _, column := range columns {
					b.add(v.code+"_stock", v.name+"余额", "point_in_time", "CNY_trillion", "current_prices", "provider_unspecified", officialScope+"社融存量分项，不等于全部金融机构贷款。", column.year, column.month, b.cell(row, column.index))
					b.add(v.code+"_stock_yoy", v.name+"余额官方同比", "point_in_time", "percent", "current_prices", "provider_unspecified", officialScope+"原表公布增速，不从两个存量版本自行重算。", column.year, column.month, b.cell(row, column.index+1))
				}
			}
		}
	}
	return b.require("afre_stock", "afre_stock_yoy", "afre_government_bonds_stock")
}

func pbcFinancialReport(b *builder, d Document) error {
	if m := regexp.MustCompile(`<span\s+id=["']shijian["'][^>]*>\s*(20[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})`).FindStringSubmatch(d.HTML); m != nil {
		published, err := time.ParseInLocation("2006-01-02 15:04:05", m[1], time.FixedZone("Asia/Shanghai", 8*3600))
		if err != nil {
			return err
		}
		utc := published.UTC()
		b.s.PublishedAt = &utc
		b.s.PublicationPrecision = "timestamp"
	}
	title := regexp.MustCompile(`20[0-9]{2}年[^<>\n]{0,35}金融统计数据报告`).FindString(d.Text)
	if title == "" {
		return fmt.Errorf("PBC financial report title missing")
	}
	b.s.Title = title
	year, _ := strconv.Atoi(title[:4])
	month := 0
	if m := regexp.MustCompile(`([0-9]{1,2})月`).FindStringSubmatch(title); m != nil {
		month, _ = strconv.Atoi(m[1])
	}
	for word, n := range map[string]int{"前三季度": 9, "上半年": 6, "一季度": 3} {
		if strings.Contains(title, word) {
			month = n
		}
	}
	if month == 0 && regexp.MustCompile(`^20[0-9]{2}年金融统计数据报告$`).MatchString(title) {
		month = 12
	}
	if month == 0 {
		return fmt.Errorf("PBC report period missing")
	}
	for _, v := range []struct{ code, name, pattern string }{
		{"money_m2_yoy", "广义货币M2官方同比", `广义货币[（(]M2[）)]余额[0-9.]+万亿元，同比(增长|下降)([0-9.]+)%`},
		{"money_m1_2025_yoy", "新口径M1官方同比", `狭义货币[（(]M1[）)]余额[0-9.]+万亿元，同比(增长|下降)([0-9.]+)%`},
		{"money_m0_yoy", "流通中货币M0官方同比", `流通中货币[（(]M0[）)]余额[0-9.]+万亿元，同比(增长|下降)([0-9.]+)%`},
		{"bank_rmb_loans_stock_yoy", "金融机构人民币贷款余额官方同比", `人民币贷款余额[0-9.]+万亿元，同比(增长|下降)([0-9.]+)%`},
	} {
		for _, m := range regexp.MustCompile(v.pattern).FindAllStringSubmatch(d.Text, -1) {
			raw := m[2]
			if m[1] == "下降" {
				raw = "-" + raw
			}
			b.add(v.code, v.name, "point_in_time", "percent", "current_prices", "provider_unspecified", officialScope+"官方报告公布同比；人民币贷款的金融统计范围与社融对实体经济贷款不同。", year, month, Cell{raw, "prose/" + v.code})
		}
	}
	return b.require("money_m2_yoy", "money_m1_2025_yoy", "bank_rmb_loans_stock_yoy")
}
