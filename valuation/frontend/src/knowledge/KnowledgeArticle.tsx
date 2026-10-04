import { lazy, Suspense, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useKnowledge } from './context';
import { safeKnowledgeUrl } from './urls';
import { KNOWLEDGE_BINDINGS, resolveLiveContext, resolveLiveValue } from './liveValues';
import type { KnowledgeDetail } from './types';

const LazyMarkdown = lazy(() => import('./KnowledgeMarkdown').then(module => ({ default: module.KnowledgeMarkdown })));
function MarkdownBlock({ children, onTerm }: { children: string; onTerm?: (id: string) => void }) {
  return <Suspense fallback={<p className="knowledge-muted" role="status">正在加载正文与公式…</p>}>
    <LazyMarkdown onTerm={onTerm}>{children}</LazyMarkdown>
  </Suspense>;
}

export function KnowledgeArticle({ termId, bindingId, onTerm }: { termId: string; bindingId?: string; onTerm?: (id: string) => void }) {
  const knowledge = useKnowledge();
  const client = knowledge?.client;
  const release = knowledge?.index?.release_id;
  const [result, setResult] = useState<{ termId: string; detail?: KnowledgeDetail; error?: string } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [chosenBinding, setChosenBinding] = useState<{ termId: string; id: string } | null>(null);
  useEffect(() => {
    if (!client) return;
    let active = true;
    client.getTerm(termId).then(detail => { if (active) setResult({ termId, detail }); })
      .catch((error: unknown) => { if (active) setResult({ termId, error: error instanceof Error ? error.message : '词条读取失败。' }); });
    return () => { active = false; };
  }, [client, termId, attempt, release]);
  if (!knowledge) return null;
  if (knowledge.indexError) return <div role="status"><p>{knowledge.indexError}</p><button type="button" className="knowledge-link" onClick={() => void knowledge.refreshIndex()}>重新检查词条库</button></div>;
  if (knowledge.index?.status === 'unavailable') return <KnowledgeEmpty />;
  if (!result || result.termId !== termId || (result.detail && release && result.detail.release_id !== release)) return <p className="knowledge-muted" role="status">正在读取词条…</p>;
  if (!result.detail) return <div role="status"><p>{result.error}</p><button type="button" className="knowledge-link" onClick={() => { setResult(null); setAttempt(value => value + 1); void knowledge.refreshIndex(); }}>重试</button></div>;
  const { term, bindings, relations, sources, release_id, release_label } = result.detail;
  const binding = bindings.find(item => item.binding_id === (bindingId ?? (chosenBinding?.termId === termId ? chosenBinding.id : bindings[0]?.binding_id))) ?? null;
  const knownBinding = binding && Object.hasOwn(KNOWLEDGE_BINDINGS, binding.binding_id)
    ? KNOWLEDGE_BINDINGS[binding.binding_id as keyof typeof KNOWLEDGE_BINDINGS] : null;
  const live = binding && knownBinding?.termId === term.term_id && knownBinding.fieldPath === binding.field_path
    ? resolveLiveValue(knowledge.valuation, binding.field_path) : null;
  const context = live && binding ? resolveLiveContext(knowledge.valuation, binding.field_path) : null;
  const valuation = knowledge.valuation;
  const course = relations.filter(relation => ['上一课', '下一课', '所属课程'].includes(relation.relation_type));
  return <article className="knowledge-article" data-knowledge-surface>
    <div className="knowledge-eyebrow">{term.category} · {term.kind}</div>
    <h2>{term.title_zh || term.title_en}</h2>
    {term.title_en && <p className="knowledge-subtitle">{term.title_en}</p>}
    <p className="knowledge-summary">{term.summary}</p>
    <section aria-label="通用定义与方法"><MarkdownBlock onTerm={onTerm}>{term.body_md}</MarkdownBlock></section>
    {binding && <section className="knowledge-usage"><h3>在估值页面中的使用</h3>
      {!bindingId && bindings.length > 1 && <label className="knowledge-binding-choice">关联页面字段<select value={binding.binding_id} onChange={event => setChosenBinding({ termId, id: event.target.value })}>{bindings.map(item => <option key={item.binding_id} value={item.binding_id}>{item.usage_md.split('\n')[0]}</option>)}</select></label>}
      <MarkdownBlock onTerm={onTerm}>{binding.usage_md}</MarkdownBlock>
      {binding.limitations_md && <><h4>适用条件与限制</h4><MarkdownBlock onTerm={onTerm}>{binding.limitations_md}</MarkdownBlock></>}
      {binding.reviewed_engine_ref && <p className="knowledge-muted">对应实现版本：{binding.reviewed_engine_ref}</p>}
      {valuation ? live && <div className="knowledge-current"><span>当前估值 · {live.label}</span><strong>{live.value}</strong><small>来自当前估值会话；更新参数后同步更新。</small>
        {context && <>
          <p>{valuation.inputs.company_name || valuation.ticker || '公司未提供'} · {context.nature}</p>
          <p className="knowledge-muted">{valuation.inputs.prepared_ttm ? `财务TTM：${valuation.inputs.prepared_ttm.period_start} 至 ${valuation.inputs.prepared_ttm.period_end}` : `标准TTM期间未提供；年度报告期：${valuation.inputs.period_date_10k || '未提供'}`}</p>
          <p className="knowledge-muted">信息截止：{valuation.inputs.prepared_ttm?.information_as_of || '未提供'}；报告币种：{valuation.inputs.reporting_currency || '未提供'}</p>
          <details><summary>本次输入、计算分支与来源</summary>
            <p>以下均为当前响应原值；相关值不一定都由本次分支使用，请结合所列分支阅读。覆盖输入为空不表示实际计算使用零，来源记录不认证假设的经济合理性。</p>
            <dl className="knowledge-context-values">{context.components.map(value => <div key={value.path}><dt>{value.label}</dt><dd>{value.value}</dd></div>)}{context.branches.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
            <p>行业参考：{valuation.inputs.industry_data?.industry_name || '未提供'}；地域：{valuation.inputs.industry_data?.region || '未提供'}</p>
            <p>参考快照：{valuation.reference_snapshot?.id || '未提供'}；参考信息截止：{valuation.reference_snapshot?.information_as_of || '未提供'}</p>
            <p>估值会话：{valuation.id || '未提供'}</p>
            <p>{context.source}</p>
          </details>
          {!!valuation.warnings?.length && <details><summary>本次估值提示与限制</summary><ul>{valuation.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul></details>}
        </>}
      </div> : <p className="knowledge-muted">选择公司后可查看此词条关联的当前估值数值。</p>}
    </section>}
    {!!course.length && <nav className="knowledge-course" aria-label="课程导航">{course.map(relation => onTerm
      ? <button key={`${relation.term_id}-${relation.relation_type}`} type="button" className="knowledge-link" onClick={() => onTerm(relation.term_id)}>{relation.relation_type}：{relation.title_zh}</button>
      : <Link key={`${relation.term_id}-${relation.relation_type}`} to={`/knowledge/${relation.term_id}`}>{relation.relation_type}：{relation.title_zh}</Link>)}</nav>}
    {!!relations.length && <section><h3>相关词条</h3><ul className="knowledge-related">{relations.map((relation, i) => <li key={`${relation.term_id}-${relation.relation_type}-${i}`}>
      {onTerm ? <button type="button" className="knowledge-link" onClick={() => onTerm(relation.term_id)}>{relation.title_zh || relation.title_en}</button> : <Link to={`/knowledge/${relation.term_id}`}>{relation.title_zh || relation.title_en}</Link>}
      <span className="knowledge-muted"> · {relation.relation_type}</span>
    </li>)}</ul></section>}
    {!!sources.length && <section><h3>来源与核验</h3><ol className="knowledge-sources">{sources.map((source, i) => <li key={`${source.source_id}-${i}`}>
      {safeKnowledgeUrl(source.url) ? <a href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a> : source.title}
      {source.author && <span> — {source.author}</span>}
      {source.locator && <div>{source.locator}</div>}
      {source.section_anchor && <div>对应章节：{source.section_anchor}</div>}
      {(source.published_at || source.verified_at) && <small>{source.published_at && `发布：${source.published_at} `}{source.verified_at && `核验：${source.verified_at}`}</small>}
    </li>)}</ol></section>}
    <footer className="knowledge-muted">词条版本：{release_label || release_id}</footer>
  </article>;
}

export function KnowledgeEmpty() {
  const knowledge = useKnowledge();
  return <div className="knowledge-empty" role="status"><h2>词条库尚未发布</h2><p>估值词汇与方法解释将在词条内容准备完成后提供。</p><p>您仍可继续使用估值页面。</p>{knowledge && <button type="button" className="knowledge-link" onClick={() => void knowledge.refreshIndex()}>重新检查词条库</button>}</div>;
}
