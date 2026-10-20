import type { ValuationResponse } from '../types/valuation';
import type { PatchValue } from '../api/client';
import SpreadsheetCell from '../components/SpreadsheetCell';
import SpreadsheetGrid from '../components/SpreadsheetGrid';
import ColorLegend from '../components/ColorLegend';
import SensitivityPanel from '../components/SensitivityPanel';
import TaxOverridePanel from '../components/TaxOverridePanel';
import { ciq, formula, backendField, user } from '../lib/sources';
import DualCurrency from '../components/DualCurrency';
import { fmtMoneyShort } from '../lib/currency';
import { baseYear, baseYearEbit } from '../lib/baseYear';
import { getField } from '../lib/baseYearVintage';
import VintageBadge from '../components/VintageBadge';

// ---------- helpers ----------

function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '';
  return (v * 100).toFixed(2) + '%';
}

function fmtNum(v: number | string | null | undefined): number | string {
  if (v === null || v === undefined) return '';
  return v;
}

// ---------- component ----------

interface Props {
  data: ValuationResponse;
  sessionId?: string | null;
  onPatch?: (path: string, value: PatchValue) => void | Promise<void>;
  onPatchMany?: (overrides: Record<string, PatchValue>) => void | Promise<void>;
}

export default function ValuationOutput({ data, onPatch, onPatchMany }: Props) {
  const dcf = data.dcf;
  const inputs = data.inputs;
  // LTM-rotated base year (falls back to raw_financials[0] when LTM absent).
  // Critical for non-calendar-year filers so the "Base year" column matches
  // what the engine's M3/M4 actually anchor on.
  const fin0 = baseYear(data) ?? inputs.raw_financials[0];
  const baseEbit = baseYearEbit(data) ?? 0;
  const assumptions = inputs.valuation_assumptions;
  const macro = inputs.macro_inputs;

  // 展示实际引擎基期；缺项不以另一期间替代。
  const revVintage = getField(data, 'revenues');
  const debtVintage = getField(data, 'bv_debt');
  const cashVintage = getField(data, 'cash_and_marketable_securities');
  const sharesVintage = getField(data, 'shares_outstanding');
  const priceVintage = getField(data, 'stock_price');

  const n = dcf?.revenue_projections.length ?? 0;
  const colCount = n + 2;
  const headers = ['Base year', ...Array.from({ length: n }, (_, i) => `Year ${i + 1}`), 'Terminal year'];
  const revenues = [fin0.revenues, ...(dcf?.revenue_projections ?? []), dcf?.terminal_revenue ?? undefined];
  const ebit = [baseEbit, ...(dcf?.ebit_projections ?? []), dcf?.terminal_ebit ?? undefined];
  const revenueGrowth = revenues.map((cur, i) => i > 0 && cur != null && revenues[i - 1]
    ? cur / revenues[i - 1]! - 1 : undefined);
  const ebitMargin = ebit.map((value, i) => value != null && revenues[i] ? value / revenues[i]! : undefined);
  const taxRatePerYear = [macro.tax_rate_effective ?? macro.tax_rate_marginal,
    ...(dcf?.tax_rate_projections ?? []), dcf?.terminal_tax_rate ?? undefined];
  const historicalFcff = data.cashflow?.fcff;
  const historicalReinvestment = data.cashflow?.reinvestment_firm;
  const baseNopat = historicalFcff != null && historicalReinvestment != null
    ? historicalFcff + historicalReinvestment : undefined;
  const ebitAfterTax = [baseNopat, ...(dcf?.nopat_projections ?? []), dcf?.terminal_nopat];
  const reinvestment = [historicalReinvestment ?? undefined, ...(dcf?.reinvestment_projections ?? []), dcf?.terminal_reinvestment];
  const fcff = [historicalFcff ?? undefined, ...(dcf?.fcff_projections ?? []), dcf?.terminal_fcff];
  const factors = dcf?.discount_factors ?? [];
  const costOfCapital = [undefined, ...factors.map((factor, i) => (i === 0 ? 1 : factors[i - 1]) / factor - 1), dcf?.terminal_wacc ?? undefined];
  const discountFactor = [undefined, ...factors, factors[n - 1]];
  const pvFcff = [undefined, ...(dcf?.pv_fcff ?? []), dcf?.pv_terminal_value ?? undefined];
  const terminalCF = dcf?.terminal_fcff;
  const terminalCostOfCapital = dcf?.terminal_wacc;
  const pvCashFlows = dcf?.pv_cash_flows_sum;
  const pvTerminal = dcf?.pv_terminal_value;
  const valueOfOpAssets = dcf?.value_as_going_concern;
  const failureProbability = assumptions.failure_probability;
  const proceedsIfFails = dcf?.distress_value;
  const adjustedOpAssets = dcf?.value_of_operating_assets;
  const bridge = dcf?.equity_bridge_components ?? {};
  const equityValue = dcf?.value_of_equity;
  const optionsValue = data.final?.value_of_all_options;
  const equityInCommon = equityValue != null && optionsValue != null ? equityValue - optionsValue : undefined;
  const explicitShares = (data.equity_bridge as { shares?: number } | null)?.shares;
  const sharesOutstanding = explicitShares ?? fin0.shares_outstanding;
  const valuePerShare = data.final?.value_per_share;
  const currentPrice = fin0.stock_price;

  // ---------- render helpers ----------

  /** One base column, the actual forecast horizon, and the terminal column. */
  function projRow(
    label: string,
    values: (number | null | undefined)[],
    format?: 'pct' | 'num',
    tooltip?: string | ((i: number) => string),
    termId?: string,
    bindingId?: string,
  ) {
    return (
      <tr>
        <SpreadsheetCell value={label} type="label" align="left" width="180px" termId={termId} bindingId={bindingId} />
        {values.map((v, i) => {
          const tip = typeof tooltip === 'function' ? tooltip(i) : tooltip;
          return (
            <SpreadsheetCell
              key={i}
              value={format === 'pct' ? pct(v) : fmtNum(v)}
              type="calc"
              tooltip={tip}
            />
          );
        })}
      </tr>
    );
  }

  /** Single value-bridge row: label + value */
  function bridgeRow(label: string, value: number | string | null | undefined, tooltip?: string, indent = false, termId?: string, bindingId?: string) {
    return (
      <tr>
        <SpreadsheetCell
          value={indent ? `  ${label}` : label}
          type="label"
          align="left"
          width="320px"
          termId={termId}
          bindingId={bindingId}
        />
        <SpreadsheetCell value={fmtNum(value)} type="calc" tooltip={tooltip} />
      </tr>
    );
  }

  // Tooltip composer for projection-table cells (column-aware)
  const projTip = (colLabel: string, baseDesc: string) => {
    return (i: number) => {
      if (i === 0) return `${baseDesc} — Base year (LTM from CIQ)`;
      if (i === colCount - 1) return `${baseDesc} — Terminal (year ${n + 1} perpetuity)`;
      return `${baseDesc} — ${colLabel.replace('{i}', String(i))}`;
    };
  };

  // ---------- render ----------

  return (
    <div className="p-4">
      <h2 className="text-lg font-bold mb-4">DCF Valuation Output</h2>

      <ColorLegend />

      {/* 各字段使用引擎实际基期；缺项不借旧年度补齐。 */}
      <div className="text-xs text-slate-500 mb-3 flex flex-wrap gap-x-3 gap-y-1 items-center">
        <span className="font-medium">Base-year vintage:</span>
        <span>Revenue <VintageBadge source={revVintage.vintage} /></span>
        <span>BV Debt <VintageBadge source={debtVintage.vintage} /></span>
        <span>Cash <VintageBadge source={cashVintage.vintage} /></span>
        <span>Shares <VintageBadge source={sharesVintage.vintage} /></span>
        <span>Stock Price <VintageBadge source={priceVintage.vintage} /></span>
      </div>

      {/* ===== Section 1: Projection Table ===== */}
      <div className="overflow-x-auto">
        <SpreadsheetGrid title={`Projection Table (${data.inputs.reporting_currency ?? '—'}, in millions)`}>
          <thead>
            <tr>
              <SpreadsheetCell value="" type="header" width="180px" />
              {headers.map((h) => (
                <SpreadsheetCell key={h} value={h} type="header" />
              ))}
            </tr>
          </thead>
          <tbody>
            {projRow('Revenue growth rate', revenueGrowth, 'pct',
              projTip('year {i}', 'Actual selected annual growth path; explicit annual conditions take priority over scalar stages.'), 'revenue-growth')}
            {projRow('Revenues', revenues, 'num',
              projTip('year {i}', 'Rev_t = Rev_{t-1} × (1 + g_t). Base year is LTM revenue from CIQ.'))}
            {projRow('EBIT margin', ebitMargin, 'pct',
              projTip('year {i}', 'Margin path: starts at user hypothesis (yr 1), converges linearly to target_operating_margin by margin_convergence_year, then holds flat.'), 'operating-margin')}
            {projRow('EBIT', ebit, 'num',
              projTip('year {i}', 'EBIT_t = Rev_t × margin_t (Ginzu: explicit compound, not EBIT×(1+growth))'), 'ebit')}
            {projRow(
              'Tax rate',
              taxRatePerYear,
              'pct',
              formula(
                'Effective in base year, converges to marginal over the projection',
                `Base = ${pct(macro.tax_rate_effective)} (what the firm actually paid); terminal → ${pct(macro.tax_rate_marginal)} (marginal). Tax-base rate from the engine. With R&D or NOL, this is not cash tax divided by economic EBIT.`,
              ),
              'effective-tax-rate',
            )}
            {projRow('NOPAT (after cash tax)', ebitAfterTax, 'num',
              backendField('dcf.nopat_projections', 'Economic EBIT minus actual modelled cash tax; includes selected R&D and NOL treatment'), 'nopat')}
            {projRow('Reinvestment', reinvestment, 'num',
              projTip('year {i}', 'Actual selected annual budget or capital-ratio path. Terminal reinvestment uses terminal growth / ROIC.'), 'reinvestment')}
            {projRow('FCFF', fcff, 'num',
              projTip('year {i}', 'FCFF_t = NOPAT_t − Reinvestment_t'), 'fcff')}
            {projRow('Cost of capital', costOfCapital, 'pct',
              'Actual annual discount-rate path from the engine factors; terminal uses dcf.terminal_wacc.', 'wacc', 'wacc.current')}
            {projRow('Cumulated discount factor', discountFactor, 'num',
              projTip('year {i}', 'CumDiscount_t = Π(1 / (1 + WACC_s)) for s=1..t. Product form handles non-constant WACC.'), 'discount-factor')}
            {projRow('PV(FCFF)', pvFcff, 'num',
              projTip('year {i}', 'PV = FCFF_t × CumDiscount_t'), 'present-value')}
          </tbody>
        </SpreadsheetGrid>
      </div>

      {/* ===== Section 2: Value Bridge ===== */}
      <SpreadsheetGrid title={`Value Bridge (${data.inputs.reporting_currency ?? '—'}, in millions — per-share figures on final row)`}>
        <tbody>
          {bridgeRow('Terminal cash flow', terminalCF,
            backendField('dcf.terminal_fcff', 'Stable-period NOPAT minus growth / ROIC reinvestment'), false, 'fcff')}
          {bridgeRow('Terminal cost of capital', pct(terminalCostOfCapital),
            backendField('dcf.terminal_wacc', 'Actual selected stable-period rate including the tax-shield treatment'))}
          {bridgeRow('Terminal value', dcf?.terminal_value_firm,
            formula('TV = Terminal FCFF / (terminal WACC − terminal growth) + selected tax transition value'), false, 'terminal-value', 'terminal-value.current')}
          {bridgeRow('PV(terminal value)', pvTerminal,
            formula(`PV(TV) = TV × cumulative discount factor at year ${n}`), false, 'terminal-value', 'terminal-value.present')}
          {bridgeRow(`PV(cash flows over next ${n} years)`, pvCashFlows,
            formula(`Σ PV(FCFF_t) for t=1..${n}`) + ' — ' + backendField('dcf.pv_cash_flows_sum'))}
          {bridgeRow('Sum of PV (operating assets)', valueOfOpAssets,
            formula('V_operating = Σ PV(FCFF) + PV(TV)') + ' — ' + backendField('dcf.value_as_going_concern'), false, 'operating-asset-value')}
          {bridgeRow('Probability of failure', pct(failureProbability),
            user('Failure probability', 'Default 0%. Override via FailureRate page. Applied as overlay to going-concern value.'))}
          {bridgeRow('Proceeds if firm fails', proceedsIfFails,
            formula('Proceeds = V_operating × distress_proceeds_pct (or BV if failure_tie_to = "B")'))}
          {bridgeRow('Value of operating assets (adjusted)', adjustedOpAssets,
            backendField('dcf.value_of_operating_assets', 'V_adj = V_gc × (1 − p_fail) + recovery × p_fail'), false, 'operating-asset-value', 'operating-asset-value.current')}
          {Object.entries(bridge).map(([key, value]) => <tr key={key}>
            <SpreadsheetCell value={`Equity bridge: ${key}`} type="label" />
            <SpreadsheetCell value={value} type="calc" tooltip={backendField(`dcf.equity_bridge_components.${key}`, 'Signed contribution to common equity before employee options')} />
          </tr>)}
          {bridgeRow('Value of equity', equityValue,
            formula('V_equity = adjusted operating value + sum of signed equity-bridge components'), false, 'equity-value', 'equity-value.current')}
          {bridgeRow('Minus: Value of options', optionsValue,
            'Iterative Black-Scholes from Module 6 (Option value page).')}
          {bridgeRow('Value of equity in common stock', equityInCommon,
            formula('= V_equity − Options'))}
          {bridgeRow('Number of shares', sharesOutstanding,
            `Shares outstanding from the engine base-year snapshot. CIQ mnemonic =CIQ("${inputs.ticker}","IQ_TOTAL_OUTSTANDING_FILING_DATE"). Missing current shares remain missing.`)}
          {(() => {
            const repCcy = inputs.reporting_currency;
            const listCcy = inputs.stock_price_currency;
            const fxRate = inputs.fx_rate;
            const marketListing = currentPrice;            // stock_price — listing ccy
            const marketReporting = fxRate != null && marketListing != null ? marketListing * fxRate : marketListing;
            const vpsReporting = valuePerShare;            // VPS — reporting ccy
            const marketForCompare = marketReporting;       // both sides in reporting ccy
            const ratio = vpsReporting != null && marketForCompare != null && vpsReporting !== 0
              ? marketForCompare / vpsReporting : undefined;
            return (
              <>
                <tr>
                  <SpreadsheetCell value="Value per share (reporting ccy)" type="label" align="left" width="320px" termId="value-per-share" bindingId="value-per-share.current" />
                  <td className="border px-1.5 py-0.5 bg-emerald-50 border-emerald-200 text-right whitespace-nowrap"
                      title={formula('VPS = V_equity_common / shares') + ' — ' + backendField('final.value_per_share')}>
                    <DualCurrency valueReporting={vpsReporting} reportingCcy={repCcy} listingCcy={listCcy} fxRate={fxRate} />
                  </td>
                </tr>
                <tr>
                  <SpreadsheetCell value="Current market price (listing ccy)" type="label" align="left" width="320px" />
                  <td className="border px-1.5 py-0.5 bg-emerald-50 border-emerald-200 text-right whitespace-nowrap"
                      title={ciq(inputs.ticker, 'IQ_CLOSEPRICE') + ` — displayed in listing ccy ${listCcy || '?'}; also shown converted to reporting ccy ${repCcy || '?'} for apples-to-apples comparison with VPS`}>
                    <DualCurrency valueListing={marketListing} reportingCcy={repCcy} listingCcy={listCcy} fxRate={fxRate} primary="listing" />
                  </td>
                </tr>
                <tr>
                  <SpreadsheetCell value="Price / Value (reporting-ccy basis)" type="label" align="left" width="320px" />
                  <td className="border px-1.5 py-0.5 bg-emerald-50 border-emerald-200 text-right whitespace-nowrap font-bold"
                      title={`Market ${fmtMoneyShort(marketForCompare, repCcy)} / VPS ${fmtMoneyShort(vpsReporting, repCcy)} — both in ${repCcy || '?'}. > 1 = overvalued on DCF; < 1 = undervalued.`}>
                    {ratio != null ? `${ratio.toFixed(2)}x` : '—'}
                  </td>
                </tr>
              </>
            );
          })()}
        </tbody>
      </SpreadsheetGrid>

      {/* Tax-rate override panel — mirrors the one on Stories to Numbers.
          Shared dot-path means editing in either location updates the same
          session field; the projection table above recomputes on each PATCH. */}
      <TaxOverridePanel data={data} onPatch={onPatch} />

      <SensitivityPanel data={data} onPatch={onPatch} onPatchMany={onPatchMany} />
    </div>
  );
}
