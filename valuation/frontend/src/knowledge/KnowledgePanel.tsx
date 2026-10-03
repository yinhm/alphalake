import { useEffect, useRef, useSyncExternalStore } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { Link } from 'react-router-dom';
import { useKnowledge } from './context';
import { KnowledgeArticle } from './KnowledgeArticle';

const QUERY = '(min-width: 1600px)';
const subscribe = (notify: () => void) => {
  const media = window.matchMedia(QUERY);
  media.addEventListener('change', notify);
  return () => media.removeEventListener('change', notify);
};
const isWide = () => window.matchMedia(QUERY).matches;

function containDialogFocus(event: ReactKeyboardEvent<HTMLDialogElement>) {
  if (event.key !== 'Tab') return;
  const elements = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'))
    .filter(element => element.getClientRects().length > 0);
  const first = elements[0];
  const last = elements[elements.length - 1];
  if (!first) { event.preventDefault(); return; }
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}


export function KnowledgePanel() {
  const knowledge = useKnowledge();
  const wide = useSyncExternalStore(subscribe, isWide, () => false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const termId = knowledge?.selected?.termId;
  const closePanelAction = knowledge?.closePanel;
  const opened = knowledge?.panelOpen ?? false;
  useEffect(() => {
    if (!opened) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    if (!wide && dialog) dialog.showModal();
    closeRef.current?.focus({ preventScroll: true });
    const escape = (event: KeyboardEvent) => { if (wide && event.key === 'Escape') closePanelAction?.(); };
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('keydown', escape);
      dialog?.close();
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, [wide, opened, closePanelAction]);
  useEffect(() => { bodyRef.current?.scrollTo({ top: 0 }); }, [termId]);
  if (!knowledge?.panelOpen || !knowledge.selected) return null;
  const { selected, closePanel, openTerm } = knowledge;
  const content = <div className="knowledge-panel-inner">
    <header className="knowledge-panel-header"><span>估值词条</span><div>
      <Link to={`/knowledge/${selected.termId}`} onClick={closePanel} aria-label="打开完整词条页">完整页面 ↗</Link>
      <button ref={closeRef} type="button" className="knowledge-close" aria-label="关闭词条解释" onClick={closePanel}>×</button>
    </div></header>
    <div ref={bodyRef} className="knowledge-panel-body"><KnowledgeArticle termId={selected.termId} bindingId={selected.bindingId} onTerm={openTerm} /></div>
  </div>;
  return wide
    ? <aside className="knowledge-panel" data-knowledge-surface aria-label="估值词条解释">{content}</aside>
    : <dialog ref={dialogRef} className="knowledge-dialog" data-knowledge-surface aria-label="估值词条解释" onKeyDown={containDialogFocus} onCancel={event => { event.preventDefault(); closePanel(); }} onClick={event => { if (event.target === event.currentTarget) closePanel(); }}>{content}</dialog>;
}
