import type { ValuationResponse } from '../types/valuation';
import SpreadsheetCell from '../components/SpreadsheetCell';
import SpreadsheetGrid from '../components/SpreadsheetGrid';
import ColorLegend from '../components/ColorLegend';
import { ciq, formula, user, backendField } from '../lib/sources';
import { baseYear } from '../lib/baseYear';

// ---------- helpers ----------

function fmtNum(v: number | null | undefined): number | string {
  if (v === null || v === undefined) return '';
  return v;
}

function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '';
  return (v * 100).toFixed(2) + '%';
}

// ---------- component ----------

export default function OptionValue({ data }: { data: ValuationResponse; sessionId?: string | null }) {
  const opt = data.inputs.option_inputs;
  const fin0 = baseYear(data);              // LTM-rotated base year
  const macro = data.inputs.macro_inputs;
  const fin = data.final;
  const pricing = fin?.option_pricing;

  // ---------- render helpers ----------

  /** Single input row: label + value */
  function inputRow(label: string, value: number | string | null | undefined, tooltip?: string) {
    return (
      <tr>
        <SpreadsheetCell value={label} type="label" align="left" width="280px" />
        <SpreadsheetCell value={typeof value === 'string' ? value : fmtNum(value)} type="hypothesis" tooltip={tooltip} />
      </tr>
    );
  }

  /** Single calc row: label + value */
  function calcRow(label: string, value: number | string | null | undefined, tooltip?: string) {
    return (
      <tr>
        <SpreadsheetCell value={label} type="label" align="left" width="280px" />
        <SpreadsheetCell value={typeof value === 'string' ? value : fmtNum(value)} type="calc" tooltip={tooltip} />
      </tr>
    );
  }

  const ticker = data.inputs.ticker;

  // ---------- render ----------

  return (
    <div className="p-4 max-w-4xl">
      <h2 className="text-lg font-bold mb-4">Option Value (Black-Scholes)</h2>

      <ColorLegend />

      {!opt.has_options ? (
        <div className="mt-6 p-4 bg-gray-50 border border-gray-200 rounded text-gray-500">
          No employee options outstanding.
        </div>
      ) : (
        <>
          {/* ===== Section 1: Option Inputs ===== */}
          <SpreadsheetGrid title="Option Inputs">
            <tbody>
              {inputRow('Stock price', fmtNum(fin0?.stock_price),
                ciq(ticker, 'IQ_CLOSEPRICE'))}
              {inputRow('Average strike price', fmtNum(opt.average_strike_price),
                user('Average strike of outstanding employee options', 'From 10-K footnote on stock-based compensation'))}
              {inputRow('Average expiration (years)', fmtNum(opt.average_maturity),
                user('Weighted-average remaining life of outstanding options', 'From 10-K footnote'))}
              {inputRow('Standard deviation (σ)', pct(opt.stock_price_std_dev),
                user('Annualized volatility of stock returns. Used ONLY for Black-Scholes option valuation on this page — does not feed WACC, the main DCF, or the failure probability overlay.', 'Typically 30–60% for public equities; use 3–5 year rolling std dev of weekly returns × √52'))}
              {inputRow('Dividend yield', pct(opt.dividend_yield),
                ciq(ticker, 'IQ_DIV_YIELD'))}
              {inputRow('Risk-free rate', pct(macro.risk_free_rate),
                user('Risk-free rate (10y T-bond)', 'Same RF used in WACC'))}
              {inputRow('Number of options', fmtNum(opt.number_of_options),
                user('Total outstanding options/warrants', 'From 10-K footnote'))}
              {inputRow('Number of shares', fmtNum(fin0?.shares_outstanding),
                ciq(ticker, 'IQ_BASIC_WEIGHT', 'IQ_FQ-0'))}
              {inputRow('Has options', opt.has_options ? 'Yes' : 'No',
                'Toggle: when false, option value = 0 and this page skips BSM')}
            </tbody>
          </SpreadsheetGrid>

          {/* ===== Section 2: Dilution-Adjusted Black-Scholes ===== */}
          <SpreadsheetGrid title="Dilution-Adjusted Black-Scholes">
            <tbody>
              {calcRow('Adjusted S (actual engine input)', pricing?.adjusted_stock_price,
                backendField('final.option_pricing.adjusted_stock_price', 'Existing intrinsic-seeded dilution iteration; no market-price substitution'))}
              {calcRow('Seed value per share', pricing?.seed_value_per_share)}
              {calcRow('K (strike price)', opt.average_strike_price)}
              {calcRow('t (time to expiration)', opt.average_maturity)}
              {calcRow('sigma (std dev)', pct(opt.stock_price_std_dev))}
              {calcRow('d1', pricing?.d1)}
              {calcRow('N(d1)', pricing?.n_d1)}
              {calcRow('d2', pricing?.d2)}
              {calcRow('N(d2)', pricing?.n_d2)}
              {calcRow('Iterations', pricing?.iterations)}
              {calcRow('Convergence residual', pricing?.residual)}
              {calcRow('Value per option', fmtNum(fin?.call_value_per_option),
                formula('Call = S·e^(−y·t)·N(d1) − K·e^(−r·t)·N(d2)',
                        'Black-Scholes-Merton with continuous dividend yield. Output after fixed-point iteration converges.') + ' — ' + backendField('final.call_value_per_option'))}
              {calcRow('Total value of options', fmtNum(fin?.value_of_all_options),
                formula('Total = Call × N_options') + ' — ' + backendField('final.value_of_all_options') + ' (subtracted from equity value in bridge)')}
            </tbody>
          </SpreadsheetGrid>
        </>
      )}
    </div>
  );
}
