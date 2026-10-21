package macro

import (
	"fmt"
	"html"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const officialScope = "官方公布口径；修订按发布版本保留，不用现价金额重算可比口径增速；中国大陆全国范围，不代表单家公司。"

func (b *builder) cell(row []Cell, index int) Cell {
	if index >= len(row) {
		b.err = fmt.Errorf("incomplete macro data row")
		return Cell{}
	}
	return row[index]
}
func (b *builder) require(codes ...string) error {
	for _, code := range codes {
		if _, ok := b.series["cn_"+code]; !ok {
			return fmt.Errorf("required macro series missing: %s", code)
		}
	}
	return b.err
}
func metaContent(source, key string) (string, error) {
	pattern := regexp.MustCompile(`(?i)<meta\s+name=["']` + key + `["']\s+content=["']([^"']*)["']`)
	values := pattern.FindAllStringSubmatch(source, -1)
	if len(values) != 1 {
		return "", fmt.Errorf("missing/ambiguous NBS %s", key)
	}
	return html.UnescapeString(values[0][1]), nil
}

func parseNBS(b *builder, d Document, key string) error {
	title, err := metaContent(d.HTML, "ArticleTitle")
	if err != nil {
		return err
	}
	b.s.Title = title
	f, _ := FindFeed(key)
	if !regexp.MustCompile(f.TitlePattern).MatchString(title) {
		return fmt.Errorf("NBS publication identity mismatch")
	}
	stamp, err := metaContent(d.HTML, "PubDate")
	if err != nil {
		return err
	}
	published, err := time.ParseInLocation("2006/01/02 15:04", stamp, time.FixedZone("Asia/Shanghai", 8*3600))
	if err != nil {
		return err
	}
	utc := published.UTC()
	b.s.PublishedAt = &utc
	b.s.PublicationPrecision = "timestamp"
	yearMatch := regexp.MustCompile(`^(20[0-9]{2})年`).FindStringSubmatch(title)
	reportingMonth := 0
	if yearMatch == nil {
		// Use the explicit statistical appendix, never the publication year as the reporting year.
		period := regexp.MustCompile(`附表(20[0-9]{2})年(?:1[—-])?([0-9]{1,2})月份`).FindStringSubmatch(d.Text)
		if key != "economy" || period == nil {
			return fmt.Errorf("NBS reporting year missing")
		}
		yearMatch = []string{"", period[1]}
		reportingMonth, _ = strconv.Atoi(period[2])
	}
	year, _ := strconv.Atoi(yearMatch[1])
	month := 0
	if match := regexp.MustCompile(`(?:1[—-])?([0-9]{1,2})月`).FindStringSubmatch(title); match != nil {
		month, _ = strconv.Atoi(match[1])
	}
	if month == 0 {
		month = reportingMonth
	}
	if month == 0 {
		for i, word := range []string{"一季度", "二季度", "三季度", "四季度"} {
			if strings.Contains(title, word) {
				month = (i + 1) * 3
			}
		}
		if strings.Contains(title, "上半年") {
			month = 6
		}
	}
	if key == "household" && regexp.MustCompile(`^20[0-9]{2}年居民收入和消费支出情况$`).MatchString(title) {
		month = 12
	}
	if key == "economy" {
		if month == 0 {
			if strings.Contains(title, "前三季度") {
				month = 9
			} else if strings.Contains(title, "全年") {
				month = 12
			} else if strings.Contains(title, "一季度") {
				month = 3
			} else if strings.Contains(title, "上半年") {
				month = 6
			}
		}
	}
	if month == 0 {
		return fmt.Errorf("NBS reporting period not identified")
	}
	if month < 1 || month > 12 || (key == "gdp" || key == "capacity" || key == "household") && month%3 != 0 {
		return fmt.Errorf("invalid NBS monthly/quarterly period")
	}
	switch key {
	case "gdp":
		return nbsGDP(b, d, year, month)
	case "pmi":
		return nbsPMI(b, d)
	case "cpi", "ppi":
		return nbsPrices(b, d, key, year, month)
	case "industrial", "retail":
		return nbsActivity(b, d, key, year, month)
	case "investment":
		return nbsInvestment(b, d, year, month)
	case "property":
		return nbsProperty(b, d, year, month)
	case "capacity":
		return nbsCapacity(b, d, year, month)
	case "household":
		return nbsHousehold(b, d, year, month)
	case "economy":
		return nbsEconomy(b, d, year, month)
	case "profits":
		return nbsProfits(b, d, year, month)
	}
	return fmt.Errorf("unreviewed NBS release")
}

func nbsGDP(b *builder, d Document, year, month int) error {
	labels := map[string][2]string{"GDP": {"gdp", "国内生产总值"}, "第一产业": {"gdp_primary", "第一产业增加值"}, "第二产业": {"gdp_secondary", "第二产业增加值"}, "第三产业": {"gdp_tertiary", "第三产业增加值"}}
	for ti, t := range d.Tables {
		for _, row := range t {
			label, ok := labels[rowLabel(row)]
			if !ok {
				continue
			}
			if !headerContains(t, "绝对额（亿元）", "比上年同期增长（%）") {
				return fmt.Errorf("GDP nominal/real header changed")
			}
			if err := quarterHeaders(t, month, 1, 2); err != nil {
				return err
			}
			for i, c := range []struct{ suffix, name, basis, unit, price string }{{"_nominal", "现价金额", "quarter", "CNY_100_million", "current_prices"}, {"_nominal_ytd", "现价累计金额", "ytd", "CNY_100_million", "current_prices"}, {"_real_yoy", "实际同比", "quarter", "percent", "constant_prices"}, {"_real_ytd_yoy", "实际累计同比", "ytd", "percent", "constant_prices"}} {
				b.add(label[0]+c.suffix, label[1]+c.name, c.basis, c.unit, c.price, "provider_unspecified", officialScope, year, month, b.cell(row, i+1))
			}
		}
		if !headerContains(t, "年份", "1季度", "4季度") {
			continue
		}
		lead := d.Leads[ti]
		yoy, qoq := strings.LastIndex(lead, "GDP同比增长速度"), strings.LastIndex(lead, "GDP环比增长速度")
		code, name, adjust := "gdp_real_yoy", "国内生产总值实际同比", "provider_unspecified"
		if qoq > yoy {
			code, name, adjust = "gdp_real_qoq_sa", "国内生产总值实际季调环比", "seasonally_adjusted"
		} else if yoy < 0 {
			return fmt.Errorf("GDP growth table caption missing")
		}
		for _, row := range t {
			y, e := strconv.Atoi(rowLabel(row))
			if e != nil {
				continue
			}
			for q := 1; q <= 4; q++ {
				b.add(code, name, "quarter", "percent", "constant_prices", adjust, officialScope, y, q*3, b.cell(row, q))
			}
		}
	}
	return b.require("gdp_nominal", "gdp_real_yoy", "gdp_real_qoq_sa")
}

func nbsPrices(b *builder, d Document, key string, year, month int) error {
	labels := map[string][2]string{}
	if key == "cpi" {
		labels = map[string][2]string{"居民消费价格": {"cpi", "居民消费价格"}, "其中：食品": {"cpi_food", "食品消费价格"}, "非食品": {"cpi_nonfood", "非食品消费价格"}, "服务": {"cpi_services", "服务消费价格"}}
	} else {
		labels = map[string][2]string{"一、工业生产者出厂价格": {"ppi", "工业生产者出厂价格"}, "二、工业生产者购进价格": {"ppi_input", "工业生产者购进价格"}, "生产资料": {"ppi_production_materials", "生产资料出厂价格"}, "原材料": {"ppi_raw_materials", "原材料出厂价格"}}
	}
	for _, t := range d.Tables {
		for _, row := range t {
			v, ok := labels[rowLabel(row)]
			if !ok {
				continue
			}
			if !headerContains(t, "环比涨跌幅（%）", "同比涨跌幅（%）") {
				return fmt.Errorf("price percentage header changed")
			}
			for i, p := range []struct{ suffix, label, basis string }{{"_mom", "环比", "month"}, {"_yoy", "同比", "month"}, {"_ytd_yoy", "累计平均同比", "ytd"}} {
				b.add(v[0]+p.suffix, v[1]+p.label, p.basis, "percent", "price_change", "provider_unspecified", officialScope, year, month, b.cell(row, i+1))
			}
		}
	}
	if key == "cpi" { // Core CPI is explicitly reported in the prose; do not infer it by subtracting baskets.
		pattern := regexp.MustCompile(`扣除食品和能源价格的核心CPI同比(上涨|下降|持平)([0-9]+(?:\.[0-9]+)?)?%?`)
		for _, m := range pattern.FindAllStringSubmatch(d.Text, -1) {
			raw := m[2]
			if m[1] == "持平" {
				raw = "0"
			}
			if m[1] == "下降" {
				raw = "-" + raw
			}
			b.add("cpi_core_yoy", "核心居民消费价格同比", "month", "percent", "price_change", "provider_unspecified", officialScope, year, month, Cell{raw, "prose/core_cpi_yoy"})
		}
		return b.require("cpi_yoy", "cpi_mom")
	}
	return b.require("ppi_yoy", "ppi_input_yoy")
}

func nbsPMI(b *builder, d Document) error {
	for _, t := range d.Tables {
		var keys, names []string
		switch {
		case headerContains(t, "PMI", "生产", "新订单"):
			keys = []string{"manufacturing", "production", "new_orders", "raw_inventory", "employment", "supplier_delivery"}
			names = []string{"制造业PMI", "制造业生产指数", "制造业新订单指数", "制造业原材料库存指数", "制造业从业人员指数", "制造业供应商配送时间指数"}
		case headerContains(t, "新出口订单", "进口"):
			keys = []string{"export_orders", "imports", "purchases", "input_price", "output_price", "finished_inventory", "backlog", "expectations"}
			names = []string{"制造业新出口订单指数", "制造业进口指数", "制造业采购量指数", "制造业原材料购进价格指数", "制造业出厂价格指数", "制造业产成品库存指数", "制造业在手订单指数", "制造业生产经营预期指数"}
		case headerContains(t, "商务活动", "投入品价格"):
			keys = []string{"nonmanufacturing", "nonmanufacturing_orders", "nonmanufacturing_input_price", "nonmanufacturing_output_price", "nonmanufacturing_employment", "nonmanufacturing_expectations"}
			names = []string{"非制造业商务活动指数", "非制造业新订单指数", "非制造业投入品价格指数", "非制造业销售价格指数", "非制造业从业人员指数", "非制造业业务活动预期指数"}
		case headerContains(t, "新出口订单", "供应商配送时间"):
			keys = []string{"nonmanufacturing_export_orders", "nonmanufacturing_backlog", "nonmanufacturing_inventory", "nonmanufacturing_supplier_delivery"}
			names = []string{"非制造业新出口订单指数", "非制造业在手订单指数", "非制造业存货指数", "非制造业供应商配送时间指数"}
		default:
			continue
		}
		if !headerContains(t, "单位：%") {
			return fmt.Errorf("PMI diffusion index scale header changed")
		}
		for _, row := range t {
			m := regexp.MustCompile(`^(20[0-9]{2})年([0-9]{1,2})月$`).FindStringSubmatch(rowLabel(row))
			if m == nil {
				continue
			}
			year, _ := strconv.Atoi(m[1])
			month, _ := strconv.Atoi(m[2])
			for i, key := range keys {
				b.add("pmi_"+key, names[i], "month", "diffusion_index_percent_scale", "not_applicable", "provider_unspecified", "扩散指数，以50为较上月变化的分界，单位指数点；不是增长率或概率，配送时间有不同经济方向。", year, month, b.cell(row, i+1))
			}
		}
	}
	return b.require("pmi_manufacturing", "pmi_new_orders", "pmi_nonmanufacturing", "pmi_input_price")
}

func nbsActivity(b *builder, d Document, key string, year, month int) error {
	labels := map[string][2]string{"规模以上工业增加值": {"industrial_value_added", "规模以上工业增加值"}, "采矿业": {"industrial_mining", "采矿业增加值"}, "制造业": {"industrial_manufacturing", "制造业增加值"}, "其中：高技术制造业": {"industrial_hightech", "高技术制造业增加值"}, "电力、热力、燃气及水生产和供应业": {"industrial_utilities", "公用事业工业增加值"}}
	if key == "retail" {
		labels = map[string][2]string{"社会消费品零售总额": {"retail_sales", "社会消费品零售总额"}, "餐饮收入": {"catering_sales", "餐饮收入"}}
	}
	for _, t := range d.Tables {
		for _, row := range t {
			v, ok := labels[rowLabel(row)]
			if !ok {
				continue
			}
			if !headerContains(t, "同比增长（%）", "绝对量") {
				return fmt.Errorf("activity amount/growth header changed")
			}
			if key == "retail" && !headerContains(t, "绝对量（亿元）") {
				return fmt.Errorf("retail currency unit changed")
			}
			for _, item := range []struct {
				suffix, basis string
				offset        int
			}{{"", "month_or_jan_feb", 0}, {"_ytd", "ytd", 2}} {
				if month == 2 && len(row) == 3 {
					item.offset = 0
				}
				if key == "retail" {
					b.add(v[0]+item.suffix, v[1]+"金额", item.basis, "CNY_100_million", "current_prices", "provider_unspecified", officialScope, year, month, b.cell(row, 1+item.offset))
				}
				prices := "constant_prices"
				if key == "retail" {
					prices = "current_prices"
				}
				b.add(v[0]+item.suffix+"_yoy", v[1]+"同比", item.basis, "percent", prices, "provider_unspecified", officialScope, year, month, b.cell(row, 2+item.offset))
			}
		}
		if headerContains(t, "环比增速（%）") {
			nbsMonthlySA(b, t, map[string]string{"industrial": "industrial_value_added_mom_sa", "retail": "retail_sales_mom_sa"}[key], map[string]string{"industrial": "规模以上工业增加值季调环比", "retail": "社会消费品零售总额季调环比"}[key])
		}
	}
	if key == "retail" {
		return b.require("retail_sales_ytd", "retail_sales_ytd_yoy", "retail_sales_mom_sa")
	}
	return b.require("industrial_value_added_yoy", "industrial_value_added_ytd_yoy", "industrial_value_added_mom_sa")
}

func nbsMonthlySA(b *builder, t Table, code, name string) {
	year := 0
	for _, row := range t {
		if len(row) < 3 {
			continue
		}
		y := regexp.MustCompile(`^(20[0-9]{2})年$`).FindStringSubmatch(row[0].Text)
		if y == nil {
			continue
		}
		year, _ = strconv.Atoi(y[1])
		m := regexp.MustCompile(`^([0-9]{1,2})月$`).FindStringSubmatch(row[1].Text)
		if m == nil {
			continue
		}
		month, _ := strconv.Atoi(m[1])
		prices := "current_prices"
		if strings.HasPrefix(code, "industrial_") {
			prices = "constant_prices"
		}
		b.add(code, name, "month", "percent", prices, "seasonally_adjusted", officialScope, year, month, b.cell(row, 2))
	}
}

func nbsInvestment(b *builder, d Document, year, month int) error {
	labels := map[string][2]string{"固定资产投资（不含农户）": {"fixed_asset_investment_ytd_yoy", "固定资产投资累计同比"}, "其中：民间投资": {"private_investment_ytd_yoy", "民间投资累计同比"}, "其中：国有控股": {"state_investment_ytd_yoy", "国有控股投资累计同比"}, "设备工器具购置": {"equipment_investment_ytd_yoy", "设备工器具购置投资累计同比"}, "其中：制造业": {"manufacturing_investment_ytd_yoy", "制造业投资累计同比"}}
	for _, t := range d.Tables {
		for _, row := range t {
			v, ok := labels[rowLabel(row)]
			if !ok {
				continue
			}
			if !headerContains(t, "同比增长（%）") {
				return fmt.Errorf("investment rate header changed")
			}
			b.add(v[0], v[1], "ytd", "percent", "current_prices", "provider_unspecified", officialScope+"投资统计有可比口径调整，禁止用不同版本金额直接重算同比。", year, month, b.cell(row, 1))
		}
		if headerContains(t, "环比增速（%）") {
			nbsMonthlySA(b, t, "fixed_asset_investment_mom_sa", "固定资产投资季调环比")
		}
	}
	return b.require("fixed_asset_investment_ytd_yoy", "fixed_asset_investment_mom_sa")
}

func nbsProperty(b *builder, d Document, year, month int) error {
	labels := map[string]struct{ code, name, unit, basis string }{
		"房地产开发投资（亿元）":       {"property_investment", "房地产开发投资", "CNY_100_million", "ytd"},
		"房屋新开工面积（万平方米）":     {"property_new_starts", "房屋新开工面积", "square_metre_10000", "ytd"},
		"房屋竣工面积（万平方米）":      {"property_completions", "房屋竣工面积", "square_metre_10000", "ytd"},
		"新建商品房销售面积（万平方米）":   {"property_sales_area", "新建商品房销售面积", "square_metre_10000", "ytd"},
		"新建商品房销售额（亿元）":      {"property_sales", "新建商品房销售金额", "CNY_100_million", "ytd"},
		"商品房待售面积（万平方米）":     {"property_unsold_area", "商品房待售面积", "square_metre_10000", "point_in_time"},
		"房地产开发企业本年到位资金（亿元）": {"property_developer_funding", "房地产开发企业到位资金", "CNY_100_million", "ytd"},
	}
	for _, t := range d.Tables {
		for _, row := range t {
			v, ok := labels[rowLabel(row)]
			if !ok {
				continue
			}
			if !headerContains(t, "绝对量", "同比增长（%）") {
				return fmt.Errorf("property table header changed")
			}
			prices := "current_prices"
			if v.unit == "square_metre_10000" {
				prices = "not_applicable"
			}
			b.add(v.code, v.name, v.basis, v.unit, prices, "provider_unspecified", officialScope, year, month, b.cell(row, 1))
			b.add(v.code+"_yoy", v.name+"同比", v.basis, "percent", prices, "provider_unspecified", officialScope, year, month, b.cell(row, 2))
		}
	}
	return b.require("property_investment", "property_sales", "property_sales_area", "property_unsold_area")
}

func nbsCapacity(b *builder, d Document, year, month int) error {
	labels := map[string][2]string{"工业": {"capacity_industrial", "工业"}, "其中：采矿业": {"capacity_mining", "采矿业"}, "制造业": {"capacity_manufacturing", "制造业"}, "电力、热力、燃气及水生产和供应业": {"capacity_utilities", "公用事业"}}
	for _, t := range d.Tables {
		for _, row := range t {
			v, ok := labels[rowLabel(row)]
			if !ok {
				continue
			}
			if !headerContains(t, "产能利用率（%）", "百分点") {
				return fmt.Errorf("capacity ratio/difference header changed")
			}
			if err := quarterHeaders(t, month, 1, 3); err != nil {
				return err
			}
			for _, part := range []struct {
				suffix, basis string
				offset        int
			}{{"", "quarter", 0}, {"_ytd", "ytd", 2}} {
				b.add(v[0]+part.suffix, v[1]+"产能利用率", part.basis, "percent", "not_applicable", "provider_unspecified", officialScope, year, month, b.cell(row, 1+part.offset))
				b.add(v[0]+part.suffix+"_yoy_pp", v[1]+"产能利用率同比增减", part.basis, "percentage_points", "not_applicable", "provider_unspecified", "百分点差，不是同比增长率。"+officialScope, year, month, b.cell(row, 2+part.offset))
			}
		}
	}
	return b.require("capacity_industrial", "capacity_manufacturing")
}

func quarterHeaders(t Table, month, quarterColumn, ytdColumn int) error {
	quarter := []string{"一季度", "二季度", "三季度", "四季度"}[month/3-1]
	cumulative := map[int]string{3: "一季度", 6: "上半年", 9: "前三季度", 12: "全年"}[month]
	for _, row := range t[:min(3, len(t))] {
		if len(row) > ytdColumn && row[quarterColumn].Text == quarter && row[ytdColumn].Text == cumulative {
			return nil
		}
	}
	return fmt.Errorf("quarter/cumulative macro column order changed")
}

func nbsHousehold(b *builder, d Document, year, month int) error {
	labels := map[string][2]string{"全国居民人均可支配收入": {"household_disposable_income", "全国居民人均可支配收入"}, "全国居民人均消费支出": {"household_consumption", "全国居民人均消费支出"}}
	for _, t := range d.Tables {
		for _, row := range t {
			v, ok := labels[regexp.MustCompile(`^[（(][一二三四五六七八九十]+[）)]`).ReplaceAllString(rowLabel(row), "")]
			if !ok {
				continue
			}
			if !headerContains(t, "绝对量（元）", "实际增速") {
				return fmt.Errorf("household nominal/real header changed")
			}
			b.add(v[0], v[1], "ytd", "CNY_per_person", "current_prices", "provider_unspecified", officialScope, year, month, b.cell(row, 1))
			c := b.cell(row, 2)
			m := regexp.MustCompile(`^(-?[0-9]+(?:\.[0-9]+)?)[（(](-?[0-9]+(?:\.[0-9]+)?)[）)]$`).FindStringSubmatch(c.Text)
			if m == nil {
				return fmt.Errorf("household nominal/real rate pair changed")
			}
			b.add(v[0]+"_nominal_yoy", v[1]+"名义同比", "ytd", "percent", "current_prices", "provider_unspecified", officialScope, year, month, Cell{m[1], c.Locator + "/nominal"})
			b.add(v[0]+"_real_yoy", v[1]+"实际同比", "ytd", "percent", "constant_prices", "provider_unspecified", officialScope, year, month, Cell{m[2], c.Locator + "/real"})
		}
	}
	return b.require("household_disposable_income", "household_consumption")
}

func nbsEconomy(b *builder, d Document, year, month int) error {
	pattern := regexp.MustCompile(strconv.Itoa(month) + `月份?，全国城镇调查失业率为([0-9]+(?:\.[0-9]+)?)%`)
	for _, m := range pattern.FindAllStringSubmatch(d.Text, -1) {
		b.add("urban_surveyed_unemployment", "全国城镇调查失业率", "month", "percent", "not_applicable", "provider_unspecified", officialScope+"调查失业率不同于登记失业率和青年失业率。", year, month, Cell{m[1], "prose/monthly_urban_surveyed_unemployment"})
	}
	for _, t := range d.Tables {
		for _, row := range t {
			if strings.Contains(rowLabel(row), "城镇调查失业率") && strings.Contains(rowLabel(row), "%") && len(row) >= 5 {
				b.add("urban_surveyed_unemployment", "全国城镇调查失业率", "month", "percent", "not_applicable", "provider_unspecified", officialScope+"调查失业率不同于登记失业率和青年失业率。", year, month, b.cell(row, 1))
			}
		}
	}
	trade := regexp.MustCompile(`(?:前三季度|上半年|一季度|全年|1[—-]` + strconv.Itoa(month) + `月份?)，货物进出口总额([0-9.]+)亿元，同比(增长|下降)([0-9.]+)%。其中，出口([0-9.]+)亿元，(增长|下降)([0-9.]+)%[；;]进口([0-9.]+)亿元，(增长|下降)([0-9.]+)%`)
	for _, m := range trade.FindAllStringSubmatch(d.Text, -1) {
		for i, v := range []struct{ code, name string }{{"goods_trade_ytd", "货物进出口总额"}, {"goods_exports_ytd", "货物出口额"}, {"goods_imports_ytd", "货物进口额"}} {
			offset := i*3 + 1
			b.add(v.code, v.name, "ytd", "CNY_100_million", "current_prices", "provider_unspecified", "国家统计局经济运行稿转述的货物贸易统计，原统计机构为海关；不是国际收支货物贸易口径。", year, month, Cell{m[offset], "prose/ytd_goods_trade/" + v.code})
			growth := m[offset+2]
			if m[offset+1] == "下降" {
				growth = "-" + growth
			}
			b.add(v.code+"_yoy", v.name+"同比", "ytd", "percent", "current_prices", "provider_unspecified", officialScope, year, month, Cell{growth, "prose/ytd_goods_trade/" + v.code + "_yoy"})
		}
	}
	return b.require("urban_surveyed_unemployment")
}

func nbsProfits(b *builder, d Document, year, month int) error {
	// The national release explicitly gives these three cumulative levels in yuan units.
	for _, v := range []struct{ code, name, pattern string }{
		{"industrial_profit_ytd", "规模以上工业企业利润总额", `规模以上工业企业实现利润总额([0-9]+(?:\.[0-9]+)?)亿元`},
		{"industrial_revenue_ytd", "规模以上工业企业营业收入", `规模以上工业企业实现营业收入([0-9]+(?:\.[0-9]+)?)万亿元`},
		{"industrial_cost_ytd", "规模以上工业企业营业成本", `发生营业成本([0-9]+(?:\.[0-9]+)?)万亿元`},
	} {
		for _, m := range regexp.MustCompile(v.pattern).FindAllStringSubmatch(d.Text, -1) {
			unit := "CNY_trillion"
			if v.code == "industrial_profit_ytd" {
				unit = "CNY_100_million"
			}
			b.add(v.code, v.name, "ytd", unit, "current_prices", "provider_unspecified", officialScope, year, month, Cell{m[1], "prose/" + v.code})
		}
	}
	return b.require("industrial_profit_ytd", "industrial_revenue_ytd", "industrial_cost_ytd")
}
