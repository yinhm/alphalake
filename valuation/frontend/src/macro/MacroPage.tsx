import { useEffect, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { BarChart, Bar, CartesianGrid, LineChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from 'recharts';
import { apiErrorMessage, fetchMacro, fetchMacroCatalog, fetchMacroComparison } from '../api/client';
import type { MacroCatalog, MacroPoint, MacroPublication, MacroSeries, MacroComparison } from '../types/macro';
import './macro.css';

const SOURCE: Record<string, string> = { nbs: '国家统计局', pbc: '中国人民银行', mof: '财政部' };
const BASIS: Record<string, string> = { month: '当月', month_or_jan_feb: '当月／1—2月合并', quarter: '当季', ytd: '年初累计', point_in_time: '期末' };
const PRICE: Record<string, string> = { current_prices: '现价／名义', constant_prices: '不变价／实际', price_change: '价格变动', not_applicable: '不适用' };
const COLOR = ['#0f766e', '#315b9a', '#b66c19', '#836296'];
const CHART_LABEL: Record<string, string> = {
  cn_cpi_yoy: 'CPI', cn_ppi_yoy: 'PPI', cn_ppi_input_yoy: '购进价格',
  cn_fixed_asset_investment_ytd_yoy: '固定资产投资', cn_private_investment_ytd_yoy: '民间投资',
  cn_equipment_investment_ytd_yoy: '设备购置', cn_property_investment_yoy: '房地产开发投资',
  cn_property_sales_area_yoy: '销售面积', cn_property_new_starts_yoy: '新开工', cn_property_completions_yoy: '竣工', cn_property_developer_funding_yoy: '开发资金',
  cn_capacity_industrial: '工业总体', cn_capacity_manufacturing: '制造业', cn_capacity_mining: '采矿业', cn_capacity_utilities: '公用事业',
};
const HEADLINES = ['cn_gdp_real_yoy', 'cn_pmi_manufacturing', 'cn_industrial_value_added_yoy', 'cn_retail_sales_yoy',
  'cn_cpi_yoy', 'cn_ppi_yoy', 'cn_afre_stock_yoy', 'cn_property_sales_yoy'];
const CHARTS = [
  { title: '增长动能', subtitle: '实际GDP同比 · 最近12季', codes: ['cn_gdp_real_yoy'], mode: 'line' },
  { title: '生产与订单', subtitle: 'PMI指数点 · 50为扩张／收缩分界', codes: ['cn_pmi_manufacturing', 'cn_pmi_production', 'cn_pmi_new_orders'], mode: 'line', benchmark: 50 },
  { title: '生产与消费的月度变化', subtitle: '季调环比 · %，并非同比', codes: ['cn_industrial_value_added_mom_sa', 'cn_retail_sales_mom_sa'], mode: 'line', benchmark: 0 },
  { title: '信用结构', subtitle: '期末余额官方同比 · %', codes: ['cn_afre_stock_yoy', 'cn_afre_rmb_loans_stock_yoy', 'cn_afre_government_bonds_stock_yoy'], mode: 'line' },
  { title: '价格传导', subtitle: '各指标最新月同比 · 横截面对比，篮子不同', codes: ['cn_cpi_yoy', 'cn_ppi_yoy', 'cn_ppi_input_yoy'], mode: 'bar', benchmark: 0 },
  { title: '投资与地产需求', subtitle: '各指标最新年初累计同比 · %', codes: ['cn_fixed_asset_investment_ytd_yoy', 'cn_private_investment_ytd_yoy', 'cn_equipment_investment_ytd_yoy', 'cn_property_investment_yoy'], mode: 'bar', benchmark: 0 },
  { title: '融资从哪里来', subtitle: '当月增量 · 万亿元，负值保留', codes: ['cn_afre_flow', 'cn_afre_government_bonds_flow', 'cn_afre_rmb_loans_flow'], mode: 'line', benchmark: 0 },
  { title: '投入与销售价格扩散', subtitle: '制造业PMI指数点 · 非企业利润率', codes: ['cn_pmi_input_price', 'cn_pmi_output_price'], mode: 'line', benchmark: 50 },
  { title: '地产链的不同环节', subtitle: '各指标最新累计同比 · %，不可相加', codes: ['cn_property_sales_area_yoy', 'cn_property_new_starts_yoy', 'cn_property_completions_yoy', 'cn_property_developer_funding_yoy'], mode: 'bar', benchmark: 0 },
  { title: '产能是否被有效使用', subtitle: '各组最新当季产能利用率 · %，不相加、不等同ROIC', codes: ['cn_capacity_industrial', 'cn_capacity_manufacturing', 'cn_capacity_mining', 'cn_capacity_utilities'], mode: 'bar' },
  { title: '出口订单的边际变化', subtitle: '制造业新出口订单PMI · 指数点，不是贸易额增速', codes: ['cn_pmi_export_orders'], mode: 'line', benchmark: 50 },
];

function periodLabel(s: MacroSeries, p?: MacroPoint) {
  if (!p) return '暂无期间';
  const [year, month] = p.period_end.split('-');
  const end = s.frequency === 'quarterly' ? `${year}Q${Number(month) / 3}` : `${year}-${month}`;
  const basis = s.period_basis === 'month_or_jan_feb'
    ? p.period_start.slice(5, 7) === '01' && month === '02' ? '1—2月合并' : '当月'
    : BASIS[s.period_basis] ?? s.period_basis;
  return `${end} · ${basis}`;
}

function scale(s: Pick<MacroSeries, 'unit'>) { return s.unit === 'fraction' ? 100 : s.unit === 'CNY' ? 1e-12 : s.unit === 'square_metre' ? 1e-8 : 1; }
function unit(s: Pick<MacroSeries, 'unit'>) { return ({ fraction: '%', index_points: '点', percentage_points: '个百分点', CNY: '万亿元', CNY_per_person: '元／人', square_metre: '亿平方米' } as Record<string, string>)[s.unit] ?? s.unit; }
function valueLabel(s: Pick<MacroSeries, 'unit'>, p?: Pick<MacroPoint, 'value'>) {
  if (!p || p.value === null) return '未列报';
  return `${(Number(p.value) * scale(s)).toLocaleString('zh-CN', { maximumFractionDigits: 2, minimumFractionDigits: s.unit === 'CNY_per_person' ? 0 : 1 })}${unit(s)}`;
}

function priorChange(s: MacroSeries) {
  const a = s.points.at(-2), b = s.points.at(-1);
  if (!a || !b || a.value === null || b.value === null || !['fraction', 'index_points', 'percentage_points'].includes(s.unit)) return null;
  const month = (d: string) => Number(d.slice(0, 4)) * 12 + Number(d.slice(5, 7));
  if (month(b.period_end) - month(a.period_end) !== (s.frequency === 'quarterly' ? 3 : 1)) return null;
  if (s.period_basis === 'ytd' && a.period_end.slice(0, 4) !== b.period_end.slice(0, 4)) return null;
  if (s.period_basis === 'month_or_jan_feb' && month(a.period_end) - month(a.period_start) !== month(b.period_end) - month(b.period_start)) return null;
  const delta = (Number(b.value) - Number(a.value)) * scale(s);
  return `较上期同口径 ${delta > 0 ? '+' : ''}${delta.toFixed(2)}${s.unit === 'index_points' ? '点' : '个百分点'}`;
}

function chartRows(series: MacroSeries[], limit = 12) {
  const days = [...new Set(series.flatMap(s => s.points.map(p => p.period_end)))].sort().slice(-limit);
  return days.map(day => Object.fromEntries([['date', Date.parse(day + 'T00:00:00Z')], ...series.map(s => {
    const p = s.points.find(p => p.period_end === day);
    return [s.series_code, p?.value == null ? null : Number(p.value) * scale(s)];
  })]));
}

function Chart({ title, subtitle, series, mode = 'line', benchmark }: {
  title: string; subtitle: string; series: MacroSeries[]; mode?: string; benchmark?: number;
}) {
  const rows = chartRows(series);
  const bars = series.map(s => ({ name: CHART_LABEL[s.series_code] ?? s.name_zh, value: s.points.at(-1)?.value == null ? null : Number(s.points.at(-1)!.value) * scale(s), period: s.points.at(-1)?.period_end }));
  const labelDate = (stamp: number) => new Date(stamp).toISOString().slice(0, 7);
  const body = <>
    <CartesianGrid vertical={false} stroke="#e5eaf0" />
    <YAxis width={50} domain={mode === 'bar' ? ([low, high]: readonly [number, number]) => [Math.min(0, low), Math.max(0, high)] : undefined} tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} />
    <Tooltip formatter={v => v == null ? '未列报' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}
      labelFormatter={v => typeof v === 'number' ? labelDate(v) : String(v)} contentStyle={{ borderRadius: 8, border: '1px solid #dde5ec', fontSize: 12 }} />
    {benchmark !== undefined && <ReferenceLine y={benchmark} stroke="#8898aa" strokeDasharray="4 4" />}
  </>;
  return <section className="macro-chart" aria-label={title}>
    <h3>{title}</h3><p className="macro-caption">{subtitle}</p>
    {!rows.length ? <p>该期暂无已发布数据。</p> : <>
      <div className="macro-plot" role="img" aria-label={`${title}图表，完整数值见下方数据表`}>
        <ResponsiveContainer width="100%" height={250} minWidth={0}>
          {mode === 'bar' ? <BarChart data={bars} margin={{ top: 15, right: 12, left: 0, bottom: 10 }} accessibilityLayer>
            {body}<XAxis dataKey="name" tick={{ fontSize: 10, fill: '#64748b' }} interval={0} axisLine={false} tickLine={false} />
            <Bar dataKey="value" name={series[0] ? unit(series[0]) : '值'} fill={COLOR[0]} maxBarSize={45} radius={[3, 3, 0, 0]} isAnimationActive={false} />
          </BarChart> : <LineChart data={rows} margin={{ top: 15, right: 16, left: 0, bottom: 0 }} accessibilityLayer>
            {body}<XAxis dataKey="date" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={labelDate} minTickGap={35} tick={{ fontSize: 10, fill: '#64748b' }} axisLine={false} tickLine={false} />
            <Legend wrapperStyle={{ fontSize: 11, paddingTop: 12 }} />
            {series.map((s, i) => <Line key={s.series_code} dataKey={s.series_code} name={s.name_zh} stroke={COLOR[i % COLOR.length]} strokeWidth={2.2} type="linear" dot={rows.length === 1} connectNulls={false} isAnimationActive={false} />)}
          </LineChart>}
        </ResponsiveContainer>
      </div>
      {rows.length === 1 && <p className="macro-caption">仅有一期观测，不能据此判断趋势。</p>}
      <details className="macro-chart-data"><summary>查看图表数据与期间</summary><div className="macro-table-wrap"><table><thead><tr><th>指标</th><th>统计期</th><th>数值</th></tr></thead><tbody>
        {series.flatMap(s => s.points.filter(p => mode === 'bar' ? p === s.points.at(-1) : rows.some(r => r.date === Date.parse(p.period_end + 'T00:00:00Z'))).map(p => <tr key={s.series_code + p.period_end}><td>{s.name_zh}</td><td>{periodLabel(s, p)}</td><td>{valueLabel(s, p)}</td></tr>))}
      </tbody></table></div></details>
    </>}
  </section>;
}

const CHANGE_KIND: Record<string, string> = { new_period: '新统计期', coverage_added: '新增覆盖', historical_added: '补充历史', value_revised: '同期间数值修订', scope_changed: '统计区间变更', source_changed: '仅来源更新', removed_observation: '当前投影未含', definition_changed: '定义变更' };

function revisionLabel(row: MacroComparison['changes'][number]) {
  if (row.delta === null) return '不作数值差分';
  const value = Number(row.delta) * scale(row);
  const label = value !== 0 && Math.abs(value) < 1e-6 ? value.toExponential(3) : value.toLocaleString('zh-CN', { maximumFractionDigits: 6 });
  return label + (row.unit === 'fraction' ? '个百分点' : unit(row));
}

function PublicationComparison({ page, catalog }: { page: MacroPublication; catalog: MacroCatalog }) {
  const available = catalog.publications.slice(catalog.publications.findIndex(p => p.id === page.id) + 1);
  const [chosen, setChosen] = useState(page.previous_id ?? '');
  const [offset, setOffset] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const key = JSON.stringify([chosen, page.id, attempt]);
  const [result, setResult] = useState<{ key: string; value?: MacroComparison; error?: string } | null>(null);
  const value = result?.key === key ? result.value : null;
  const error = result?.key === key ? result.error : null;
  useEffect(() => {
    if (!chosen) return;
    const controller = new AbortController();
    fetchMacroComparison(chosen, page.id, controller.signal).then(value => {
      if (!controller.signal.aborted) setResult({ key, value });
    }).catch(error => { if (!controller.signal.aborted) setResult({ key, error: apiErrorMessage(error) }); });
    return () => controller.abort();
  }, [chosen, page.id, key]);
  return <section className="macro-comparison" aria-label="本期变化">
    <div className="macro-section-heading"><h2>本期究竟变了什么</h2>{available.length > 0 && <label>比较基准<select aria-label="宏观比较基准" value={chosen} onChange={e => { setChosen(e.target.value); setOffset(0); }}>
      <option value="">选择较早期次</option>{available.map(p => <option key={p.id} value={p.id}>{p.title} · {p.id}</option>)}
    </select></label>}</div>
    {!chosen ? <p className="macro-caption">{available.length ? '选择基准后比较两个固定发布。' : '这是首个发布，尚无更早页面可比较。'}</p>
      : error ? <div role="alert"><p>{error}</p><button onClick={() => setAttempt(n => n + 1)}>重试比较</button></div>
      : !value ? <p role="status">正在核对发布变化…</p> : <>
        <p className="macro-caption"><Link to={`/macro/${value.before.id}`}>基准 {value.before.id}</Link> → 本期 {value.after.id} · {value.commentary_changed ? '点评有更新' : '点评未变'}</p>
        <div className="macro-change-counts">{Object.entries(CHANGE_KIND).map(([kind, name]) => <span key={kind}>{name}<strong>{value.summary[kind] ?? 0}</strong></span>)}</div>
        {!value.total ? <p>未检测到标准数值、统计范围或来源内容变化。解读更新不代表经济数据又改善了一次。</p> : <>
          <div className="macro-table-wrap"><table><thead><tr><th>指标／统计期</th><th>变化类别</th><th>基准值</th><th>本期值</th><th>修订差</th></tr></thead><tbody>{value.changes.slice(offset, offset + 10).map(row => <tr key={row.series_code + row.period_end}>
            <td>{row.name_zh}<small className="macro-source-hash">{row.period_end ?? `口径：${row.changed_fields.join('、')}`}</small></td><td>{CHANGE_KIND[row.kind]}</td>
            <td>{row.before ? <a href={row.before.source_url} target="_blank" rel="noopener noreferrer">{valueLabel(row, row.before)}</a> : '无该观测'}</td>
            <td>{row.after ? <a href={row.after.source_url} target="_blank" rel="noopener noreferrer">{valueLabel(row, row.after)}</a> : '无该观测'}</td>
            <td>{revisionLabel(row)}</td>
          </tr>)}</tbody></table></div>
          <div className="macro-pagination"><button disabled={!offset} onClick={() => setOffset(offset - 10)}>上一组变化</button><span>{offset + 1}—{Math.min(offset + 10, value.total)} / {value.total}</span><button disabled={offset + 10 >= value.total} onClick={() => setOffset(offset + 10)}>下一组变化</button></div>
        </>}
        <p className="macro-caption">这是发布之间的记录比较；同期间修订差不是经济环比，新增覆盖不是景气改善。“当前投影未含”不等于官方撤回。定义变更暂停数值比较；实际来源与期间以固定原页为准。</p>
      </>}
  </section>;
}

function Dashboard({ page, current, catalog }: { page: MacroPublication; current: boolean; catalog: MacroCatalog }) {
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState('');
  const series = page.snapshot.series;
  const byCode = (code: string) => series.find(s => s.series_code === code);
  const filtered = series.filter(s => `${s.name_zh} ${s.series_code} ${SOURCE[s.source]}`.toLowerCase().includes(query.toLowerCase().trim()));
  const selectedSeries = byCode(selected);
  const showSeries = (code: string) => {
    setSelected(code);
    window.setTimeout(() => document.getElementById('macro-selected')?.scrollIntoView({ block: 'start' }), 0);
  };
  return <>
    <header className="macro-hero"><div className="macro-kicker">CHINA · MACRO OBSERVATORY</div>
      <p className="macro-caption">{current ? '最新已发布' : '固定历史期次'} · {new Date(page.published_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}（北京时间）</p>
      <h1>{page.commentary.title}</h1><p className="macro-summary">{page.commentary.summary}</p>
      <div className="macro-meta"><span>{series.length}个标准系列</span><span>{page.snapshot.releases.length}个来源版本</span><span>数据导出：{new Date(page.snapshot.exported_at).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' })}</span></div>
      <p className="macro-caption">各指标统计期独立列示；当前来源修订版本，不代表历史当时可见数据。</p>
    </header>
    <PublicationComparison page={page} catalog={catalog} />
    <section aria-label="重要宏观指标" className="macro-cards">{HEADLINES.map(code => {
      const s = byCode(code), p = s?.points.at(-1);
      return <article key={code} className="macro-card"><p>{s?.name_zh ?? code}</p><strong>{s ? valueLabel(s, p) : '尚未接入'}</strong>
        <span>{s ? periodLabel(s, p) : '来源缺项'}</span><small>{s ? priorChange(s) ?? '暂无相邻可比前值' : ''}</small>
      </article>;
    })}</section>
    <section className="macro-commentary" aria-label="数据点评"><div className="macro-section-heading"><h2>本期观察与解读</h2><span>{page.commentary.author} · 审核 {page.commentary.reviewed_at.slice(0, 10)}</span></div>
      <nav className="macro-topics" aria-label="本期研究导航">{page.commentary.sections.map((section, i) => <a key={section.title} href={`#macro-observation-${i}`}>{section.title}</a>)}</nav>
      <div className="macro-notes">{page.commentary.sections.map((section, i) => <article id={`macro-observation-${i}`} key={section.title}><span className="macro-note-number">{String(i + 1).padStart(2, '0')}</span><h3>{section.title}</h3>
        <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml disallowedElements={['img', 'script', 'style', 'iframe', 'form', 'input']}
          components={{ table: ({ children }) => <div className="macro-table-wrap"><table>{children}</table></div>, a: ({ href, children }) => href && /^\/(research|knowledge)\/[a-z0-9][a-z0-9-]*$/.test(href) ? <Link to={href}>{children}</Link> : href?.startsWith('https://') ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span> }}>{section.body}</ReactMarkdown>
        <details><summary>核对支撑数据（{section.evidence.length}项）</summary><ul>{section.evidence.map(e => {
          const s = byCode(e.series_code), p = s?.points.find(p => p.period_end === e.period_end);
          const release = page.snapshot.releases.find(r => r.release_id === p?.release_id);
          return <li key={e.series_code + e.period_end}>{s?.name_zh} · {e.period_end} · {s ? valueLabel(s, p) : '缺项'} {release && <a href={release.url} target="_blank" rel="noopener noreferrer">原文</a>} <button onClick={() => showSeries(e.series_code)}>指标历史</button></li>;
        })}</ul></details>
      </article>)}</div>
    </section>
    <div className="macro-section-heading"><h2>关键趋势与结构</h2><span>缺项不补零，单期观测不画成历史趋势</span></div>
    <div className="macro-charts">{CHARTS.map(chart => <Chart key={chart.title} title={chart.title} subtitle={chart.subtitle} mode={chart.mode} benchmark={chart.benchmark} series={chart.codes.map(byCode).filter((s): s is MacroSeries => !!s)} />)}</div>
    <section className="macro-dictionary" aria-label="指标检索"><div className="macro-section-heading"><h2>指标与历史数据</h2><label>搜索指标<input aria-label="搜索宏观指标" value={query} onChange={e => { setQuery(e.target.value); setOffset(0); }} placeholder="例如：社会融资、可支配收入、PMI" /></label></div>
      <p className="macro-caption">找到{filtered.length}项，每页10项。点击指标名称查看全部已存历史及口径。</p>
      <div className="macro-table-wrap"><table><thead><tr><th>指标</th><th>最新统计期</th><th>数值</th><th>来源</th></tr></thead><tbody>{filtered.slice(offset, offset + 10).map(s => <tr key={s.series_code}>
        <td><button type="button" onClick={() => showSeries(s.series_code)}>{s.name_zh}</button></td><td>{periodLabel(s, s.points.at(-1))}</td><td>{valueLabel(s, s.points.at(-1))}</td><td>{SOURCE[s.source]}</td>
      </tr>)}</tbody></table></div>
      {!filtered.length && <p>没有匹配的标准指标。</p>}
      <div className="macro-pagination"><button disabled={!offset} onClick={() => setOffset(offset - 10)}>上一页</button><span>{filtered.length ? `${offset + 1}—${Math.min(offset + 10, filtered.length)}` : '0'} / {filtered.length}</span><button disabled={offset + 10 >= filtered.length} onClick={() => setOffset(offset + 10)}>下一页</button></div>
      {selectedSeries && <div className="macro-selected" id="macro-selected" aria-live="polite"><div className="macro-section-heading"><h3>{selectedSeries.name_zh} · 历史与定义</h3><button onClick={() => setSelected('')}>收起指标</button></div>
        <p>{selectedSeries.definition}</p><p className="macro-caption">{BASIS[selectedSeries.period_basis]} · {PRICE[selectedSeries.price_basis]} · {selectedSeries.seasonal_adjustment === 'seasonally_adjusted' ? '已季调' : selectedSeries.seasonal_adjustment === 'not_seasonally_adjusted' ? '未季调' : '目录未标季调'} · 标准名称 {selectedSeries.series_code}</p>
        <Chart title={selectedSeries.name_zh} subtitle={`单位：${unit(selectedSeries)} · 图示最近12期，完整历史见下表`} series={[selectedSeries]} />
        <div className="macro-table-wrap"><table><thead><tr><th>统计区间</th><th>数值</th><th>原始来源</th></tr></thead><tbody>{selectedSeries.points.map(p => {
          const r = page.snapshot.releases.find(r => r.release_id === p.release_id);
          return <tr key={p.period_end}><td>{p.period_start} — {p.period_end}</td><td>{valueLabel(selectedSeries, p)}</td><td>{r && <a href={r.url} target="_blank" rel="noopener noreferrer">{SOURCE[r.source]} · 版本{r.release_id}</a>}</td></tr>;
        })}</tbody></table></div>
      </div>}
    </section>
    <section className="macro-sources"><h2>来源与阅读边界</h2><ul>{page.commentary.limitations.map(text => <li key={text}>{text}</li>)}</ul>
      <details><summary>查看全部来源版本与取得时间</summary><div className="macro-table-wrap"><table><thead><tr><th>官方发布</th><th>最新统计期</th><th>原文发布时间</th><th>首次取得</th></tr></thead><tbody>{page.snapshot.releases.map(r => <tr key={r.release_id}>
        <td><a href={r.url} target="_blank" rel="noopener noreferrer">{SOURCE[r.source]} · {r.dataset.replace(/^macro_|_v\d+$/g, '')}</a><small className="macro-source-hash">SHA-256 {r.sha256}</small></td>
        <td>{series.flatMap(s => s.points.filter(p => p.release_id === r.release_id).map(p => p.period_end)).sort().at(-1)}</td>
        <td>{r.source_published_at ? new Date(r.source_published_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '原文未标明'}</td>
        <td>{new Date(r.first_acquired_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}</td>
      </tr>)}</tbody></table></div></details>
      <p className="macro-caption">本期ID：{page.id} · 内容版本：{page.content_hash.slice(0, 16)} · 宏观观察不自动修改公司估值参数。</p>
    </section>
  </>;
}

export default function MacroPage() {
  const { id } = useParams<{ id: string }>();
  const history = useLocation().pathname === '/macro/history';
  const [attempt, setAttempt] = useState(0);
  const requestKey = JSON.stringify([id, history, attempt]);
  const [result, setResult] = useState<{ key: string; catalog?: MacroCatalog; page?: MacroPublication; error?: string } | null>(null);
  const catalog = result?.key === requestKey ? result.catalog : null;
  const page = result?.key === requestKey ? result.page : null;
  const error = result?.key === requestKey ? result.error : null;
  const loading = result?.key !== requestKey;
  useEffect(() => {
    const controller = new AbortController();
    fetchMacroCatalog(controller.signal).then(async index => {
      if (controller.signal.aborted) return;
      const page = !history && (id || index.current_id) ? await fetchMacro(id, controller.signal) : undefined;
      if (!controller.signal.aborted) setResult({ key: requestKey, catalog: index, page });
    }).catch(e => { if (!controller.signal.aborted) setResult({ key: requestKey, error: apiErrorMessage(e) }); });
    return () => controller.abort();
  }, [id, history, requestKey]);
  return <div className="macro-page"><nav aria-label="宏观页面" className="macro-navigation"><Link to="/macro">宏观概览</Link><Link to="/macro/history">历史发布</Link>{page && <Link to={`/macro/${page.id}`}>本期固定链接</Link>}</nav>
    {loading ? <p role="status">正在读取宏观发布…</p> : error ? <div className="macro-error" role="alert"><p>{error}</p><button onClick={() => setAttempt(attempt + 1)}>重试读取</button></div> : history ? <>
      <header className="macro-hero"><div className="macro-kicker">PUBLICATION ARCHIVE</div><h1>宏观历史发布</h1><p>每期保留当时发布的数据与点评。历史曲线采用该期来源版本，不能当作严格历史PIT。</p></header>
      {!catalog?.publications.length ? <p>尚无已发布宏观页面。</p> : <div className="macro-archive">{catalog.publications.map(entry => <article key={entry.id}><p className="macro-caption">发布 {new Date(entry.published_at).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' })} · {entry.id === catalog.current_id ? '当前期' : '历史期'}</p><h2><Link to={`/macro/${entry.id}`}>{entry.title}</Link></h2><p>{entry.summary}</p><small>{entry.series_count}个系列 · {entry.observation_count}条观测 · 各系列最新统计期最晚至{entry.latest_statistical_period}</small></article>)}</div>}
    </> : page && catalog ? <Dashboard key={page.id} page={page} current={!id} catalog={catalog} /> : <div className="macro-hero"><h1>宏观概览</h1><p>尚未发布宏观阅读页。数据入库后，需要核对点评并发布阅读快照。</p></div>}
  </div>;
}
