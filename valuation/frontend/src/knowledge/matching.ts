import type { KnowledgeTermSummary } from './types';

export function normalizeAlias(value: string): string {
  return value.normalize('NFKC').trim().replace(/\s+/gu, ' ').toLowerCase();
}

export function matchTerms(text: string, terms: KnowledgeTermSummary[], annotatedTermId?: string): KnowledgeTermSummary[] {
  if (annotatedTermId) return terms.filter(term => term.term_id === annotatedTermId);
  const normalized = normalizeAlias(text);
  if (!normalized) return [];
  return terms.filter(term => [term.title_zh, term.title_en, ...term.aliases].some(alias => normalizeAlias(alias) === normalized));
}

export const SELECTION_IGNORE = 'input, textarea, select, button, [contenteditable]:not([contenteditable="false"]), [data-knowledge-ignore], [data-knowledge-editable="true"], dialog, [data-knowledge-surface]';
const BLOCK = 'td, th, p, li, h1, h2, h3, h4, h5, h6, blockquote, pre, div, section, article, header';
const elementOf = (node: Node | null): Element | null => node?.nodeType === 1 ? node as Element : node?.parentElement ?? null;

export function readSelection(selection: Selection | null): { text: string; termId?: string; bindingId?: string; rect: DOMRect } | null {
  if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return null;
  const text = selection.toString().trim();
  if (!text || text.length > 80 || /^[\s\d.,%+−\-()/¥$€:：]+$/u.test(text) || /[\r\n]/u.test(text)) return null;
  const range = selection.getRangeAt(0);
  const start = elementOf(range.startContainer);
  const end = elementOf(range.endContainer);
  if (!start || !end || !start.closest('[data-knowledge-scope]') || !end.closest('[data-knowledge-scope]') || start.closest(SELECTION_IGNORE) || end.closest(SELECTION_IGNORE)) return null;
  // A range containing an edit control must also be ignored, even if its endpoints are outside it.
  if (range.cloneContents().querySelector(SELECTION_IGNORE)) return null;
  const startCell = start.closest('td, th');
  const endCell = end.closest('td, th');
  if (startCell !== endCell) return null;
  const annotation = start.closest<HTMLElement>('[data-term-id]');
  if (annotation && annotation === end.closest('[data-term-id]')) {
    return { text, termId: annotation.dataset.termId, bindingId: annotation.dataset.bindingId, rect: range.getBoundingClientRect() };
  }
  if (start.closest(BLOCK) !== end.closest(BLOCK)) return null;
  return { text, rect: range.getBoundingClientRect() };
}

export function searchTerms(query: string, terms: KnowledgeTermSummary[]): KnowledgeTermSummary[] {
  const q = normalizeAlias(query);
  if (!q) return terms;
  return terms.filter(term => normalizeAlias([term.title_zh, term.title_en, term.category, ...term.aliases].join(' ')).includes(q));
}
