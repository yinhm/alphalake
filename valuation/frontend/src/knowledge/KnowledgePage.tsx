import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useKnowledge } from './context';
import { KnowledgeArticle, KnowledgeEmpty } from './KnowledgeArticle';
import { searchTerms } from './matching';
import type { KnowledgeTermSummary } from './types';

const lessons = ['lesson-financials', 'lesson-cashflow', 'lesson-growth', 'lesson-risk', 'lesson-terminal', 'lesson-equity', 'lesson-full-dcf'];
const categories = [
  { title: '财务报表', description: '先分清期末余额、期间流量与报表之间的联系。', terms: ['balance-sheet', 'income-statement', 'cash-flow-statement'] },
  { title: '利润与现金流', description: '会计利润怎样变成可以分配给资本提供者的现金？', terms: ['ebit', 'ebitda', 'fcff'] },
  { title: '增长与资本效率', description: '把收入、利润率和支持增长的投入一起考虑。', terms: ['revenue-forecasting', 'margin-forecasting', 'sales-to-capital', 'roic'] },
  { title: '风险与折现', description: '现金属于谁、用什么货币计量，决定怎样匹配资本成本。', terms: ['wacc', 'beta', 'cost-of-equity'] },
  { title: '终值与成熟状态', description: '增长放缓之后，风险、回报与再投资仍要相互一致。', terms: ['terminal-value', 'stable-growth', 'terminal-reinvestment'] },
  { title: '股权与每股价值', description: '从经营价值扣除其他索取权，再看普通股能分到多少。', terms: ['enterprise-value', 'equity-value', 'dilution'] },
];
const topics = [
  { id: 'foundations-finance', description: '达摩达兰免费金融基础课：原课中文译文、HTML幻灯片、官方视频与测验。' },
  { id: 'accounting-course', description: '达摩达兰免费会计课：三张报表、会计调整与财务比率，含原课实例和测验。' },
  { id: 'learning-practice', description: '通过概念对照、制造与研发案例、自测，把零散词条连接起来。' },
  { id: 'valuation-strategy-selection', description: '周期、亏损、扩产与有限寿命等经济特征，为什么需要不同处理？' },
  { id: 'forecast-assumptions', description: '学习怎样为增长、利润率和资本效率寻找依据、检验候选。' },
  { id: 'initial-valuation-assumptions', description: '读懂本系统的初始条件、来源与限制，再决定如何修改参数。' },
  { id: 'currency-wacc', description: '区分币种与业务风险，理解人民币及其他货币的折现要求。' },
  { id: 'guide-valuation-checklist', description: '拿到估值结果后，依次检查输入、现金流、终值与股权归属。' },
];

function KnowledgeHome({ terms }: { terms: KnowledgeTermSummary[] }) {
  const available = new Map(terms.map(term => [term.term_id, term]));
  const publishedLessons = lessons.filter(id => available.has(id));
  const publishedCategories = categories.filter(category => category.terms.some(id => available.has(id)));
  const publishedTopics = topics.filter(topic => available.has(topic.id));
  const termLink = (id: string) => {
    const term = available.get(id);
    return term ? <Link to={`/knowledge/${id}`}>{term.title_zh || term.title_en}</Link> : null;
  };
  return <div className="knowledge-home">
    {(publishedLessons.length > 0 || available.has('learning-roadmap')) && <section aria-labelledby="knowledge-learning-title">
      <h2 id="knowledge-learning-title">估值自学路线</h2>
      <p>第一次学习，建议按顺序阅读：理解报表，构建现金流，再判断预测、风险和股权归属。</p>
      {available.has('learning-roadmap') && <p className="knowledge-link">{termLink('learning-roadmap')}</p>}
      <ol className="knowledge-learning-path">{publishedLessons.map(id => <li key={id}>{termLink(id)}</li>)}</ol>
    </section>}
    {publishedCategories.length > 0 && <section aria-labelledby="knowledge-categories-title">
      <h2 id="knowledge-categories-title">按主题查阅</h2>
      <div className="knowledge-home-grid">{publishedCategories.map(category => <nav key={category.title} className="knowledge-home-card" aria-label={category.title}>
        <h3>{category.title}</h3><p>{category.description}</p>
        <ul>{category.terms.filter(id => available.has(id)).map(id => <li key={id}>{termLink(id)}</li>)}</ul>
      </nav>)}</div>
    </section>}
    {publishedTopics.length > 0 && <section aria-labelledby="knowledge-topics-title">
      <h2 id="knowledge-topics-title">专题与实践</h2>
      <div className="knowledge-home-grid">{publishedTopics.map(topic => <div key={topic.id} className="knowledge-home-card">
        <h3>{termLink(topic.id)}</h3><p>{topic.description}</p>
      </div>)}</div>
    </section>}
  </div>;
}

