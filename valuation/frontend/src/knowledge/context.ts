import { createContext, useContext } from 'react';
import type { ValuationResponse } from '../types/valuation';
import type { KnowledgeIndex } from './types';
import type { KnowledgeClient } from './client';

export interface KnowledgeContextValue {
  client: KnowledgeClient;
  index: KnowledgeIndex | null;
  indexError: string | null;
  refreshIndex: () => Promise<void>;
  valuation: ValuationResponse | null;
  selected: { termId: string; bindingId?: string } | null;
  panelOpen: boolean;
  openTerm: (termId: string, bindingId?: string) => void;
  closePanel: () => void;
  selectionEnabled: boolean;
  setSelectionEnabled: (enabled: boolean) => void;
}
export const KnowledgeContext = createContext<KnowledgeContextValue | null>(null);
// Shared worksheets also render in standalone regression checks without a provider.
export function useKnowledge() { return useContext(KnowledgeContext); }
