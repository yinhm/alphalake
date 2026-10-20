import { expect, test } from '@playwright/test';

test('summary displays resolved terminal WACC rather than reconstructing the input proxy', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/synthetic-summary', route => route.fulfill({ json: {
    inputs: {
      reporting_currency: 'CNY', stock_price_currency: 'CNY', fx_rate: 1,
      raw_financials: [{ fiscal_year: 2025, revenues: 1000, ebit: 200 }],
      valuation_assumptions: { projection_years: 10, cost_of_capital_stable_override: 0.08 },
      macro_inputs: { risk_free_rate: 0.04, equity_risk_premium: 0.04, tax_rate_marginal: 0.25 },
    },
    cost_of_capital: { wacc: 0.08 }, adjusted: { adjusted_ebit: 200 },
    dcf: {
      terminal_wacc: 0.1234,
      nopat_projections: Array(10).fill(150), tax_rate_projections: Array(10).fill(.25),
      revenue_projections: Array(10).fill(1000), ebit_projections: Array(10).fill(200),
      fcff_projections: Array(10).fill(150), reinvestment_projections: Array(10).fill(0),
      discount_factors: Array.from({ length: 10 }, (_, i) => 1 / 1.09 ** (i + 1)),
      pv_fcff: Array(10).fill(100),
    },
  } }));
  await page.goto('/tests/fixtures/summary.html');
  const row = page.getByRole('row').filter({ has: page.getByText('WACC', { exact: true }) });
  await expect(row.getByRole('cell').last()).toHaveText('12.34%');
  await expect(row.getByRole('cell').nth(2)).toHaveText('9.00%');
  expect(errors).toEqual([]);
});

test('LTM growth uses the backend aligned comparison and never substitutes stale annual fields', async ({ page }) => {
  const base = forecastFixture();
  const data = {
    ...base,
    inputs: { ...base.inputs, period_date_10k: '2025-12-31', period_date_10q: '2026-06-30',
      raw_financials: [{ fiscal_year:2025, revenues:30514.403328, ebit:2880.846592, bv_debt:70, d_a:1 },
                      { fiscal_year:2024, revenues:24710.080512, ebit:2000 }],
      company_metrics: { revenue_growth:.2264400293305613 as number | null,
        revenue_growth_basis:'TTM同比：2026-06-30 / 2025-06-30；不是未来预测。' },
      prepared_ttm: { period_start:'2025-07-01', period_end:'2026-06-30', provenance:{} },
    },
    ltm_financials: { fiscal_year:2025, revenues:34252.649472, ebit:3653.317376, bv_debt:null, d_a:null },
  };
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/synthetic-summary', route => route.fulfill({ json:data }));
  const row = (label: string) => page.getByRole('row').filter({ has: page.getByText(label, { exact:true }) });
  await page.goto('/tests/fixtures/summary.html?sheet=input');
  await expect(row('Revenue Growth (%)').getByRole('cell').nth(1)).toHaveText('22.64%');
  await expect(row('Revenue Growth (%)').getByRole('cell').nth(2)).toHaveText('23.49%');
  await expect(row('Revenue Growth').getByRole('cell').nth(1)).toHaveText('22.64%');
  await expect(row('Revenue Growth').getByRole('cell').nth(1)).toHaveAttribute('title', /2025-06-30/);
  await expect(row('Book Value of Debt').getByRole('cell').nth(1)).toHaveText('');
  await page.goto('/tests/fixtures/summary.html?sheet=diagnostics');
  await expect(row('Most recent revenue growth').getByRole('cell').nth(1)).toHaveText('22.64%');
  data.inputs.company_metrics.revenue_growth = null;
  await page.reload();
  await expect(row('Most recent revenue growth').getByRole('cell').nth(1)).toHaveText('');
  await page.goto('/tests/fixtures/summary.html?sheet=ltm');
  await expect(row('D&A').getByRole('cell').last()).toHaveText('');
  await expect(row('Book Value of Debt').getByRole('cell').nth(3)).toHaveText('');
  await page.goto('/tests/fixtures/summary.html?sheet=output');
  await expect(page.getByText('10-K-1', { exact:true })).toHaveCount(0);
  expect(errors).toEqual([]);
});

