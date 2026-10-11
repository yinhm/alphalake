import { Link } from 'react-router-dom';
import { useId, useState, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';

import { isKnowledgeImage, safeKnowledgeUrl } from './urls';

export function KnowledgeMarkdown({ children, onTerm, presentation = false }: { children: string; onTerm?: (id: string) => void; presentation?: boolean }) {
  const prefix = `knowledge-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const sections: { title: string; markdown: string }[] = [{ title: '', markdown: '' }];
  let fence: string | null = null;
  for (const line of children.split('\n')) {
    const marker = /^\s{0,3}(`{3,}|~{3,})/.exec(line)?.[1];
    if (marker) {
      if (!fence) fence = marker;
      else if (marker[0] === fence[0] && marker.length >= fence.length) fence = null;
    }
    const heading = !fence && /^##\s+(.+?)\s*#*\s*$/.exec(line);
    if (heading) sections.push({ title: heading[1], markdown: '' });
    sections[sections.length - 1].markdown += `${line}\n`;
  }
  const render = (text: string) => <ReactMarkdown
    skipHtml
    remarkPlugins={[remarkMath]}
    rehypePlugins={[[rehypeKatex, { trust: false, strict: 'warn', throwOnError: false, maxExpand: 1000 }]]}
    urlTransform={url => safeKnowledgeUrl(url) ?? ''}
    disallowedElements={['iframe', 'script', 'style', 'object', 'embed', 'form', 'input']}
    components={{ img: ({ src, alt }) => src && isKnowledgeImage(src)
      ? <img src={src} alt={alt ?? ''} loading="lazy" /> : null, a: ({ href, children: label }) => {
      if (!href || !safeKnowledgeUrl(href)) return <span>{label}</span>;
      const termLink = /^\/knowledge\/([a-z0-9][a-z0-9._-]*)$/.exec(href);
      if (href.startsWith('/knowledge') && !(termLink && onTerm)) return <Link to={href}>{label}</Link>;
      return <a href={href} rel="noopener noreferrer" target={href.startsWith('http') ? '_blank' : undefined} onClick={termLink && onTerm ? event => { event.preventDefault(); onTerm(termLink[1]); } : undefined}>{label}</a>;
    } }}
  >{text}</ReactMarkdown>;
  if (presentation) return <KnowledgeSlides sections={sections.filter(section => section.markdown.trim())} render={render} />;
  return <div className="knowledge-markdown">
    {sections.filter(section => section.title).length >= 3 && <details className="knowledge-outline"><summary>本文目录</summary><nav aria-label="本文目录"><ul>{sections.map((section, i) => section.title && <li key={i}><button type="button" className="knowledge-link" onClick={() => {
      const target = document.getElementById(`${prefix}-${i}`);
      const folded = target?.closest('details');
      if (folded) folded.open = true;
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ block: 'start' });
    }}>{section.title}</button></li>)}</ul></nav></details>}
    {sections.map((section, i) => section.title === '答案与解析'
      ? <details key={i} className="knowledge-answer"><summary>展开答案与解析</summary><div id={`${prefix}-${i}`} tabIndex={-1}>{render(section.markdown)}</div></details>
      : <div key={i} id={`${prefix}-${i}`} tabIndex={-1}>{render(section.markdown)}</div>)}
  </div>;
}

function KnowledgeSlides({ sections, render }: { sections: { title: string; markdown: string }[]; render: (text: string) => ReactNode }) {
  const [page, setPage] = useState(0);
  const move = (value: number) => setPage(Math.max(0, Math.min(sections.length - 1, value)));
  if (!sections.length) return null;
  return <section className="knowledge-slides" aria-label="中文HTML幻灯片" tabIndex={0} onKeyDown={event => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || (event.target as HTMLElement).closest('input, textarea, select, [contenteditable="true"]')) return;
    const targets: Record<string, number> = { ArrowLeft: page - 1, ArrowRight: page + 1, Home: 0, End: sections.length - 1 };
    const target = targets[event.key];
    if (target !== undefined) { event.preventDefault(); move(target); }
  }}>
    <nav className="knowledge-slide-controls" aria-label="幻灯片导航">
      <button type="button" className="knowledge-link" disabled={page === 0} onClick={() => move(page - 1)}>上一页</button>
      <label>跳转到<select aria-label="选择幻灯片" value={page} onChange={event => move(Number(event.target.value))}>
        {sections.map((section, i) => <option key={i} value={i}>{i + 1}. {section.title || '导读'}</option>)}
      </select></label>
      <button type="button" className="knowledge-link" disabled={page === sections.length - 1} onClick={() => move(page + 1)}>下一页</button>
    </nav>
    <p className="knowledge-muted" aria-live="polite">第{page + 1}页 / 共{sections.length}页 · 可用左右方向键翻页</p>
    <div className="knowledge-slide-body knowledge-markdown">{render(sections[page].markdown)}</div>
  </section>;
}
