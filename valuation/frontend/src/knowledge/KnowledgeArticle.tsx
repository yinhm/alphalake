import { lazy, Suspense, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useKnowledge } from './context';
import { safeKnowledgeUrl } from './urls';
import { KNOWLEDGE_BINDINGS, resolveLiveValue } from './liveValues';
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
  const binding = bindingId ? bindings.find(item => item.binding_id === bindingId) : null;
  const knownBinding = binding && Object.hasOwn(KNOWLEDGE_BINDINGS, binding.binding_id)
    ? KNOWLEDGE_BINDINGS[binding.binding_id as keyof typeof KNOWLEDGE_BINDINGS] : null;
  const live = binding && knownBinding?.termId === term.term_id && knownBinding.fieldPath === binding.field_path
    ? resolveLiveValue(knowledge.valuation, binding.field_path) : null;
  return <article className="knowledge-article" data-knowledge-surface>
    <div className="knowledge-eyebrow">{term.category} · {term.kind}</div>
    <h2>{term.title_zh || term.title_en}</h2>
    {term.title_en && <p className="knowledge-subtitle">{term.title_en}</p>}
    <p className="knowledge-summary">{term.summary}</p>
    <section aria-label="通用定义与方法"><MarkdownBlock onTerm={onTerm}>{term.body_md}</MarkdownBlock></section>
    {binding && <section className="knowledge-usage"><h3>在估值页面中的使用</h3>
      <MarkdownBlock onTerm={onTerm}>{binding.usage_md}</MarkdownBlock>
      {binding.limitations_md && <><h4>适用条件与限制</h4><MarkdownBlock onTerm={onTerm}>{binding.limitations_md}</MarkdownBlock></>}
      {binding.reviewed_engine_ref && <p className="knowledge-muted">对应实现版本：{binding.reviewed_engine_ref}</p>}
      {knowledge.valuation ? live && <div className="knowledge-current"><span>当前估值 · {live.label}</span><strong>{live.value}</strong><small>来自当前估值会话；更新参数后同步更新。</small></div> : <p className="knowledge-muted">选择公司后可查看此词条关联的当前估值数值。</p>}
    </section>}
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