function forecastFixture(annual = false) {
  const parameters = Object.fromEntries([
    ['revenue_growth_next_year', .12], ['revenue_growth_years_2_5', .12],
    ['operating_margin_next_year', .2], ['target_operating_margin', .2],
    ['margin_convergence_year', 5], ['sales_to_capital_high', 2.5],
    ['sales_to_capital_stable', 2.5], ['growth_perpetuity_rate', .01], ['cost_of_capital_stable_override', .1234],
  ].map(([name, value]) => [name, { value, editable: !annual, source: annual ? 'annual_forecast' : 'inherited', basis: '合成条件，非公司预测。' }]));
  return {
    id: 'fixture', ticker: 'SYNTHETIC',
    inputs: {
      company_name: 'Synthetic', reporting_currency: 'CNY', stock_price_currency: 'CNY', fx_rate: 1,
      raw_financials: [{ fiscal_year: 2025, revenues: 1000, ebit: 100 }, { fiscal_year: 2024, revenues: 900, ebit: 90 }],
      valuation_assumptions: { projection_years: 2, high_growth_years: 1, margin_convergence_year: 5,
        cost_of_capital_stable_override: null, roic_stable_override: null,
        target_operating_margin: annual ? .9 : null, override_growth_perpetuity: false,
        ...(annual ? { annual_forecast: Array(2).fill({ growth: 0, margin: .2, tax: .15 }) } : {}) },
      macro_inputs: { risk_free_rate: .04, equity_risk_premium: .04, tax_rate_marginal: .25 },
      adjustment_inputs: {}, methodology_choices: {}, option_inputs: {},
      industry_data: { industry_name: 'Synthetic', region: 'TEST', pretax_operating_margin: .3 },
    },
    adjusted: { adjusted_ebit: 200 }, cost_of_capital: { wacc: .08 },
    cashflow: { historical_margin_by_year: [.15,.1] },
    warnings: [],
    dcf: {
      forecast_parameters: parameters, terminal_growth: .01, terminal_wacc: .1234, terminal_roic: .1234,
      terminal_revenue: 1010, terminal_ebit: 202, terminal_margin: .2, terminal_tax_rate: .15,
      terminal_nopat: 192, terminal_reinvestment: -3, terminal_fcff: 195,
      revenue_projections: [1000,1000], ebit_projections: [200,200], nopat_projections: [195,190],
      tax_rate_projections: [.15,.15], fcff_projections: [180,185], reinvestment_projections: [15,5],
      discount_factors: [1/1.09,1/1.09**2], pv_fcff: [100,100],
    },
  };
}

test('forecast cells use resolved values, preserve zero and clear, and lock superseded scalars', async ({ page }) => {
  let data = forecastFixture();
  await page.route('**/api/synthetic-summary', route => route.fulfill({ json: data }));
  await page.goto('/tests/fixtures/summary.html?sheet=input');
  const row = (label: string) => page.getByRole('row').filter({ has: page.getByText(label, { exact: true }) });
  const margin = row('Operating Margin — Next Year').getByRole('cell').nth(1);
  await expect(margin).toHaveText('20.00%');
  await expect(row('Operating Margin — Next Year').getByRole('cell').nth(2)).toHaveText('10.00%');
  await expect(row('Target Pre-tax Op Margin').getByRole('cell').nth(1)).toHaveText('20.00%');
  await expect(row('Revenue Growth — Transition Anchor').getByRole('cell').nth(1)).toHaveText('12.00%');
  await margin.dblclick();
  await margin.locator('input').fill('0');
  await margin.locator('input').press('Enter');
  await expect(page.getByTestId('patch')).toHaveText('{"valuation_assumptions.operating_margin_next_year":0}');
  await margin.dblclick();
  await margin.locator('input').fill('');
  await margin.locator('input').press('Enter');
  await expect(page.getByTestId('patch')).toHaveText('{"valuation_assumptions.operating_margin_next_year":null}');
  data = forecastFixture(true);
  await page.reload();
  await expect(margin).toHaveText('20.00%');
  await margin.dblclick();
  await expect(margin.locator('input')).toHaveCount(0);
});

test('terminal display reads annual, research and NOL outputs without a second calculation', async ({ page }) => {
  await page.route('**/api/synthetic-summary', route => route.fulfill({ json: forecastFixture(true) }));
  await page.goto('/tests/fixtures/summary.html');
  const expected = {
    'Revenue': '1,010', 'Revenue Growth %': '1.00%', 'Operating Margin %': '20.00%',
    'Operating Income (EBIT)': '202', 'Forecast Tax Rate %': '15.00%',
    'NOPAT (EBIT − Cash Tax)': '192', '− Reinvestment': '-3', 'FCFF': '195',
  };
  for (const [label, value] of Object.entries(expected)) {
    const row = page.getByRole('row').filter({ has: page.getByText(label, { exact: true }) });
    await expect(row.getByRole('cell').last()).toHaveText(value);
  }
  await expect(page.locator('thead').getByText('Terminal', { exact: true })).toHaveCount(1);
});

