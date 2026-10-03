import type { ReactNode } from 'react';
import { useKnowledge } from './context';
import { matchTerms } from './matching';

export function Term({ termId, bindingId, children }: { termId?: string; bindingId?: string; children: ReactNode }) {
  const knowledge = useKnowledge();
  const matches = !termId && typeof children === 'string' ? matchTerms(children, knowledge?.index?.terms ?? []) : [];
  const resolvedTermId = termId ?? (matches.length === 1 ? matches[0].term_id : undefined);
  if (!resolvedTermId) return <>{children}</>;
  return <span className="knowledge-term" data-term-id={resolvedTermId} data-binding-id={bindingId}>
    {children}
    {knowledge && <button type="button" className="knowledge-help" aria-label={`查看 ${typeof children === 'string' ? children : resolvedTermId} 的词条解释`} onClick={() => knowledge.openTerm(resolvedTermId, bindingId)}>?</button>}
  </span>;
}
