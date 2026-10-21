import { Fragment, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import { apiErrorMessage, fetchScreenCatalog, fetchScreenResults } from '../api/client';
import type { ScreenCatalog, ScreenResults, ScreenRow } from '../types/screen';
import './screen.css';

const money = (value: number | null) => value === null ? '—' : value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const percent = (value: number | null, scale = 100) => value === null ? '—' : `${money(value * scale)}%`;
const date = (value: string | null) => value ?? '未知';
const fields: Record<string, string> = {
  revenues: '营业收入', ebit: '息税前利润', bv_debt: '账面债务', cash_and_marketable_securities: '现金与有价证券',
  cross_holdings: '金融投资', minority_interests: '少数股东权益', mv_equity_listing: '挂牌市值', shares_outstanding: '总股本',
  r_and_d_expense: '研发费用', r_and_d_history: '历史研发',
};
const ranges = [
  { key: 'difference', label: '描述性价差（%）', suffix: '_pct', scale: 1 },
  { key: 'wacc', label: '初始WACC（%）', suffix: '_rate', scale: 100 },
  { key: 'growth', label: '首年收入增长（%）', suffix: '_rate', scale: 100 },
  { key: 'margin', label: '首年调整利润率（%）', suffix: '_rate', scale: 100 },
  { key: 'capital_ratio', label: '首年资金安排资本倍率（倍）', suffix: '', scale: 1 },
];
const extraColumns = [
  ['first_year_growth_rate', '首年增长'], ['first_year_adjusted_margin_rate', '首年调整利润率'],
  ['first_year_funding_sales_to_capital', '首年资金安排资本倍率'], ['terminal_growth_rate', '永续增长'],
  ['terminal_wacc_rate', '稳态WACC'], ['terminal_pv_share_rate', '终值现值占经营价值'],
];

export default function ScreenPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const dataset = params.get('dataset') ?? '';
  const [attempt, setAttempt] = useState(0);
  const [catalog, setCatalog] = useState<ScreenCatalog | null>(null);
  const [catalogError, setCatalogError] = useState('');
  const [result, setResult] = useState<{ key: string; data?: ScreenResults; error?: string } | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const immediate = useRef(false);
  const query = new URLSearchParams(params);
  query.delete('dataset'); query.delete('assumptions');
  const queryString = query.toString();
  const requestKey = `${dataset}?${queryString}#${attempt}`;
  const data = result?.data?.dataset.id === dataset ? result.data : undefined;
  const error = result?.key === requestKey ? result.error : undefined;
  const pending = !!dataset && result?.key !== requestKey;
  const showAssumptions = params.get('assumptions') === '1';

  useEffect(() => {
    const controller = new AbortController();
    void fetchScreenCatalog(controller.signal).then(body => {
      if (!controller.signal.aborted) { setCatalog(body); setCatalogError(''); }
    }).catch(reason => { if (!controller.signal.aborted) setCatalogError(apiErrorMessage(reason)); });
    return () => controller.abort();
  }, [attempt]);

  useEffect(() => {
    if (!dataset && catalog?.current) {
      setParams(previous => { const next = new URLSearchParams(previous); next.set('dataset', catalog.current!); return next; }, { replace: true });
    }
  }, [catalog, dataset, setParams]);

  useEffect(() => {
    if (!dataset) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void fetchScreenResults(dataset, new URLSearchParams(queryString), controller.signal).then(body => {
        if (!controller.signal.aborted) setResult({ key: requestKey, data: body });
      }).catch(reason => {
        if (!controller.signal.aborted) setResult({ key: requestKey, error: apiErrorMessage(reason) });
      });
    }, immediate.current ? 0 : 250);
    immediate.current = false;
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [dataset, queryString, requestKey]);

  useEffect(() => {
    if (result?.key === requestKey && data && data.offset !== Number(params.get('offset') ?? 0)) {
      setParams(previous => { const next = new URLSearchParams(previous); next.set('offset', String(data.offset)); return next; }, { replace: true });
    }
  }, [data, params, setParams, requestKey, result?.key]);

  const update = (key: string, value: string | null, page = false) => {
    const next = new URLSearchParams(params);
    if (value === null || value === '') next.delete(key); else next.set(key, value);
    if (!page && key !== 'assumptions') next.delete('offset');
    setParams(next);
  };
  const toggle = (key: string, value: string) => {
    const selected = params.getAll(key);
    const next = new URLSearchParams(params); next.delete(key); next.delete('offset');
    (selected.includes(value) ? selected.filter(item => item !== value) : [...selected, value]).forEach(item => next.append(key, item));
    setParams(next);
  };
  const sort = (key: string) => {
    const next = new URLSearchParams(params);
    next.set('sort', key); next.set('direction', params.get('sort') === key && params.get('direction') !== 'desc' ? 'desc' : 'asc');
    next.delete('offset'); setParams(next);
  };
  const heading = (key: string, title: string, className = '') => {
    const active = (params.get('sort') ?? 'ticker') === key;
    const descending = params.get('direction') === 'desc';
    return <th className={className} scope="col" aria-sort={active ? descending ? 'descending' : 'ascending' : undefined}>
      <button type="button" onClick={() => sort(key)}>{title}<span aria-hidden="true"> {active ? descending ? '↓' : '↑' : '↕'}</span></button>
    </th>;
  };

  return <section className="screen-page">
    <header className="screen-header"><p className="screen-eyebrow">ALPHALAKE · SCREEN</p><h1>选股</h1>
      <p>按行业、条件价值与经营假设筛选研究候选，再阅读公司报告。已计算不等于预测获得经济认证。</p></header>
    <div className="screen-toolbar">
      <label>研究批次<select value={dataset} onChange={event => update('dataset', event.target.value)}>
        {!dataset && <option value="">选择已发布批次</option>}
        {dataset && !catalog?.datasets.some(item => item.id === dataset) && <option value={dataset}>{dataset}</option>}
        {catalog?.datasets.map(item => <option key={item.id} value={item.id}>{item.research_date ?? '日期未知'} · {item.title}</option>)}
      </select></label>
      <form role="search" onSubmit={event => { event.preventDefault(); immediate.current = true; setAttempt(value => value + 1); }}>
        <label>搜索公司<input type="search" value={params.get('q') ?? ''} maxLength={160}
          placeholder="公司名、六位代码或完整证券代码" onChange={event => update('q', event.target.value)} /></label>
        <button type="submit">搜索</button>
      </form>
      <button type="button" onClick={() => setParams(dataset ? { dataset } : {})}>重置筛选</button>
    </div>
    {catalogError && <div className="screen-message" role="alert">{catalogError}<button onClick={() => setAttempt(value => value + 1)}>重试读取</button></div>}
    {!dataset && !catalogError && <p role="status">{catalog ? catalog.datasets.length ? '请选择已发布的研究批次。' : '尚未发布估值结果表。' : '正在读取批次…'} <Link to="/research">阅读研究报告</Link></p>}
    {error && <div className="screen-message" role="alert">{error}<button onClick={() => setAttempt(value => value + 1)}>重试读取</button></div>}
    {pending && <p role="status">{data ? '正在更新筛选；下表仍为上次已完成查询结果。' : '正在读取选股结果…'}</p>}
    {data && <>
      <div className="screen-snapshot">
        <dl><div><dt>财务基期</dt><dd>{data.dataset.financial_period}</dd></div>
          <div><dt>股本基期（家数）</dt><dd>{Object.entries(data.dataset.share_dates).map(([key, count]) => `${key === 'unknown' ? '未知' : key}（${count}）`).join('；')}</dd></div>
          <div><dt>报价交易日（家数）</dt><dd>{Object.entries(data.dataset.quote_dates).map(([key, count]) => `${key === 'unknown' ? '无合格报价' : key}（${count}）`).join('；')}</dd></div>
          <div><dt>财务输入信息截止</dt><dd>{data.dataset.information_as_of}</dd></div>
          <div><dt>研究冻结</dt><dd>{data.dataset.research_frozen_at}</dd></div></dl>
        <p>批次：{data.dataset.id} · {data.dataset.research_date ?? '研究日期未知'}。{data.dataset.condition_type}。此表沿冻结研究快照，财务库刷新不会自动重估；资本基期代理价差不代表预期收益。</p>
        <div className="screen-links"><Link to={data.dataset.report_url}>阅读批次总览</Link><a href={data.dataset.csv_url}>下载完整批次CSV</a></div>
      </div>
      <div className="screen-counts" aria-label="完整批次状态">
        <span>全部 <strong>{data.universe_count.toLocaleString()}</strong></span>
        {Object.entries(data.labels.admission).map(([key, title]) => <button key={key} type="button" aria-pressed={params.getAll('admission').includes(key)}
          onClick={() => toggle('admission', key)}>{title} <strong>{(data.admission_counts[key] ?? 0).toLocaleString()}</strong></button>)}
      </div>
      <div className="screen-filters">
        {(['industry', 'admission', 'flag', 'comparison'] as const).map(key => <details key={key}>
          <summary>{{ industry: '估值参考行业', admission: '计算状态', flag: '审阅提示', comparison: '价格比较状态' }[key]}{params.getAll(key).length ? `（${params.getAll(key).length}）` : ''}</summary>
          <div className="screen-options">{Object.entries(data.facets[key]).sort(([a], [b]) => a.localeCompare(b)).map(([value, count]) => <label key={value}>
            <input type="checkbox" checked={params.getAll(key).includes(value)} onChange={() => toggle(key, value)} />
            {key === 'industry' ? value || '未知行业' : data.labels[key][value] ?? value}<small>{count}</small></label>)}</div>
        </details>)}
      </div>
      <details className="screen-ranges"><summary>数值范围筛选</summary>
        <form key={queryString} onSubmit={event => {
          event.preventDefault(); const form = new FormData(event.currentTarget); const next = new URLSearchParams(params);
          for (const range of ranges) for (const bound of ['min', 'max']) {
            const key = `${range.key}_${bound}${range.suffix}`; const value = String(form.get(key) ?? '').trim();
            if (value) next.set(key, String(Number(value) / range.scale)); else next.delete(key);
          }
          next.delete('offset'); setParams(next);
        }}>
          {ranges.map(range => <fieldset key={range.key}><legend>{range.label}</legend>{['min', 'max'].map(bound => {
            const key = `${range.key}_${bound}${range.suffix}`; const value = params.get(key);
            return <label key={key}>{bound === 'min' ? '下限' : '上限'}<input aria-label={`${range.label}${bound === 'min' ? '下限' : '上限'}`} name={key} type="number" step="any"
              defaultValue={value === null ? '' : Number(value) * range.scale} /></label>;
          })}</fieldset>)}<button type="submit">应用数值范围</button>
        </form>
      </details>
      {Object.keys(data.range_unknown_counts).length > 0 && <p className="screen-muted">各条件排除的未知值（重叠不相加）：{Object.entries(data.range_unknown_counts).map(([key, count]) => `${ranges.find(range => range.key === key)?.label ?? key} ${count}家`).join('；')}</p>}
      <div className="screen-table-tools"><p aria-live="polite">匹配 <strong>{data.filtered_count.toLocaleString()}</strong> 家 / 完整批次 {data.universe_count.toLocaleString()} 家<small className="block text-slate-500">金额单位：人民币元／股</small></p>
        <label><input type="checkbox" checked={showAssumptions} onChange={event => update('assumptions', event.target.checked ? '1' : null)} /> 显示关键假设</label>
        <label>每页<select value={params.get('limit') ?? '50'} onChange={event => update('limit', event.target.value)}>{[25, 50, 100].map(value => <option key={value}>{value}</option>)}</select></label>
      </div>
      {data.issues.map((item, index) => <p key={index} role="status">{item.message}</p>)}
      {!data.rows.length ? <p className="screen-message" role="status">没有匹配的公司，请调整或重置筛选条件。</p> : <div className="screen-scroll" tabIndex={0} role="region" aria-busy={pending} aria-label="选股结果表，可横向滚动">
        <table><caption className="sr-only">冻结批次条件估值。表头按钮可排序；金额为人民币元每股，非实时行情。</caption>
          <thead><tr>{heading('ticker', '公司／代码', 'screen-company')}<th scope="col" className="screen-industry">估值参考行业</th><th scope="col">计算状态</th>
            {heading('value', '每股条件价值')}{heading('close', '来源收盘价')}{heading('difference', '描述性价差')}{heading('initial_wacc_rate', '初始WACC', 'screen-wacc')}
            <th scope="col" className="screen-review">审阅提示</th>{showAssumptions && extraColumns.map(([key, title]) => <Fragment key={key}>{heading(key, title)}</Fragment>)}</tr></thead>
          <tbody>{data.rows.map(row => <Fragment key={row.ticker}><tr>
            <td className="screen-company"><div className="screen-company-heading"><Link to={row.url} state={{ screenReturn: location.pathname + location.search }}>{row.name}</Link>
              <button type="button" aria-expanded={expanded === row.ticker} onClick={() => setExpanded(expanded === row.ticker ? null : row.ticker)}>详情{expanded === row.ticker ? ' −' : ' ＋'}</button></div><small>{row.ticker}</small></td>
            <td className="screen-industry">{row.reference_industry ?? '未知'}</td><td>{data.labels.admission[row.admission]}</td>
            <td className={`screen-number ${row.condition_value_cny_per_share !== null && row.condition_value_cny_per_share <= 0 ? 'screen-caution' : ''}`}>{money(row.condition_value_cny_per_share)}</td>
            <td className="screen-number">{money(row.close_cny)}<small>{date(row.close_date)}{row.close_date && row.close_date < Object.keys(data.dataset.quote_dates).filter(key => key !== 'unknown').sort().at(-1)! ? ' · 较旧报价' : ''}</small></td>
            <td className="screen-number">{percent(row.descriptive_difference_pct, 1)}<small title={row.comparison_display_reason}>{data.labels.comparison[row.comparison_display_status]}</small></td>
            <td className="screen-number screen-wacc" title={row.assumption_missing_reasons.initial_wacc_rate}>{percent(row.assumptions.initial_wacc_rate)}</td>
            <td className="screen-review">{row.review_flags.slice(0, 2).map(flag => <span className="screen-badge" key={flag}>{data.labels.flag[flag]}</span>)}{row.review_flags.length > 2 && <button onClick={() => setExpanded(row.ticker)}>另{row.review_flags.length - 2}项</button>}{!row.review_flags.length && '—'}</td>
            {showAssumptions && extraColumns.map(([key]) => <td className="screen-number" key={key} title={row.assumption_missing_reasons[key]}>{key === 'first_year_funding_sales_to_capital' ? money(row.assumptions[key]) : percent(row.assumptions[key])}</td>)}
          </tr>{expanded === row.ticker && <tr><td colSpan={8 + (showAssumptions ? extraColumns.length : 0)}><RowDetails row={row} data={data} /></td></tr>}</Fragment>)}</tbody>
        </table>
      </div>}
      <nav className="screen-pagination" aria-label="选股分页"><button disabled={data.offset === 0} onClick={() => update('offset', String(Math.max(0, data.offset - data.limit)), true)}>上一页</button>
        <span>第 {Math.floor(data.offset / data.limit) + 1} / {Math.max(1, Math.ceil(data.filtered_count / data.limit))} 页</span>
        <button disabled={data.offset + data.limit >= data.filtered_count} onClick={() => update('offset', String(data.offset + data.limit), true)}>下一页</button></nav>
    </>}
  </section>;
}

