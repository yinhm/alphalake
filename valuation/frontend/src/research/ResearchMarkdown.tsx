import { Link } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import type { ResearchDetail } from '../types/research';
import { researchLink } from './urls';

export default function ResearchMarkdown({ detail }: { detail: ResearchDetail }) {
  return <div className="research-markdown"><ReactMarkdown skipHtml
    remarkPlugins={[remarkGfm, remarkMath]}
    rehypePlugins={[[rehypeKatex, { trust: false, strict: 'warn', throwOnError: false, maxExpand: 1000 }]]}
    urlTransform={url => researchLink(url, detail) ?? ''}
    disallowedElements={['script', 'style', 'iframe', 'object', 'embed', 'form', 'input']}
    components={{
      a: ({ href, children }) => !href ? <span title="该链接未登记为可阅读附件">{children}（链接未登记）</span>
        : /^\/research(?:\/|$|\?|#)/.test(href) || href.startsWith('/knowledge')
          ? <Link to={href}>{children}</Link>
          : <a href={href} target={href.startsWith('#') ? undefined : '_blank'} rel="noopener noreferrer">{children}</a>,
      img: ({ src, alt }) => {
        const file = detail.files.find(item => item.url === src && item.available
          && /^image\/(png|jpeg|webp)$/.test(item.media_type));
        return file ? <img src={file.url} alt={alt ?? ''} loading="lazy" /> : <span>（图片未登记：{alt}）</span>;
      },
    }}
  >{detail.document.body_md}</ReactMarkdown></div>;
}
