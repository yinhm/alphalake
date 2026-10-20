import type { ResearchDetail } from '../types/research';

// Virtual paths resolve Markdown relatives; only registered files become actual HTTP links.
export function researchLink(href: string, detail: ResearchDetail): string | undefined {
  if (/^https?:\/\//i.test(href) || /^#[^\s]*$/.test(href)
      || /^\/knowledge(?:\/|$|\?|#)/.test(href)
      || /^\/research(?:\/[a-z0-9][a-z0-9-]{0,79})?(?:[?#][^\s]*)?$/.test(href)) return href;
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('//')) return undefined;
  try {
    const resolved = new URL(href, `https://research.invalid/${detail.document.path}`);
    if (resolved.origin !== 'https://research.invalid') return undefined;
    const file = detail.files.find(item => item.available && item.path === decodeURIComponent(resolved.pathname.slice(1)));
    if (!file) return undefined;
    const main = /^research\/([a-z0-9][a-z0-9-]{0,79})\/report\.md$/.exec(file.path);
    if (main && main[1] !== detail.report.id) return `/research/${main[1]}${resolved.hash}`;
    return file.path.endsWith('.md')
      ? `/research/${detail.report.id}?document=${file.id}${resolved.hash}`
      : file.url + resolved.hash;
  } catch {
    return undefined;
  }
}

