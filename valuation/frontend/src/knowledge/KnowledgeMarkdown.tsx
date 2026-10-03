import { Link } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';

import { safeKnowledgeUrl } from './urls';

export function KnowledgeMarkdown({ children, onTerm }: { children: string; onTerm?: (id: string) => void }) {
  return <div className="knowledge-markdown"><ReactMarkdown
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
  >{children}</ReactMarkdown></div>;
}
