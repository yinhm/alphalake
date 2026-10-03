import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useKnowledge } from './context';
import { KnowledgeArticle, KnowledgeEmpty } from './KnowledgeArticle';
import { searchTerms } from './matching';

export function KnowledgePage() {
  const knowledge = useKnowledge();
  const { termId } = useParams<{ termId: string }>();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const articleRef = useRef<HTMLDivElement>(null);
  useEffect(() => { articleRef.current?.scrollIntoView({ block: 'start' }); }, [termId]);
  if (!knowledge) return null;
  const { index, indexError, refreshIndex } = knowledge;
  return <div className="knowledge-page">
    <header className="knowledge-page-header"><p className="knowledge-eyebrow">ALPHALAKE · 估值知识</p><h1>估值词汇与方法</h1><p>阅读定义、公式、适用条件与原始来源。在估值页面选择词语，或点击词语旁的问号，即可随时查阅。</p></header>
    {indexError ? <div role="status" className="knowledge-empty"><p>{indexError}</p><button type="button" className="knowledge-link" onClick={() => void refreshIndex()}>重试读取词条库</button></div>
      : !index ? <p role="status">正在读取词条目录…</p>
      : index.status === 'unavailable' ? <KnowledgeEmpty />
      : <div className={`knowledge-directory${termId ? ' has-article' : ''}`}>
        <section className="knowledge-directory-list" aria-label="词条目录"><label htmlFor="knowledge-search">搜索词条</label>
          <input id="knowledge-search" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="中文、英文或缩写" autoComplete="off" />
          <p className="knowledge-muted">{searchTerms(query, index.terms).length} 个词条</p>
          {index.terms.length === 0 ? <p>此版本尚未收录词条。</p> : searchTerms(query, index.terms).length === 0 ? <p role="status">没有匹配的词条，请尝试其他名称。</p> : <ul>{searchTerms(query, index.terms).map(term => <li key={term.term_id}><Link to={`/knowledge/${term.term_id}`} aria-current={term.term_id === termId ? 'page' : undefined}>
            <strong>{term.title_zh || term.title_en}</strong>{term.title_en && <span>{term.title_en}</span>}<p>{term.summary}</p>
          </Link></li>)}</ul>}
        </section>
        {termId && <div ref={articleRef} className="knowledge-page-article"><Link to="/knowledge" className="knowledge-back">← 全部词条</Link><KnowledgeArticle termId={termId} onTerm={id => navigate(`/knowledge/${id}`)} /></div>}
      </div>}
  </div>;
}
