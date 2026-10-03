import type { ValuationResponse } from '../types/valuation';

type LiveValue = { label: string; value: string };
type Field = {
  label: string;
  read: (valuation: ValuationResponse) => number | null | undefined;
  format: 'percent' | 'multiple' | 'money' | 'per-share';
};

// Editorial bindings can only select these response fields. Never evaluate a
// database-supplied expression or derive another financial result in the UI.
const fields = {
  'cost_of_capital.wacc': { label: '本次初始 WACC', read: d => d.cost_of_capital?.wacc, format: 'percent' },
  'cost_of_capital.beta_u': { label: '本次无杠杆 Beta', read: d => d.cost_of_capital?.beta_u, format: 'multiple' },
  'cost_of_capital.beta_l': { label: '本次有杠杆 Beta', read: d => d.cost_of_capital?.beta_l, format: 'multiple' },
  'cost_of_capital.risk_free_rate': { label: '本次无风险利率', read: d => d.cost_of_capital?.risk_free_rate, format: 'percent' },
  'cost_of_capital.equity_risk_premium': { label: '本次采用的股权风险溢价', read: d => d.cost_of_capital?.equity_risk_premium, format: 'percent' },
  'cost_of_capital.cost_of_equity': { label: '本次股权成本', read: d => d.cost_of_capital?.cost_of_equity, format: 'percent' },
  'cost_of_capital.cost_of_debt_pretax': { label: '本次税前债务成本', read: d => d.cost_of_capital?.cost_of_debt_pretax, format: 'percent' },
  'cost_of_capital.cost_of_debt_aftertax': { label: '本次税后债务成本', read: d => d.cost_of_capital?.cost_of_debt_aftertax, format: 'percent' },
  'cost_of_capital.interest_coverage_ratio': { label: '本次利息保障倍数', read: d => d.cost_of_capital?.interest_coverage_ratio, format: 'multiple' },
  'cost_of_capital.d_e_ratio': { label: '本次债务／股权比', read: d => d.cost_of_capital?.d_e_ratio, format: 'multiple' },
  'cost_of_capital.weight_equity': { label: '本次股权权重', read: d => d.cost_of_capital?.weight_equity, format: 'percent' },
  'cost_of_capital.weight_debt': { label: '本次债务权重', read: d => d.cost_of_capital?.weight_debt, format: 'percent' },
  'cost_of_capital.mv_debt_total': { label: '本次债务总市值', read: d => d.cost_of_capital?.mv_debt_total, format: 'money' },
  'cashflow.fcff': { label: '基年公司自由现金流', read: d => d.cashflow?.fcff, format: 'money' },
  'cashflow.roic': { label: '基年投入资本回报率', read: d => d.cashflow?.roic, format: 'percent' },
  'cashflow.reinvestment_firm': { label: '基年公司再投资', read: d => d.cashflow?.reinvestment_firm, format: 'money' },
  'cashflow.rir_firm': { label: '基年公司再投资率', read: d => d.cashflow?.rir_firm, format: 'percent' },
  'cashflow.adjusted_invested_capital': { label: '基年调整后投入资本', read: d => d.cashflow?.adjusted_invested_capital, format: 'money' },
  'dcf.terminal_value_firm': { label: '本次终值（未折现）', read: d => d.dcf?.terminal_value_firm, format: 'money' },
  'dcf.pv_terminal_value': { label: '本次终值现值', read: d => d.dcf?.pv_terminal_value, format: 'money' },
  'dcf.value_of_operating_assets': { label: '本次经营资产价值', read: d => d.dcf?.value_of_operating_assets, format: 'money' },
  'dcf.value_of_equity': { label: '本次股权价值（扣除期权前）', read: d => d.dcf?.value_of_equity, format: 'money' },
  'final.value_per_share': { label: '本次每股价值', read: d => d.final?.value_per_share, format: 'per-share' },
  'adjusted.value_of_research_asset': { label: '本次研发资产价值', read: d => d.adjusted?.value_of_research_asset, format: 'money' },
  'adjusted.pv_of_operating_leases': { label: '本次经营租赁现值', read: d => d.adjusted?.pv_of_operating_leases, format: 'money' },
  'inputs.macro_inputs.risk_free_rate': { label: '无风险利率输入', read: d => d.inputs.macro_inputs.risk_free_rate, format: 'percent' },
  'inputs.macro_inputs.equity_risk_premium': { label: '股权风险溢价输入', read: d => d.inputs.macro_inputs.equity_risk_premium, format: 'percent' },
  'inputs.macro_inputs.country_risk_premium': { label: '国家风险溢价输入', read: d => d.inputs.macro_inputs.country_risk_premium, format: 'percent' },
  'inputs.macro_inputs.tax_rate_effective': { label: '有效税率输入', read: d => d.inputs.macro_inputs.tax_rate_effective, format: 'percent' },
  'inputs.macro_inputs.tax_rate_marginal': { label: '边际税率输入', read: d => d.inputs.macro_inputs.tax_rate_marginal, format: 'percent' },
  'inputs.valuation_assumptions.stable_growth_rate': { label: '稳定增长率输入', read: d => d.inputs.valuation_assumptions.stable_growth_rate, format: 'percent' },
  'inputs.valuation_assumptions.growth_perpetuity_rate': { label: '永续增长率覆盖输入', read: d => d.inputs.valuation_assumptions.growth_perpetuity_rate, format: 'percent' },
  'inputs.valuation_assumptions.revenue_growth_next_year': { label: '下一年收入增长率假设', read: d => d.inputs.valuation_assumptions.revenue_growth_next_year, format: 'percent' },
  'inputs.valuation_assumptions.revenue_growth_years_2_5': { label: '第 2–5 年收入增长率假设', read: d => d.inputs.valuation_assumptions.revenue_growth_years_2_5, format: 'percent' },
  'inputs.valuation_assumptions.operating_margin_next_year': { label: '下一年经营利润率假设', read: d => d.inputs.valuation_assumptions.operating_margin_next_year, format: 'percent' },
  'inputs.valuation_assumptions.target_operating_margin': { label: '目标经营利润率假设', read: d => d.inputs.valuation_assumptions.target_operating_margin, format: 'percent' },
  'inputs.valuation_assumptions.sales_to_capital_high': { label: '高增长期销售／资本倍率假设', read: d => d.inputs.valuation_assumptions.sales_to_capital_high, format: 'multiple' },
  'inputs.valuation_assumptions.sales_to_capital_stable': { label: '稳定期销售／资本倍率假设', read: d => d.inputs.valuation_assumptions.sales_to_capital_stable, format: 'multiple' },
  'inputs.valuation_assumptions.cost_of_capital_stable_override': { label: '稳定期 WACC 覆盖输入', read: d => d.inputs.valuation_assumptions.cost_of_capital_stable_override, format: 'percent' },
  'inputs.valuation_assumptions.roic_stable_override': { label: '稳定期 ROIC 覆盖输入', read: d => d.inputs.valuation_assumptions.roic_stable_override, format: 'percent' },
  'inputs.valuation_assumptions.failure_probability': { label: '失败概率假设', read: d => d.inputs.valuation_assumptions.failure_probability, format: 'percent' },
} satisfies Record<string, Field>;

