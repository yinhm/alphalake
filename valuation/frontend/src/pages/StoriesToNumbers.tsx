import { forecastValue } from '../lib/forecast';
import type { ValuationResponse } from '../types/valuation';
import type { PatchValue } from '../api/client';
import SpreadsheetCell from '../components/SpreadsheetCell';
import SpreadsheetGrid from '../components/SpreadsheetGrid';
import ColorLegend from '../components/ColorLegend';
import { baseYear } from '../lib/baseYear';
import { firstProjectedROIC } from '../lib/reverseChecks';
import ClosedLoopStrip from '../components/ClosedLoopStrip';
import StoryValidationBlock from '../components/StoryValidationBlock';
import TaxOverridePanel from '../components/TaxOverridePanel';
import SensitivityPanel from '../components/SensitivityPanel';
import HistoricalDetailTable from '../components/HistoricalDetailTable';

interface Props {
  data: ValuationResponse;
  sessionId?: string | null;
  onPatch?: (path: string, value: PatchValue) => void | Promise<void>;
  onPatchMany?: (overrides: Record<string, PatchValue>) => void | Promise<void>;
}

export default function StoriesToNumbers({ data, onPatch, onPatchMany }: Props) {
  const assumptions = data.inputs.valuation_assumptions;
  const industry = data.inputs.industry_data;
  const cf = data.cashflow;
  const fin = baseYear(data);

  // Industry stats — some fields carry quartile ranges on industry_data.
  // For the three-story blocks we need median, Q1, Q3 for each metric.
  // The current IndustryData shape only exposes single-value medians; if
  // the backend surfaces quartile stats elsewhere, wire them in here.
  // Falls back to median-only for Q1/Q3 if not available.
  const stats = data.industry_stats ?? null;
  const q = (k: 'revenue_growth_3y' | 'pretax_operating_margin' | 'sales_to_capital') =>
    stats?.[k] ?? null;

  const narrative: { narrative: string; driver: string; termId?: string; bindingId?: string; input: string; value: string | number | null; type: 'hypothesis' | 'calc' | 'reference' }[] = [
    {
      narrative: 'How fast will the company grow revenues?',
      driver: 'Revenue growth (next year)',
      termId: 'revenue-growth',
      bindingId: 'revenue-growth.next-year',
      input: 'revenue_growth_next_year',
      value: forecastValue(data, 'revenue_growth_next_year'),
      type: 'hypothesis',
    },
    {
      narrative: 'How fast will it grow after year 1?',
      driver: data.inputs.valuation_assumptions.high_growth_years === 1 ? 'Revenue growth (transition anchor)' : `Revenue growth (years 2–${data.inputs.valuation_assumptions.high_growth_years})`,
      termId: 'revenue-growth',
      bindingId: 'revenue-growth.high',
      input: 'revenue_growth_years_2_5',
      value: forecastValue(data, 'revenue_growth_years_2_5'),
      type: 'hypothesis',
    },
    {
      narrative: 'How profitable will the company be at maturity?',
      driver: 'Target pre-tax operating margin',
      termId: 'operating-margin',
      bindingId: 'operating-margin.target',
      input: 'target_operating_margin',
      value: forecastValue(data, 'target_operating_margin'),
      type: 'hypothesis',
    },
    {
      narrative: 'How long until current margin reaches target?',
      driver: 'Margin convergence year (K)',
      termId: 'operating-margin',
      input: 'margin_convergence_year',
      value: forecastValue(data, 'margin_convergence_year'),
      type: 'hypothesis',
    },
    {
      narrative: 'How efficiently will capital generate revenue?',
      driver: 'Sales/Capital (initial stage)',
      termId: 'sales-to-capital',
      bindingId: 'sales-to-capital.high',
      input: 'sales_to_capital_high',
      value: forecastValue(data, 'sales_to_capital_high'),
      type: 'hypothesis',
    },
    {
      narrative: 'Reinvestment in the stable phase',
      driver: 'Sales/Capital (transition stage)',
      termId: 'sales-to-capital',
      bindingId: 'sales-to-capital.stable',
      input: 'sales_to_capital_stable',
      value: forecastValue(data, 'sales_to_capital_stable'),
      type: 'hypothesis',
    },
    {
      narrative: 'How risky is this company?',
      driver: 'Cost of capital (WACC)',
      termId: 'wacc',
      bindingId: 'wacc.current',
      input: 'wacc',
      value: data.cost_of_capital?.wacc ?? null,
      type: 'calc',
    },
    {
      narrative: 'What is the long-term growth rate?',
      driver: 'Terminal growth',
      termId: 'stable-growth',
      bindingId: 'stable-growth.input',
      input: 'stable_growth_rate',
      value: forecastValue(data, 'growth_perpetuity_rate'),
      type: 'hypothesis',
    },
    {
      narrative: 'Could this company fail?',
      driver: 'Probability of failure',
      termId: 'failure-probability',
      bindingId: 'failure-probability.input',
      input: 'failure_probability',
      value: assumptions.failure_probability,
      type: 'hypothesis',
    },
    {
      narrative: 'Current market price (reference)',
      driver: 'Current stock price',
      input: 'stock_price',
      value: fin?.stock_price ?? null,
      type: 'reference',
    },
  ];

  const impliedROIC = firstProjectedROIC(data);

  // Per-cell tooltip builders — surface the exact calculation for every
  // historical annual value, reading from raw_financials so the analyst
  // can cross-check each cell.
  const history = data.inputs.raw_financials ?? [];
  const fmtM = (v: number | null | undefined) =>
    v == null ? '—' : `${(v / 1000).toLocaleString('en-US', { maximumFractionDigits: 1 })}B`;
  const yearLabel = (i: number) => `FY-${i}`;

  const growthTooltip = (i: number): string => {
    const cur = history[i]?.revenues;
    const prev = history[i + 1]?.revenues;
    if (cur == null || prev == null) return `No data for ${yearLabel(i)} (revenue or prior-year revenue missing).`;
    const g = cur / prev - 1;
    return `${yearLabel(i)} revenue growth = (Rev[${yearLabel(i)}] − Rev[${yearLabel(i + 1)}]) / Rev[${yearLabel(i + 1)}]\n= (${fmtM(cur)} − ${fmtM(prev)}) / ${fmtM(prev)}\n= ${(g * 100).toFixed(2)}%\nSource: IQ_TOTAL_REV × IQ_FY-${i} and IQ_FY-${i + 1}.`;
  };

  const marginTooltip = (i: number): string => {
    const margin = cf?.historical_margin_by_year?.[i];
    if (margin == null) return `No complete adjusted margin for ${yearLabel(i)}.`;
    return `${yearLabel(i)} (${history[i]?.fiscal_year}) adjusted operating margin = ${(margin * 100).toFixed(2)}%\n后端逐年研发队列：年度EBIT + 当年研发 − 当年研发摊销，再除以当年收入。不能用当期研发资产或未调整EBIT替代。历史年度租赁调整未重建，与当前调整利润率仍须核对组成。`;
  };

  const scTooltip = (i: number): string => {
    const value = cf?.historical_s_c_by_year?.[i];
    return value == null ? `No complete capital ratio for ${yearLabel(i)}.`
      : `${yearLabel(i)} (${history[i]?.fiscal_year}) sales/capital = ${value.toFixed(3)}×\n后端年度收入/年度投入资本（含可重建的研发资产），不是新增收入/新增资本的预测倍率。`;
  };

  const roicTooltip = (i: number): string => {
    const value = cf?.historical_roic_by_year?.[i];
    return value == null ? `No complete ROIC for ${yearLabel(i)}.`
      : `${yearLabel(i)} (${history[i]?.fiscal_year}) ROIC = ${(value * 100).toFixed(2)}%\n后端当年调整NOPAT/上年期末投入资本，使用当年税务证据；缺项不以默认税率补算。`;
  };

  const avgTooltip = (metric: string, years: number) => (_window: 3 | 5): string =>
    `${years}Y average ${metric}: complete backend annual diagnostics only; missing years are not zero.`;

  return (
    <div className="max-w-6xl">
      <h2 className="text-xl font-bold mb-2">Stories to Numbers</h2>
      <ColorLegend />

      <p className="text-sm text-slate-600 mb-3">
        Three stories — Growth, Margin, Capital Efficiency — are mathematically coupled.
        This page surfaces projected ROIC and explicit capital assumptions alongside
        historical and industry anchors so you can judge whether the three stories close
        the loop. Edit drivers in the Sensitivity panel below; everything on this page
        recomputes on every change.
      </p>

      {/* Narrative mapping table */}
      <SpreadsheetGrid>
        <thead>
          <tr>
            <SpreadsheetCell type="header" value="Narrative Question" align="left" />
            <SpreadsheetCell type="header" value="Value Driver" align="left" />
            <SpreadsheetCell type="header" value="Input Field" align="left" />
            <SpreadsheetCell type="header" value="Current Value" />
            <SpreadsheetCell type="header" value="Source" />
          </tr>
        </thead>
        <tbody>
          {narrative.map((s, i) => (
            <tr key={i}>
              <SpreadsheetCell type="label" value={s.narrative} align="left" />
              <SpreadsheetCell type="label" value={s.driver} align="left" termId={s.termId} bindingId={s.bindingId} />
              <SpreadsheetCell type="hint" value={s.input} align="left" />
              <SpreadsheetCell type={s.type} value={s.value} tooltip={data.dcf?.forecast_parameters?.[s.input === 'stable_growth_rate' ? 'growth_perpetuity_rate' : s.input]?.basis} />
              <SpreadsheetCell type="label" value={
                s.type === 'hypothesis' ? (data.dcf?.forecast_parameters?.[s.input === 'stable_growth_rate' ? 'growth_perpetuity_rate' : s.input]?.source ?? 'User Input') :
                s.type === 'reference' ? 'Industry/Market' : 'Calculated'
              } align="center" />
            </tr>
          ))}
        </tbody>
      </SpreadsheetGrid>

      {/* Three-story joint examination */}
      <ClosedLoopStrip data={data} />

      <StoryValidationBlock
        title="Growth Story — how fast will revenue grow? (CAGR as primary average)"
        historical={cf?.historical_revenue_growth_by_year ?? []}
        avg3={cf?.historical_revenue_cagr_3yr ?? null}
        avg5={cf?.historical_revenue_cagr_5yr ?? null}
        industryMedian={industry?.revenue_growth ?? null}
        industryQ1={q('revenue_growth_3y')?.q1 ?? null}
        industryQ3={q('revenue_growth_3y')?.q3 ?? null}
        formatAs="pct"
        cellTooltip={growthTooltip}
        averageTooltip={(w) => `${w}-year CAGR = (Rev[FY-0] / Rev[FY-${w}])^(1/${w}) − 1.\nGeometric — financially correct multi-period growth measure, immune to volatility.\nSee the 10-year detail table below for the arithmetic-mean comparison.`}
      />

      <StoryValidationBlock
        title="Margin Story — pre-tax operating margin (EBIT / Revenue)"
        historical={cf?.historical_margin_by_year ?? []}
        avg3={cf?.historical_margin_avg_3yr ?? null}
        avg5={cf?.historical_margin_avg_5yr ?? null}
        industryMedian={industry?.pretax_operating_margin ?? null}
        industryQ1={q('pretax_operating_margin')?.q1 ?? null}
        industryQ3={q('pretax_operating_margin')?.q3 ?? null}
        formatAs="pct"
        cellTooltip={marginTooltip}
        averageTooltip={avgTooltip('pre-tax operating margin', 5)}
        reverseCheck={{
          label: 'First-year projected ROIC (NOPAT / opening capital)',
          required: impliedROIC,
          actual: cf?.historical_roic_avg_5yr ?? null,
          unit: 'pp',
        }}
      />

      {/* Historical ROIC block — the closed-loop output measured annually
          from the firm's own history. Mirrors the other three blocks' layout. */}
      <StoryValidationBlock
        title="Historical ROIC — what the firm has actually earned per dollar of capital"
        historical={cf?.historical_roic_by_year ?? []}
        avg3={cf?.historical_roic_avg_3yr ?? null}
        avg5={cf?.historical_roic_avg_5yr ?? null}
        industryMedian={industry?.roic ?? null}
        industryQ1={null}
        industryQ3={null}
        formatAs="pct"
        cellTooltip={roicTooltip}
        averageTooltip={avgTooltip('ROIC', 5)}
      />

      <StoryValidationBlock
        title="Capital-Efficiency Story — how much capital does each dollar of revenue require?"
        historical={cf?.historical_s_c_by_year ?? []}
        avg3={cf?.historical_s_c_avg_3yr ?? null}
        avg5={cf?.historical_s_c_avg_5yr ?? null}
        industryMedian={industry?.sales_to_capital ?? null}
        industryQ1={q('sales_to_capital')?.q1 ?? null}
        industryQ3={q('sales_to_capital')?.q3 ?? null}
        formatAs="dec"
        cellTooltip={scTooltip}
        averageTooltip={avgTooltip('Sales/Capital', 5)}
      />

      {/* 10-year detailed historical table — lists every annual value for
          ROIC, S/C, margin, and growth, with per-year tooltips showing the
          exact calculation and dual averaging (arithmetic + CAGR/weighted
          where financially warranted). Sits directly below the three
          Required-X reverse checks so the analyst can scan annual
          history alongside the required-values call-out. */}
      <HistoricalDetailTable data={data} />

      {/* Tax-rate override panel */}
      <TaxOverridePanel data={data} onPatch={onPatch} />

      {/* How these are calculated — explanatory footer */}
      <section className="my-4 bg-slate-50 border border-slate-200 rounded-md p-3 text-xs text-slate-700">
        <h3 className="text-sm font-semibold text-slate-800 mb-2">
          How each number is calculated
        </h3>
        <dl className="space-y-2">
          <div>
            <dt className="font-semibold text-slate-900">Historical Revenue Growth (per year)</dt>
            <dd className="ml-3 font-mono text-[11px]">
              = (Revenue[t] − Revenue[t−1]) / Revenue[t−1]
              <br />
              Source: IQ_TOTAL_REV × IQ_FY-t and IQ_FY-(t+1)
            </dd>
          </div>
          <div>
            <dt className="font-semibold text-slate-900">Historical Pre-tax Operating Margin (per year)</dt>
            <dd className="ml-3 font-mono text-[11px]">
              = EBIT[t] / Revenue[t]
              <br />
              Source: IQ_EBIT / IQ_TOTAL_REV × IQ_FY-t
            </dd>
          </div>
          <div>
            <dt className="font-semibold text-slate-900">Historical Invested Capital (per year)</dt>
            <dd className="ml-3 font-mono text-[11px]">
              IC[t] = BV Equity[t] + BV Debt[t] − Cash[t]
              <br />
              Source: IQ_TOTAL_EQUITY + IQ_TOTAL_DEBT − IQ_CASH_ST_INVEST × IQ_FY-t
              <br />
              <span className="italic text-slate-500">
                Note: for the DCF engine's IC, the R&D capitalization asset is added; this historical diagnostic
                uses raw book values only for cross-checkability against the 10-K.
              </span>
            </dd>
          </div>
          <div>
            <dt className="font-semibold text-slate-900">Historical ROIC (per year)</dt>
            <dd className="ml-3 font-mono text-[11px]">
              = NOPAT[t] / IC[t−1]
              <br />
              NOPAT[t] = EBIT[t] × (1 − effective tax[t])
              <br />
              effective tax[t] = IQ_INC_TAX[t] / IQ_EBT_EXCL[t]
              <br />
              Uses prior-year IC as the denominator (standard convention — matches the capital in place at the
              start of the period during which NOPAT was earned).
            </dd>
          </div>
          <div>
            <dt className="font-semibold text-slate-900">Historical Sales / Capital (per year)</dt>
            <dd className="ml-3 font-mono text-[11px]">
              = Revenue[t] / IC[t]
              <br />
              Uses current-year IC so the ratio reflects the capital in place during the revenue-generating period.
            </dd>
          </div>
          <div>
            <dt className="font-semibold text-slate-900">3-yr and 5-yr averages</dt>
            <dd className="ml-3 font-mono text-[11px]">
              Simple mean across annual values. Years where the ratio resolved to None (missing components,
              zero denominator) are skipped — the average is taken over whatever values were computable.
            </dd>
          </div>
          <div>
            <dt className="font-semibold text-slate-900">First-year projected ROIC</dt>
            <dd className="ml-3 font-mono text-[11px]">
              = first-year NOPAT / opening invested capital
              <br />
              Read directly from the existing DCF response, using matched after-tax operating profit
              and capital. Missing capital remains unavailable. Historical ROIC and WACC are references;
              they do not certify the operating forecast.
            </dd>
          </div>
          <div>
            <dt className="font-semibold text-slate-900">Incremental and historical Sales / Capital</dt>
            <dd className="ml-3 font-mono text-[11px]">
              The forecast assumption pairs incremental revenue with total net reinvestment and its timing.
              Historical turnover pairs annual revenue with stock capital. Their scopes differ; dividing
              an after-tax ROIC anchor by a pre-tax target margin does not establish a required ratio.
            </dd>
          </div>
          <div>
            <dt className="font-semibold text-slate-900">Implied ROIC Projections (per year, from the DCF engine)</dt>
            <dd className="ml-3 font-mono text-[11px]">
              Implied ROIC[t] = NOPAT[t] / IC[t−1] from the projection path itself.
              <br />
              As the stories project forward, IC rolls forward as IC[t] = IC[t−1] + Reinvestment[t].
              <br />
              Terminal ROIC defaults to WACC as a no-excess-return condition. An explicit override can
              represent sustained competitive advantage when supported by business evidence. The capital
              path's implied terminal return is a separate consistency check.
            </dd>
          </div>
        </dl>
      </section>

      {/* Reuse the existing SensitivityPanel — same archetype presets,
          Reset, tornado, 8-driver sliders, live impact cards. */}
      <SensitivityPanel data={data} onPatch={onPatch} onPatchMany={onPatchMany} />
    </div>
  );
}
