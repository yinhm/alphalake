import type { ValuationResponse } from '../types/valuation';
import SpreadsheetCell from '../components/SpreadsheetCell';
import SpreadsheetGrid from '../components/SpreadsheetGrid';
import { baseYear } from '../lib/baseYear';

function dec(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return '';
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export default function RelativeValuation({ data }: { data: ValuationResponse; sessionId?: string | null }) {
  const multiples = data.multiples;
  const fin0 = baseYear(data);          // LTM-rotated base year
  const ind = data.inputs.industry_data;
  const indGlobal = data.inputs.industry_data_global;
  const adj = data.adjusted;

  // Compute company market-value multiples
  const mvEquity = fin0?.mv_equity;
  const debtContribution = data.dcf?.equity_bridge_components.debt;
  const mvDebt = debtContribution == null ? null : -debtContribution;
  const cash = fin0?.cash_and_marketable_securities;
  const ev = fin0?.mv_equity != null && mvDebt != null && cash != null ? mvEquity! + mvDebt - cash : null;

  const rev = fin0?.revenues;
  const ebitda = fin0?.ebitda;
  const bvEquity = adj?.adjusted_bv_equity ?? fin0?.bv_equity;

  const pe_market = multiples?.pe_ratio_market ?? null;
  const pbv_market = bvEquity != null && bvEquity > 0 && mvEquity != null ? mvEquity / bvEquity : null;
  const ev_ebitda_market = ebitda != null && ebitda > 0 && ev != null ? ev / ebitda : null;
  const ev_sales_market = rev != null && rev > 0 && ev != null ? ev / rev : null;

  const rows: Array<{
    label: string;
    intrinsic: number | null | undefined;
    market: number | null;
    regional: number | null | undefined;
    global: number | null | undefined;
    formula: string;
    sourceRegional: string;
    sourceGlobal: string;
  }> = [
    {
      label: 'PE Ratio',
      intrinsic: multiples?.pe_ratio_intrinsic,
      market: pe_market,
      regional: ind.pe_ratio,
      global: indGlobal?.pe_ratio,
      formula: 'Stable forward P/E = (1 − equity reinvestment rate) / (Ke − g); not current multi-stage DCF equity / current earnings',
      sourceRegional: 'pedata.xls',
      sourceGlobal: 'peGlobal.xls',
    },
    {
      label: 'PBV Ratio',
      intrinsic: multiples?.pbv_ratio_intrinsic,
      market: pbv_market,
      regional: ind.pbv_ratio,
      global: indGlobal?.pbv_ratio,
      formula: 'Stable P/BV = (ROE − g) / (Ke − g); not the multi-stage DCF',
      sourceRegional: 'pbvdata.xls',
      sourceGlobal: 'pbvGlobal.xls',
    },
    {
      label: 'EV / EBITDA',
      intrinsic: multiples?.ev_ebitda_intrinsic,
      market: ev_ebitda_market,
      regional: ind.ev_ebitda,
      global: indGlobal?.ev_ebitda,
      formula: 'Current-cash-flow perpetuity illustration = current FCFF / EBITDA / (WACC − g); no forecast-period roll-forward',
      sourceRegional: 'vebitda.xls',
      sourceGlobal: 'vebitdaGlobal.xls',
    },
    {
      label: 'EV / Sales',
      intrinsic: multiples?.ev_sales_intrinsic,
      market: ev_sales_market,
      regional: ind.ev_sales,
      global: indGlobal?.ev_sales,
      formula: 'Current-cash-flow perpetuity illustration = current NOPAT margin × (1 − reinvestment rate) / (WACC − g)',
      sourceRegional: 'psdata.xls',
      sourceGlobal: 'psGlobal.xls',
    },
  ];

  return (
    <div className="max-w-[95vw] mx-auto p-4">
      <h1 className="text-xl font-bold mb-1">Relative Valuation — Stable Illustrations and Market Multiples</h1>
      <p className="text-sm text-gray-600 mb-4">
        稳定模型示意与市场倍数的利润期间、再投资及范围不完全相同，不能直接据此判断高估或低估。下列稳定模型不是本次多阶段DCF；公司市场PE使用后端调整后净利润。
        <span className="inline-block bg-emerald-50 border border-emerald-200 px-2 ml-2 rounded-sm">Stable illustration</span>
        <span className="inline-block bg-sky-50 border border-sky-200 px-2 ml-1 rounded-sm">Market-observed</span>
        <span className="inline-block bg-slate-50 border border-slate-200 px-2 ml-1 rounded-sm">Industry benchmark</span>
      </p>

      <SpreadsheetGrid title="Multiples Comparison">
        <thead>
          <tr>
            <SpreadsheetCell value="Multiple" type="header" width="160px" />
            <SpreadsheetCell value="Stable illustration" type="header" width="130px" />
            <SpreadsheetCell value="Market" type="header" width="130px" />
            <SpreadsheetCell value={`${ind.region} Industry`} type="header" width="130px" />
            <SpreadsheetCell value="Global Industry" type="header" width="130px" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            return (
              <tr key={`rv-${i}`}>
                <SpreadsheetCell value={r.label} type="label" />
                <SpreadsheetCell value={dec(r.intrinsic)} type="calc" tooltip={r.formula} />
                <SpreadsheetCell value={dec(r.market)} type="financial" tooltip="From market cap, EV, and reported financials" />
                <SpreadsheetCell value={dec(r.regional)} type="reference" tooltip={`Source: ${r.sourceRegional} | Industry: ${ind.industry_name} (${ind.region})`} />
                <SpreadsheetCell value={dec(r.global)} type="reference" tooltip={`Source: ${r.sourceGlobal} | Industry: ${ind.industry_name} (Global)`} />
              </tr>
            );
          })}
        </tbody>
      </SpreadsheetGrid>

      <SpreadsheetGrid title="Underlying Inputs">
        <tbody>
          <tr>
            <SpreadsheetCell value={`Market Cap (${data.inputs.reporting_currency ?? '—'}, millions)`} type="label" termId="market-cap" width="280px" />
            <SpreadsheetCell value={dec(mvEquity, 0)} type="financial"
              tooltip={`Market cap in REPORTING currency. Fetched as =CIQ("${data.ticker}","IQ_MARKETCAP","","REPORTED") when the template supplies the reporting-ccy variant; otherwise derived as mv_equity_listing × fx_rate. Paired with bv_debt and cash (also reporting-ccy) to compute EV, P/E_market, and EV/EBITDA_market on a same-currency basis.`} />
          </tr>
          <tr>
            <SpreadsheetCell value="Adjusted MV Debt (incl lease PV)" type="label" />
            <SpreadsheetCell value={dec(mvDebt, 0)} type="calc" tooltip="Actual debt claim used in this calculation; unknown remains blank." />
          </tr>
          <tr>
            <SpreadsheetCell value="Cash & Marketable Securities" type="label" />
            <SpreadsheetCell value={dec(cash, 0)} type="financial" tooltip="Base-year cash. Source: IQ_CASH_ST_INVEST × IQ_FY-0 (or LTM-rotated). Subtracted in the EV calc." />
          </tr>
          <tr>
            <SpreadsheetCell value="Basic EV proxy = MV_E + MV_D − Cash" type="label" bold />
            <SpreadsheetCell value={dec(ev, 0)} type="calc" bold tooltip="Basic EV proxy excludes other claims and assets; it is not the full DCF equity bridge." />
          </tr>
          <tr>
            <SpreadsheetCell value="Revenue" type="label" />
            <SpreadsheetCell value={dec(rev, 0)} type="financial" tooltip="LTM revenue. Source: IQ_TOTAL_REV rotated to LTM. Denominator of EV/Sales." />
          </tr>
          <tr>
            <SpreadsheetCell value="EBITDA" type="label" />
            <SpreadsheetCell value={dec(ebitda, 0)} type="financial" tooltip="LTM EBITDA. Source: IQ_EBITDA rotated to LTM. Denominator of EV/EBITDA." />
          </tr>
          <tr>
            <SpreadsheetCell value="Adjusted Net Income" type="label" />
            <SpreadsheetCell value={dec(adj?.adjusted_net_income, 0)} type="calc" tooltip="Net Income + R&D add-back − amortization (from M1)" />
          </tr>
          <tr>
            <SpreadsheetCell value="Adjusted BV Equity" type="label" />
            <SpreadsheetCell value={dec(bvEquity, 0)} type="calc" tooltip="BV Equity + Value of Research Asset (from M1)" />
          </tr>
          <tr>
            <SpreadsheetCell value="Value of Operating Assets (DCF)" type="label" />
            <SpreadsheetCell value={dec(data.dcf?.value_of_operating_assets, 0)} type="calc" tooltip="Σ PV(FCFF) + PV(TV), after failure overlay" />
          </tr>
          <tr>
            <SpreadsheetCell value="Value of Equity (DCF)" type="label" />
            <SpreadsheetCell value={dec(data.dcf?.value_of_equity, 0)} type="calc" tooltip="V_op − debt − minority + cash_usable + cross_holdings" />
          </tr>
        </tbody>
      </SpreadsheetGrid>
    </div>
  );
}