export function KnowledgePage() {
  const knowledge = useKnowledge();
  const { termId } = useParams<{ termId: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const pageRef = useRef<HTMLDivElement>(null);
  const articleRef = useRef<HTMLDivElement>(null);
  useEffect(() => { (articleRef.current ?? pageRef.current)?.scrollIntoView({ block: 'start' }); }, [termId]);
  if (!knowledge) return null;
  const { index, indexError, refreshIndex } = knowledge;
  const matches = searchTerms(query, index?.terms ?? []);
  const presentation = searchParams.get('view') === 'slides' && index?.terms.some(term => term.term_id === termId && term.kind === '课程讲义');
  return <div ref={pageRef} className="knowledge-page">
    <header className="knowledge-page-header"><p className="knowledge-eyebrow">ALPHALAKE · 估值知识</p><h1>{termId ? '估值词汇与方法' : '估值知识库'}</h1><p>从报表到每股价值，学习生意怎样创造现金、增长需要多少投入，以及风险如何影响今天的价值。</p>
      {!termId && index?.status === 'ready' && <a href="#knowledge-search" className="knowledge-link">已有问题？直接搜索词条 →</a>}
    </header>
    {indexError ? <div role="status" className="knowledge-empty"><p>{indexError}</p><button type="button" className="knowledge-link" onClick={() => void refreshIndex()}>重试读取词条库</button></div>
      : !index ? <p role="status">正在读取词条目录…</p>
      : index.status === 'unavailable' ? <KnowledgeEmpty />
      : <div className={`knowledge-directory${termId ? ' has-article' : index.terms.length > 0 ? ' has-home' : ''}${presentation ? ' has-slides' : ''}`}>
        {!termId && index.terms.length > 0 && <KnowledgeHome terms={index.terms} />}
        <section className="knowledge-directory-list" aria-label="词条目录"><label htmlFor="knowledge-search">搜索词条</label>
          <input id="knowledge-search" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="中文、英文或缩写" autoComplete="off" />
          {termId && index.terms.some(term => term.term_id === 'learning-roadmap') && <p><Link to="/knowledge/learning-roadmap">从估值自学路线开始 →</Link></p>}
          <p className="knowledge-muted">{matches.length} 个词条{matches.length > 10 && '，显示前10条；请细化搜索或通过词条链接阅读。'}</p>
          {index.terms.length === 0 ? <p>此版本尚未收录词条。</p> : matches.length === 0 ? <p role="status">没有匹配的词条，请尝试其他名称。</p> : <ul>{matches.slice(0, 10).map(term => <li key={term.term_id}><Link to={`/knowledge/${term.term_id}`} aria-current={term.term_id === termId ? 'page' : undefined}>
            <strong>{term.title_zh || term.title_en}</strong>{term.title_en && <span>{term.title_en}</span>}<p>{term.summary}</p>
          </Link></li>)}</ul>}
        </section>
        {termId && <div ref={articleRef} className="knowledge-page-article"><Link to="/knowledge" className="knowledge-back">← 知识库首页</Link><KnowledgeArticle termId={termId} presentation={Boolean(presentation)} onTerm={id => navigate(`/knowledge/${id}${presentation && index.terms.some(term => term.term_id === id && term.kind === '课程讲义') ? '?view=slides' : ''}`)} /></div>}
      </div>}
  </div>;
}
