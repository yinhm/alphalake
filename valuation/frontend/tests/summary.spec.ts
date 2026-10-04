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
