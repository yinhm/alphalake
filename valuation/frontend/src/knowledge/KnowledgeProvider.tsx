import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { ValuationResponse } from '../types/valuation';
import { KnowledgeClient } from './client';
import { KnowledgeContext } from './context';
import { matchTerms, readSelection } from './matching';
import type { KnowledgeIndex, KnowledgeTermSummary } from './types';
import './knowledge.css';

const PREFERENCE = 'alphalake.knowledge.selection-enabled';
type Preview = { terms: KnowledgeTermSummary[]; bindingId?: string; top: number; left: number };

export function KnowledgeProvider({ children, valuation }: { children: ReactNode; valuation: ValuationResponse | null }) {
  const [client] = useState(() => new KnowledgeClient());
  const [index, setIndex] = useState<KnowledgeIndex | null>(null);
  const [indexError, setIndexError] = useState<string | null>(null);
  const [selected, setSelected] = useState<{ termId: string; bindingId?: string } | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const previewRef = useRef<HTMLDivElement>(null);
  const [selectionEnabled, setEnabled] = useState(() => {
    try { return localStorage.getItem(PREFERENCE) !== 'false'; } catch { return true; }
  });
  const refreshIndex = useCallback(async () => {
    try { setIndex(await client.getIndex()); setIndexError(null); }
    catch (error) { setIndexError(error instanceof Error ? error.message : '词条库暂时无法读取。'); setPreview(null); }
  }, [client]);
  useEffect(() => {
    const unsubscribe = client.subscribe(next => { setIndex(next); setIndexError(null); setPreview(null); });
    const refresh = () => { if (document.visibilityState !== 'hidden') void refreshIndex(); };
    refresh();
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => { unsubscribe(); window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', refresh); };
  }, [client, refreshIndex]);
  const setSelectionEnabled = useCallback((enabled: boolean) => {
    setEnabled(enabled);
    setPreview(null);
    try { localStorage.setItem(PREFERENCE, String(enabled)); } catch { /* storage may be disabled */ }
  }, []);
  const openTerm = useCallback((termId: string, bindingId?: string) => {
    setSelected({ termId, bindingId }); setPanelOpen(true); setPreview(null);
  }, []);
  const closePanel = useCallback(() => setPanelOpen(false), []);

  useEffect(() => {
    if (!selectionEnabled || indexError || !index || index.status !== 'ready') return;
    const showSelection = (event?: Event) => {
      if (event?.target instanceof Element && event.target.closest('[data-knowledge-surface]')) return;
      const selection = readSelection(window.getSelection());
      if (!selection) return;
      const terms = matchTerms(selection.text, index.terms, selection.termId);
      if (!terms.length) { setPreview(null); return; }
      if (panelOpen && window.matchMedia('(min-width: 1600px)').matches && terms.length === 1) {
        setSelected({ termId: terms[0].term_id, bindingId: selection.bindingId });
        setPreview(null);
        return;
      }
      const rect = selection.rect;
      setPreview({ terms, bindingId: selection.bindingId,
        left: Math.max(12, Math.min(rect.left, window.innerWidth - Math.min(340, window.innerWidth - 24) - 12)),
        top: Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - 230)),
      });
    };
    const onKeyUp = (event: KeyboardEvent) => { if (event.key === 'Shift' || (event.shiftKey && event.key.startsWith('Arrow'))) showSelection(); };
    const dismissOutside = (event: PointerEvent) => { if (!previewRef.current?.contains(event.target as Node)) setPreview(null); };
    const dismiss = () => setPreview(null);
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setPreview(null); };
    document.addEventListener('pointerup', showSelection);
    document.addEventListener('keyup', onKeyUp);
    document.addEventListener('pointerdown', dismissOutside);
    document.addEventListener('keydown', escape);
    window.addEventListener('resize', dismiss);
    window.addEventListener('scroll', dismiss, true);
    return () => {
      document.removeEventListener('pointerup', showSelection);
      document.removeEventListener('keyup', onKeyUp);
      document.removeEventListener('pointerdown', dismissOutside);
      document.removeEventListener('keydown', escape);
      window.removeEventListener('resize', dismiss);
      window.removeEventListener('scroll', dismiss, true);
    };
  }, [index, indexError, panelOpen, selectionEnabled]);

  const value = useMemo(() => ({ client, index, indexError, refreshIndex, valuation, selected, panelOpen, openTerm, closePanel, selectionEnabled, setSelectionEnabled }),
    [client, index, indexError, refreshIndex, valuation, selected, panelOpen, openTerm, closePanel, selectionEnabled, setSelectionEnabled]);
  return <KnowledgeContext.Provider value={value}>
    {children}
    {preview && <div ref={previewRef} className="knowledge-preview" style={{ top: preview.top, left: preview.left }} data-knowledge-surface role="region" aria-label="选中文字的词条解释">
      <button type="button" className="knowledge-dismiss" aria-label="关闭简释" onClick={() => setPreview(null)}>×</button>
      {preview.terms.length === 1 ? <>
        <strong>{preview.terms[0].title_zh || preview.terms[0].title_en}</strong>
        <p>{preview.terms[0].summary}</p>
        <button type="button" className="knowledge-link" onClick={() => openTerm(preview.terms[0].term_id, preview.bindingId)}>展开词条 →</button>
      </> : <><strong>请选择要了解的概念</strong><ul>{preview.terms.map(term => <li key={term.term_id}><button type="button" className="knowledge-link" onClick={() => openTerm(term.term_id, preview.bindingId)}>{term.title_zh || term.title_en} {term.title_en}</button></li>)}</ul></>}
    </div>}
  </KnowledgeContext.Provider>;
}