// Stable UI attachment points for future knowledge releases. This is an
// application contract, not glossary content; the SQLite release supplies the
// reviewed explanation and may select only the allowed response fields above.
export const KNOWLEDGE_BINDINGS = {
  'wacc.current': { termId: 'wacc', fieldPath: 'cost_of_capital.wacc' },
  'wacc.stable-override': { termId: 'wacc', fieldPath: 'inputs.valuation_assumptions.cost_of_capital_stable_override' },
  'beta.levered': { termId: 'beta', fieldPath: 'cost_of_capital.beta_l' },
  'beta.unlevered': { termId: 'beta', fieldPath: 'cost_of_capital.beta_u' },
  'risk-free-rate.current': { termId: 'risk-free-rate', fieldPath: 'cost_of_capital.risk_free_rate' },
  'risk-free-rate.input': { termId: 'risk-free-rate', fieldPath: 'inputs.macro_inputs.risk_free_rate' },
  'equity-risk-premium.current': { termId: 'equity-risk-premium', fieldPath: 'cost_of_capital.equity_risk_premium' },
  'equity-risk-premium.input': { termId: 'equity-risk-premium', fieldPath: 'inputs.macro_inputs.equity_risk_premium' },
  'country-risk-premium.input': { termId: 'country-risk-premium', fieldPath: 'inputs.macro_inputs.country_risk_premium' },
  'cost-of-equity.current': { termId: 'cost-of-equity', fieldPath: 'cost_of_capital.cost_of_equity' },
  'cost-of-debt.pretax': { termId: 'cost-of-debt', fieldPath: 'cost_of_capital.cost_of_debt_pretax' },
  'cost-of-debt.aftertax': { termId: 'cost-of-debt', fieldPath: 'cost_of_capital.cost_of_debt_aftertax' },
  'interest-coverage.current': { termId: 'interest-coverage', fieldPath: 'cost_of_capital.interest_coverage_ratio' },
  'capital-structure.debt-equity': { termId: 'capital-structure', fieldPath: 'cost_of_capital.d_e_ratio' },
  'capital-structure.debt-value': { termId: 'capital-structure', fieldPath: 'cost_of_capital.mv_debt_total' },
  'capital-structure.equity-weight': { termId: 'capital-structure', fieldPath: 'cost_of_capital.weight_equity' },
  'capital-structure.debt-weight': { termId: 'capital-structure', fieldPath: 'cost_of_capital.weight_debt' },
  'tax.marginal': { termId: 'marginal-tax-rate', fieldPath: 'inputs.macro_inputs.tax_rate_marginal' },
  'revenue-growth.next-year': { termId: 'revenue-growth', fieldPath: 'inputs.valuation_assumptions.revenue_growth_next_year' },
  'revenue-growth.high': { termId: 'revenue-growth', fieldPath: 'inputs.valuation_assumptions.revenue_growth_years_2_5' },
  'operating-margin.next-year': { termId: 'operating-margin', fieldPath: 'inputs.valuation_assumptions.operating_margin_next_year' },
  'operating-margin.target': { termId: 'operating-margin', fieldPath: 'inputs.valuation_assumptions.target_operating_margin' },
  'sales-to-capital.high': { termId: 'sales-to-capital', fieldPath: 'inputs.valuation_assumptions.sales_to_capital_high' },
  'sales-to-capital.stable': { termId: 'sales-to-capital', fieldPath: 'inputs.valuation_assumptions.sales_to_capital_stable' },
  'stable-growth.input': { termId: 'stable-growth', fieldPath: 'inputs.valuation_assumptions.stable_growth_rate' },
  'roic.stable-override': { termId: 'roic', fieldPath: 'inputs.valuation_assumptions.roic_stable_override' },
  'failure-probability.input': { termId: 'failure-probability', fieldPath: 'inputs.valuation_assumptions.failure_probability' },
  'terminal-value.current': { termId: 'terminal-value', fieldPath: 'dcf.terminal_value_firm' },
  'terminal-value.present': { termId: 'terminal-value', fieldPath: 'dcf.pv_terminal_value' },
  'operating-asset-value.current': { termId: 'operating-asset-value', fieldPath: 'dcf.value_of_operating_assets' },
  'equity-value.current': { termId: 'equity-value', fieldPath: 'dcf.value_of_equity' },
  'value-per-share.current': { termId: 'value-per-share', fieldPath: 'final.value_per_share' },
  'rd-capitalization.asset': { termId: 'rd-capitalization', fieldPath: 'adjusted.value_of_research_asset' },
  'lease-capitalization.present': { termId: 'lease-capitalization', fieldPath: 'adjusted.pv_of_operating_leases' },
} satisfies Record<string, { termId: string; fieldPath: keyof typeof fields }>;

export function resolveLiveValue(valuation: ValuationResponse | null, fieldPath: string): LiveValue | null {
  if (!valuation || !Object.hasOwn(fields, fieldPath)) return null;
  const field = fields[fieldPath as keyof typeof fields];
  const raw = field.read(valuation);
  if (raw == null || !Number.isFinite(raw)) return { label: field.label, value: '未提供' };
  if (field.format === 'percent') return { label: field.label, value: `${(raw * 100).toFixed(2)}%` };
  const value = raw.toLocaleString('zh-CN', { maximumFractionDigits: field.format === 'multiple' ? 4 : 2 });
  if (field.format === 'multiple') return { label: field.label, value };
  const currency = valuation.inputs.reporting_currency || '报告币种未提供';
  return { label: field.label, value: `${value} ${field.format === 'money' ? `百万 ${currency}` : `${currency}/股`}` };
}
