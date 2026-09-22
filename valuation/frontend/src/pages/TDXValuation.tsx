import { useState } from 'react';
import type { StandardFinancialRow, ValuationResponse } from '../types/valuation';

const number = (value: number | null | undefined) => value == null ? '缺失' : value.toLocaleString('zh-CN', { maximumFractionDigits: 4 });
const names: Record<string, string> = {
  balance_sheet: '资产负债表', income_statement: '利润表', cash_flow_statement: '现金流量表',
  cash_recovery_scenario: '现金回收情景', operating_cash_reserve: '经营现金保留',
  debt_book_proxy: '账面债务代理', minority_book_proxy: '少数股权账面代理', additional_claims_scenario: '附加索偿情景',
};

/** 专用标准财务展示：不套用CIQ缺值补零或10-K/10-Q的前端拆分。 */
export default function TDXValuation({ data }: { data: ValuationResponse }) {
  const [query, setQuery] = useState('');
  const source = data.alphalake!;
  function table(rows: StandardFinancialRow[]) {
    return <div className="overflow-auto max-h-96"><table className="w-full text-sm text-left">
      <thead><tr><th>标准字段</th><th>期间／类型</th><th>原单位数值</th><th>单位</th><th>状态</th></tr></thead>
      <tbody>{rows.filter(r => r.field.includes(query.trim()) || r.label?.includes(query.trim())).map((r, i) => <tr key={`${r.field}-${r.period}-${i}`} className="border-t">
        <td>{r.label ?? r.field}{r.label && <div className="text-xs text-gray-500">{r.field}</div>}</td><td>{r.balance_date ?? r.period ?? source.report_period} / {r.period_type ?? r.period_basis}</td>
        <td>{r.value ?? '缺失'}</td><td>{r.unit}</td><td>{r.status ?? 'available'}</td>
      </tr>)}</tbody>
    </table></div>;
  }
  return <div className="space-y-5">
    <section className="bg-white rounded border p-4">
      <h2 className="text-xl font-bold">条件估值：{number(data.final?.value_per_share)} 元／股</h2>
      <p>以下模型金额为百万元，股数为百万股；下方报表保留各字段原标准单位。</p>
      <p>TTM收入：{number(data.ltm_financials?.revenues)}；政策调整EBIT：{number(data.ltm_financials?.ebit)}</p>
      <p>经营资产价值：{number(data.dcf?.value_of_operating_assets)}；股权价值：{number(data.dcf?.value_of_equity)}</p>
      <h3 className="font-semibold mt-3">股权桥接（显式政策结果）</h3>
      <ul>{Object.entries(source.equity_bridge.components).map(([key, value]) => <li key={key}>{names[key] ?? key}：{number(value)}</li>)}</ul>
      <p>情景股数：{number(source.equity_bridge.shares)}</p>
    </section>
    <section className="bg-white rounded border p-4 overflow-auto">
      <h3 className="font-semibold">预测现金流（非报表事实）</h3>
      <table className="w-full text-sm text-left"><thead><tr><th>预测年</th><th>收入</th><th>EBIT</th><th>再投资</th><th>FCFF</th></tr></thead>
        <tbody>{data.dcf?.revenue_projections.map((value, i) => <tr key={i} className="border-t">
          <td>{i + 1}</td><td>{number(value)}</td><td>{number(data.dcf?.ebit_projections[i])}</td>
          <td>{number(data.dcf?.reinvestment_projections[i])}</td><td>{number(data.dcf?.fcff_projections[i])}</td>
        </tr>)}</tbody>
      </table>
    </section>
    <section className="bg-white rounded border p-4 space-y-3">
      <h3 className="font-semibold">TDX标准财务数据</h3>
      <p>源零歧义、未审核和缺项保持原状态；不按模型需要补零。报表范围沿用供应商口径，未逐公司认证合并范围；余额项显示实际余额日。</p>
      <label className="block">筛选标准字段 <input className="border rounded px-2 py-1" value={query} onChange={e => setQuery(e.target.value)} /></label>
      {Object.entries(source.financial_statements.statements).map(([key, rows]) => <details key={key} open>
        <summary className="font-semibold">{names[key] ?? key}（{rows.length}项）</summary>{table(rows)}
      </details>)}
      <details><summary>全部已导出的历史标准事实（{source.standard_financials.length}条）</summary>{table(source.standard_financials)}</details>
    </section>
    {data.warnings.length > 0 && <details><summary>计算诊断</summary><ul>{data.warnings.map((text, i) => <li key={i}>{text}</li>)}</ul></details>}
  </div>;
}
