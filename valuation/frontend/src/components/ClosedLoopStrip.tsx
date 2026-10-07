import { forecastValue } from '../lib/forecast';
/**
 * Top-of-page summary strip for the three-story joint examination.
 *
 * Reads projected ROIC from the engine. Incremental capital assumptions and
 * historical stock turnover are distinct bases, without reverse inference.
 *
 * Current contract: AlphaLake `docs/valuation/engine-change-ledger.md`, E39.
 */

import type { ValuationResponse } from '../types/valuation';
import { firstProjectedROIC, gapStatement } from '../lib/reverseChecks';

interface Props {
  data: ValuationResponse;
}

function pct(v: number | null | undefined, digits = 1): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${(v * 100).toFixed(digits)}%`;
}
function dec(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v.toFixed(digits)}×`;
}

export default function ClosedLoopStrip({ data }: Props) {
  const cf = data.cashflow;
  const ind = data.inputs.industry_data;
  const wacc = data.cost_of_capital?.wacc ?? null;

  const impliedROIC = firstProjectedROIC(data);
  const initialCapitalRatio = forecastValue(data, 'sales_to_capital_high');

  // Historical (5yr avg) and industry references
  const histROIC = cf?.historical_roic_avg_5yr ?? null;
  const histSC = cf?.historical_s_c_avg_5yr ?? null;
  const indROIC = ind?.roic ?? null;
  const indSC = ind?.sales_to_capital ?? null;

  // Gaps
  const roicGapHist = gapStatement(impliedROIC, histROIC, 'pp');
  const roicGapInd = gapStatement(impliedROIC, indROIC, 'pp');
  const roicGapWacc = gapStatement(impliedROIC, wacc, 'pp');

  // Terminal state invariant
  const terminalImplied = data.dcf?.implied_roic_terminal ?? null;
  const terminalWACC = data.dcf?.terminal_wacc ?? null;

  return (
    <section className="my-4 bg-white border border-slate-300 rounded-md shadow-sm p-3">
      <h3 className="text-sm font-semibold text-slate-800 mb-2">
        Closed-loop summary — projected returns and capital assumptions
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-6 gap-2 text-xs">
        {/* ROIC row */}
        <div className="md:col-span-6 border-b border-slate-200 pb-2">
          <div className="font-semibold text-slate-700 mb-1">First-year projected ROIC (NOPAT / opening capital)</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 items-baseline">
            <span className="text-emerald-700 font-mono font-semibold text-sm">
              {pct(impliedROIC)}
            </span>
            <span className="text-slate-500">
              vs Historical 5-yr avg: <span className="font-mono text-slate-800">{pct(histROIC)}</span> <span className="text-slate-400">({roicGapHist})</span>
            </span>
            <span className="text-slate-500">
              vs Industry median: <span className="font-mono text-slate-800">{pct(indROIC)}</span> <span className="text-slate-400">({roicGapInd})</span>
            </span>
            <span className="text-slate-500">
              vs WACC: <span className="font-mono text-slate-800">{pct(wacc)}</span> <span className="text-slate-400">({roicGapWacc})</span>
            </span>
          </div>
        </div>

        {/* S/C row */}
        <div className="md:col-span-6 border-b border-slate-200 pb-2">
          <div className="font-semibold text-slate-700 mb-1">Initial incremental Sales/Capital assumption</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 items-baseline">
            <span className="text-emerald-700 font-mono font-semibold text-sm">
              {dec(initialCapitalRatio)}
            </span>
            <span className="text-slate-500">
              Historical stock turnover: <span className="font-mono text-slate-800">{dec(histSC)}</span>
            </span>
            <span className="text-slate-500">
              Industry stock turnover: <span className="font-mono text-slate-800">{dec(indSC)}</span>
            </span>
            <span className="text-slate-400 italic">
              （新增收入／总净投入与历史收入／存量资本口径不同，不反推已核验回报。）
            </span>
          </div>
        </div>

        {/* Terminal row */}
        <div className="md:col-span-6">
          <div className="font-semibold text-slate-700 mb-1">Terminal state</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 items-baseline">
            <span className="text-slate-500">
              Implied terminal ROIC: <span className="font-mono text-slate-800">{pct(terminalImplied)}</span>
            </span>
            <span className="text-slate-500">
              vs Terminal WACC: <span className="font-mono text-slate-800">{pct(terminalWACC)}</span> <span className="text-slate-400">({gapStatement(terminalImplied, terminalWACC, 'pp')})</span>
            </span>
            <span className="text-slate-400 italic">
              稳态回报条件：{pct(data.dcf?.terminal_roic)}。未覆盖时采用WACC的无超额回报条件；持续优势需经营依据。
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
