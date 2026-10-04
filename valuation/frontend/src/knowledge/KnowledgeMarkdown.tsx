import { Link } from 'react-router-dom';
import { useId } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';

import { safeKnowledgeUrl } from './urls';

export function KnowledgeMarkdown({ children, onTerm }: { children: string; onTerm?: (id: string) => void }) {
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
    disallowedElements={['img', 'iframe', 'script', 'style', 'object', 'embed', 'form', 'input']}
    components={{ a: ({ href, children: label }) => {
      if (!href || !safeKnowledgeUrl(href)) return <span>{label}</span>;
      const termLink = /^\/knowledge\/([a-z0-9][a-z0-9._-]*)$/.exec(href);
      if (href.startsWith('/knowledge') && !(termLink && onTerm)) return <Link to={href}>{label}</Link>;
      return <a href={href} rel="noopener noreferrer" target={href.startsWith('http') ? '_blank' : undefined} onClick={termLink && onTerm ? event => { event.preventDefault(); onTerm(termLink[1]); } : undefined}>{label}</a>;
    } }}
  >{text}</ReactMarkdown>;
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