function RowDetails({ row, data }: { row: ScreenRow; data: ScreenResults }) {
  const location = useLocation();
  const facts = { 估值参考行业: row.reference_industry ?? '未知', 初始WACC: percent(row.assumptions.initial_wacc_rate),
    ...Object.fromEntries(extraColumns.map(([key, label]) => [label, key === 'first_year_funding_sales_to_capital' ? money(row.assumptions[key]) : percent(row.assumptions[key])])),
    财务基期: row.financial_period, 股本基期: date(row.share_date), 信息截止: row.information_as_of,
    研究冻结: row.research_frozen_at, 条件类型: row.mode ? data.dataset.condition_type : '未生成估值', 参考版本: data.dataset.reference_snapshot_id,
    计算版本: data.dataset.runtime_stages.calculation, 审阅版本: data.dataset.runtime_stages.review,
    'run ID': row.run_id, 模型文件哈希: row.model_source.sha256 };
  return <div className="screen-row-detail"><strong>{row.name} · 输入与适用边界</strong>
    <p>{data.labels.admission[row.admission]}{row.missing_fields.length > 0 ? `：${row.missing_fields.map(key => fields[key] ?? key).join('、')}` : ''}</p>
    <p>{row.comparison_display_reason}</p>
    {row.review_flags.length > 0 && <ul>{row.review_flags.map(flag => <li key={flag}>{data.labels.flag[flag]}</li>)}</ul>}
    {Object.entries(row.assumption_missing_reasons).length > 0 && <p>假设缺项：{Object.entries(row.assumption_missing_reasons).map(([key, reason]) => `${key === 'initial_wacc_rate' ? '初始WACC' : extraColumns.find(([id]) => id === key)?.[1] ?? key}：${reason}`).join('；')}</p>}
    <dl>{Object.entries(facts).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <p>原价格比较状态：{data.labels.comparison[row.price_comparison_status] ?? row.price_comparison_status}。{row.price_comparison_reason}</p>
    <Link to={row.url} state={{ screenReturn: location.pathname + location.search }}>阅读完整研究报告 →</Link>
  </div>;
}
