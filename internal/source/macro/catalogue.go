package macro

import (
	"fmt"
	"net/url"
	"strings"
)

type Feed struct{ Key, Source, Name, TitlePattern string }

func (f Feed) Dataset() string { return "macro_" + f.Key + "_v1" }
func Feeds() []Feed {
	return []Feed{
		{"gdp", "nbs", "国内生产总值", `国内生产总值初步核算结果`},
		{"cpi", "nbs", "居民消费价格", `居民消费价格`},
		{"ppi", "nbs", "工业生产者价格", `工业生产者出厂价格`},
		{"pmi", "nbs", "采购经理指数", `中国采购经理指数运行情况`},
		{"industrial", "nbs", "规模以上工业增加值", `规模以上工业增加值`},
		{"investment", "nbs", "固定资产投资", `全国固定资产投资`},
		{"property", "nbs", "房地产开发和销售", `全国房地产(?:市场|开发投资和销售)`},
		{"retail", "nbs", "社会消费品零售", `社会消费品零售总额`},
		{"capacity", "nbs", "工业产能利用率", `全国规模以上工业产能利用率`},
		{"household", "nbs", "居民收入和消费", `居民收入和消费支出情况`},
		{"economy", "nbs", "经济运行与就业外贸", `经济运行.*(?:平稳|向好|成效)`},
		{"profits", "nbs", "工业企业利润", `全国规模以上工业企业利润`},
		{"money", "pbc", "货币供应量", ""},
		{"afre_flow", "pbc", "社会融资规模增量", ""},
		{"afre_stock", "pbc", "社会融资规模存量", ""},
		{"financial_report", "pbc", "金融统计数据报告", ""},
		{"fiscal", "mof", "财政收支", ""},
	}
}
func FindFeed(key string) (Feed, error) {
	for _, f := range Feeds() {
		if f.Key == key {
			return f, nil
		}
	}
	return Feed{}, fmt.Errorf("unsupported macro dataset %q", key)
}

func ValidateURL(source, raw string) error {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return fmt.Errorf("invalid official macro URL")
	}
	if source == "nbs" && u.Host == "www.stats.gov.cn" && strings.HasPrefix(u.Path, "/sj/") {
		return nil
	}
	if source == "pbc" && u.Host == "www.pbc.gov.cn" && strings.HasPrefix(u.Path, "/diaochatongjisi/") {
		return nil
	}
	if source == "mof" && u.Host == "gks.mof.gov.cn" && strings.HasPrefix(u.Path, "/tongjishuju/") {
		return nil
	}
	return fmt.Errorf("unreviewed macro host/path")
}