test('enabling terminal overrides starts at actual engine values rather than fixed percentages', async ({ page }) => {
  const data = forecastFixture();
  data.dcf.terminal_wacc = .1334;  // 最终含税盾调整；覆盖输入仍取正常基础值.1234
  data.dcf.terminal_roic = .1334;
  data.inputs.valuation_assumptions.cost_of_capital_stable_override = null;
  data.inputs.valuation_assumptions.roic_stable_override = null;
  await page.route('**/api/synthetic-summary', route => route.fulfill({ json:data }));
  await page.goto('/tests/fixtures/summary.html?sheet=input');
  const row = (text:string) => page.getByRole('row').filter({has:page.getByText(text,{exact:true})});
  await row('Do you want to override the stable cost of capital?').locator('select').selectOption('Yes');
  await expect(page.getByTestId('patch')).toHaveText('{"valuation_assumptions.cost_of_capital_stable_override":0.1234}');
  await row('Do you want to override the stable ROIC?').locator('select').selectOption('Yes');
  await expect(page.getByTestId('patch')).toHaveText('{"valuation_assumptions.roic_stable_override":0.1334}');
});

test('history tooltip and sensitivity show the same resolved margin', async ({ page }) => {
  await page.route('**/api/synthetic-summary', route => route.fulfill({ json: forecastFixture() }));
  await page.route('**/api/valuation/fixture/sensitivity', route => route.fulfill({ json: {
    baseline_vps: 10, currency: 'CNY', bars: [{ driver: 'target_operating_margin', label: 'Target margin',
      patch_path: 'valuation_assumptions.target_operating_margin', range_lo:.24, range_hi:.36,
      range_source:'industry_median_band', vps_lo:9, vps_hi:11, delta_lo:-1, delta_hi:1 }],
  } }));
  await page.goto('/tests/fixtures/summary.html?sheet=stories');
  await expect(page.getByRole('textbox', { name: 'Target margin value' })).toHaveValue('20.00%');
  await expect(page.getByText('band low', { exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Target margin value' }).locator('../..').getByText('Q1', { exact: true })).toHaveCount(0);
  const marginBlock = page.locator('section').filter({ has: page.getByText('Margin Story — pre-tax operating margin (EBIT / Revenue)', { exact: true }) });
  // The hover explanation must read the same adjusted annual value as the cell.
  await marginBlock.getByText('15.0%', { exact: true }).hover();
  await expect(marginBlock.getByText('15.0%', { exact: true }).locator('..')).toHaveAttribute('title', /adjusted operating margin = 15.00%/);
});

test('rejected parameter edit is visible and retains the prior forecast display', async ({ page }) => {
  const fixture = forecastFixture();
  await page.route('http://127.0.0.1:4173/api/**', route => {
    if (route.request().method() === 'PATCH') return route.fulfill({ status: 422, json: { detail: 'Convergence year must be positive' } });
    if (route.request().url().endsWith('/valuation')) return route.fulfill({ json: fixture });
    return route.fulfill({ json: { status: 'unavailable', terms: [], admin: false, configured: false } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Or try the demo data' }).click();
  const cell = page.getByRole('row').filter({ has: page.getByText('Year of Convergence (K)', { exact: true }) }).getByRole('cell').nth(1);
  await expect(cell).toHaveText('5');
  await cell.dblclick();
  await cell.locator('input').fill('0');
  await cell.locator('input').press('Enter');
  await expect(page.getByText(/参数未生效，保留上一次估值：Convergence year must be positive/)).toBeVisible();
  await expect(cell).toHaveText('5');
});

function auditFixture() {
  const data = forecastFixture() as any;
  data.inputs.valuation_assumptions.failure_probability = .2;
  data.inputs.valuation_assumptions.distress_proceeds_pct = .3;
  data.inputs.valuation_assumptions.failure_tie_to = 'B';
  data.inputs.option_inputs = { has_options: true, number_of_options: 2, average_strike_price: 8, average_maturity: 3, stock_price_std_dev: .3, dividend_yield: 0 };
  Object.assign(data.dcf, { value_as_going_concern: 2000, distress_value: 300, value_of_operating_assets: 1660,
    value_of_equity: 1530, equity_bridge_components: { debt: -200, preferred: -30, cash: 100 },
    discount_factors: [1/1.09, 1/1.09/1.11] });
  data.final = { call_value_per_option: 7, value_of_all_options: 14, value_per_share: .5,
    option_pricing: { adjusted_stock_price: 12.34, seed_value_per_share: 13.5, d1: .5, n_d1: .69, d2: .25, n_d2: .6, iterations: 4, residual: .001 } };
  data.multiples = { pe_ratio_market: 12, pe_ratio_intrinsic: 20 };
  return data;
}

test('failure percent edits preserve small percent points and display exactly one overlay', async ({ page }) => {
  await page.route('**/api/synthetic-summary', route => route.fulfill({ json: auditFixture() }));
  await page.goto('/tests/fixtures/summary.html?sheet=failure');
  const inputTable = page.getByText('Failure Probability Inputs', { exact: true }).locator('..');
  const cell = inputTable.getByRole('row').filter({ has: page.getByText('Probability of failure', { exact: true }) }).getByRole('cell').nth(1);
  for (const [text, expected] of [['0.5%', .005], ['1%', .01], ['0.5', .005], ['0%', 0]] as const) {
    await cell.dblclick(); await cell.locator('input').fill(text); await cell.locator('input').press('Enter');
    await expect(page.getByTestId('patch')).toHaveText(JSON.stringify({ 'valuation_assumptions.failure_probability': expected }));
  }
  const row = (name: string) => page.getByRole('row').filter({ has: page.getByText(name, { exact: true }) }).getByRole('cell').last();
  await expect(row('Value of operating assets (going concern)')).toHaveText('2,000');
  await expect(row('Distress sale proceeds')).toHaveText('300');
  await expect(row('Expected operating asset value')).toHaveText('1,660');
});

test('DCF detail has exact horizon, real discount path, NOPAT and complete terminal column', async ({ page }) => {
  await page.route('**/api/synthetic-summary', route => route.fulfill({ json: auditFixture() }));
  await page.goto('/tests/fixtures/summary.html?sheet=output');
  const table = page.getByText(/Projection Table \(/).locator('..');
  await expect(table.getByRole('columnheader')).toHaveCount(0); // Existing grid uses data cells for headers.
  const rows = table.getByRole('row');
  await expect(rows.first().getByRole('cell')).toHaveCount(5);
  const expected: Record<string,string[]> = {
    'Cost of capital': ['', '9.00%', '11.00%', '12.34%'],
    'NOPAT (after cash tax)': ['', '195', '190', '192'],
    'Reinvestment': ['', '15', '5', '-3'], 'FCFF': ['', '180', '185', '195'],
  };
  for (const [label, values] of Object.entries(expected)) {
    const cells = rows.filter({ has: page.getByText(label, { exact: true }) }).getByRole('cell');
    for (let i=0;i<values.length;i++) await expect(cells.nth(i+1)).toHaveText(values[i]);
  }
  await expect(page.getByRole('row').filter({ has: page.getByText('Equity bridge: preferred', {exact:true}) }).getByRole('cell').last()).toHaveText('-30');
});

test('option page uses recorded pricing state and ordinary fractional cells stay numeric', async ({ page }) => {
  await page.route('**/api/synthetic-summary', route => route.fulfill({ json: auditFixture() }));
  await page.goto('/tests/fixtures/summary.html?sheet=options');
  const row = (name: string) => page.getByRole('row').filter({ has: page.getByText(name, { exact:true }) }).getByRole('cell').last();
  await expect(row('Adjusted S (actual engine input)')).toHaveText('12.34');
  await expect(row('N(d1)')).toHaveText('0.69');
  await expect(row('Iterations')).toHaveText('4');
  await page.goto('/tests/fixtures/summary.html?sheet=cell');
  await expect(page.getByRole('cell')).toHaveText(['0.5','0.5','0.50%']);
});

test('stable multiples are not labelled multi-stage DCF and use backend market PE', async ({ page }) => {
  await page.route('**/api/synthetic-summary', route => route.fulfill({ json: auditFixture() }));
  await page.goto('/tests/fixtures/summary.html?sheet=relative');
  await expect(page.getByText('Intrinsic (DCF)', {exact:true})).toHaveCount(0);
  await expect(page.getByRole('row').filter({ has: page.getByText('PE Ratio', {exact:true}) }).getByRole('cell').nth(2)).toHaveText('12.00');
  await expect(page.getByText('Market vs Intrinsic', {exact:true})).toHaveCount(0);
});

test('sensitivity percentage editor uses the same percent-point contract', async ({ page }) => {
  await page.route('**/api/synthetic-summary', route => route.fulfill({ json: auditFixture() }));
  await page.route('**/api/valuation/fixture/sensitivity', route => route.fulfill({ json: { baseline_vps: .5, currency: 'CNY', bars: [
    { driver:'failure_probability', label:'Failure probability', patch_path:'valuation_assumptions.failure_probability', range_lo:0,range_hi:.3,vps_lo:1,vps_hi:.2,delta_lo:.5,delta_hi:-.3,range_source:'application_example' },
  ] } }));
  await page.goto('/tests/fixtures/summary.html?sheet=output');
  const editor = page.getByRole('textbox', {name:'Failure probability value'});
  await editor.fill('0.5%'); await editor.press('Enter');
  await expect(page.getByTestId('patch')).toHaveText('{"valuation_assumptions.failure_probability":0.005}');
});
