import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { apiErrorMessage, fetchResearch, fetchResearchCatalog } from '../api/client';
import { RESEARCH_KINDS, type ResearchCatalog, type ResearchDetail } from '../types/research';
import ResearchMarkdown from './ResearchMarkdown';
import { researchLink } from './urls';
import './research.css';

function RequestError({ message, retry }: { message: string; retry: () => void }) {
  return <div className="research-message" role="alert"><p>{message}</p><button type="button" onClick={retry}>重试读取</button></div>;
}

function ResearchList() {
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const kind = params.get('kind') ?? '';
  const value = Number(params.get('offset') ?? 0);
  const offset = Number.isSafeInteger(value) && value >= 0 ? value : 0;
  const [attempt, setAttempt] = useState(0);
  const requestKey = JSON.stringify([q, kind, offset, attempt]);
  const [result, setResult] = useState<{ key: string; catalog?: ResearchCatalog; error?: string } | null>(null);
  const catalog = result?.key === requestKey ? result.catalog : null;
  const error = result?.key === requestKey ? result.error : null;
  const pending = result?.key !== requestKey;
  useEffect(() => {
    const controller = new AbortController();
    void fetchResearchCatalog(q, kind, offset, controller.signal).then(result => {
      if (!controller.signal.aborted) setResult({ key: requestKey, catalog: result });
    }).catch(reason => {
      if (!controller.signal.aborted) setResult({ key: requestKey, error: apiErrorMessage(reason) });
    });
    return () => controller.abort();
  }, [q, kind, offset, requestKey]);
  const update = (query: string, category: string, page = 0) => {
    const next = new URLSearchParams();
    if (query) next.set('q', query);
    if (category) next.set('kind', category);
    if (page) next.set('offset', String(page));
    setParams(next);
  };
  return <div className="research-page">
    <header className="research-header"><p className="research-eyebrow">ALPHALAKE · RESEARCH</p><h1>研究报告</h1>
      <p>阅读公司研究、经营论点、条件估值及独立模型。每份报告保留原始时点、假设与限制。</p></header>
    <form className="research-filters" onSubmit={event => event.preventDefault()} role="search">
      <label>搜索研究<input type="search" value={q} onChange={event => update(event.target.value, kind)}
        placeholder="公司、证券代码或研究主题" maxLength={160} /></label>
      <label>研究类型<select value={kind} onChange={event => update(q, event.target.value)}>
        <option value="">全部类型</option>{Object.entries(RESEARCH_KINDS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select></label>
    </form>
    {error ? <RequestError message={error} retry={() => setAttempt(value => value + 1)} />
      : pending ? <p role="status">正在读取研究目录…</p>
      : catalog && <>
        {catalog.issues.length > 0 && <div className="research-message" role="status"><p>{catalog.issues.length}份研究登记异常，未作为有效报告展示：</p>
          <ul>{catalog.issues.map(item => <li key={item.id}>{item.id}：{item.message}</li>)}</ul></div>}
        <p className="research-muted" aria-live="polite">共{catalog.total}份研究</p>
        {catalog.reports.length === 0 ? <p className="research-message" role="status">{q || kind ? '没有匹配的研究，请调整搜索条件。' : '暂无已登记的研究报告。'}</p>
          : <ul className="research-cards">{catalog.reports.map(report => <li key={report.id}><article>
            <div className="research-card-meta"><span>{RESEARCH_KINDS[report.kind]}</span><span>{report.research_date ?? '研究日期未注明'}</span>
              {report.status !== 'complete' && <span>{report.status === 'partial' ? '部分交付' : '历史归档'}</span>}</div>
            <h2><Link to={`/research/${report.id}`}>{report.title}</Link></h2><p>{report.summary}</p>
            {report.tickers.length > 0 && <p className="research-muted">{report.tickers.join(' · ')}</p>}
          </article></li>)}</ul>}
        {catalog.total > 20 && <nav className="research-pagination" aria-label="研究分页">
          <button type="button" disabled={offset === 0} onClick={() => update(q, kind, Math.max(0, offset - 20))}>上一页</button>
          <span>第{Math.floor(offset / 20) + 1}页</span>
          <button type="button" disabled={offset + 20 >= catalog.total} onClick={() => update(q, kind, offset + 20)}>下一页</button>
        </nav>}
      </>}
  </div>;
}

function ResearchArticle({ id }: { id: string }) {
  const [params] = useSearchParams();
  const selected = params.get('document');
  const [detail, setDetail] = useState<ResearchDetail | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void fetchResearch(id, selected, controller.signal).then(result => {
      if (!controller.signal.aborted) { setDetail(result); setError(''); }
    }).catch(reason => { if (!controller.signal.aborted) setError(apiErrorMessage(reason)); });
    return () => controller.abort();
  }, [id, selected, attempt]);
  const current = detail?.report.id === id && detail.document.id === (selected || detail.report.report_file) ? detail : null;
  return <div className="research-page"><Link to="/research" className="research-back">← 返回研究目录</Link>
    {error ? <RequestError message={error} retry={() => setAttempt(value => value + 1)} />
      : !current ? <p role="status">正在读取研究报告…</p> : <>
        <header className="research-header"><p className="research-eyebrow">{RESEARCH_KINDS[current.report.kind]}</p><h1>{current.report.title}</h1>
          <p>{current.report.summary}</p></header>
        <dl className="research-facts">
          <div><dt>证券</dt><dd>{current.report.tickers.join(' · ') || '批量／专题研究'}</dd></div>
          <div><dt>研究日期</dt><dd>{current.report.research_date ?? '未注明'}</dd></div>
          <div><dt>财务基期</dt><dd>{current.report.financial_period ?? '未注明'}</dd></div>
          <div><dt>股本基期</dt><dd>{current.report.share_date ?? '未注明'}</dd></div>
          <div><dt>信息截止</dt><dd>{current.report.information_as_of ?? '未注明'}</dd></div>
        </dl>
        {current.report.run_ids.length > 0 && <details className="research-runs"><summary>可追溯运行（{current.report.run_ids.length}项）</summary>
          <ul>{current.report.run_ids.map(run => <li key={run}><code>{run}</code></li>)}</ul></details>}
        {current.report.limitations.length > 0 && <aside className="research-limitations" aria-label="研究适用边界"><strong>阅读边界</strong>
          <ul>{current.report.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul></aside>}
        {current.report.previous_id && <p><Link to={`/research/${current.report.previous_id}`}>阅读上一版研究</Link></p>}
        <nav className="research-documents" aria-label="报告与附文">{current.files.filter(file => file.path.endsWith('.md')).map(file => file.available
          ? <Link key={file.id} to={researchLink('/' + file.path, current)!} aria-current={file.id === current.document.id ? 'page' : undefined}>{file.label}</Link>
          : <span key={file.id}>{file.label}（不可用）</span>)}</nav>
        <article className="research-article" aria-label={current.document.label}><ResearchMarkdown detail={current} /></article>
        <details className="research-attachments"><summary>附件与来源（{current.files.length}项）</summary><ul>{current.files.map(file => <li key={file.id}>
          {file.available ? <a href={`${file.url}?download=true`}>{file.label}</a> : <span>{file.label}（文件不可用）</span>}
          <small>{file.available && file.size_bytes !== null ? ` · ${(file.size_bytes / 1024).toFixed(1)} KB` : ''}</small>
        </li>)}</ul></details>
      </>}
  </div>;
}

export default function ResearchPage() {
  const { id } = useParams<{ id: string }>();
  const [params] = useSearchParams();
  return id ? <ResearchArticle key={`${id}:${params.get('document') ?? ''}`} id={id} /> : <ResearchList />;
}
