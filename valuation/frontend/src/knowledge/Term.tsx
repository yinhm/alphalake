import type { ReactNode } from 'react';
import { useKnowledge } from './context';

export function Term({ termId, bindingId, children }: { termId: string; bindingId?: string; children: ReactNode }) {
  const knowledge = useKnowledge();
  return <span className="knowledge-term" data-term-id={termId} data-binding-id={bindingId}>
    {children}
    {knowledge && <button type="button" className="knowledge-help" aria-label={`查看 ${typeof children === 'string' ? children : termId} 的词条解释`} onClick={() => knowledge.openTerm(termId, bindingId)}>?</button>}
  </span>;
}
