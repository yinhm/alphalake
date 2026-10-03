export interface KnowledgeTermSummary {
  term_id: string;
  title_zh: string;
  title_en: string;
  kind: string;
  category: string;
  summary: string;
  aliases: string[];
}
export interface KnowledgeIndex {
  status: 'ready' | 'unavailable';
  release_id: string | null;
  release_label?: string | null;
  schema_version: 1;
  terms: KnowledgeTermSummary[];
}
export interface KnowledgeBinding {
  binding_id: string;
  term_id: string;
  page_key: string;
  field_path: string;
  context_key: string;
  usage_md: string;
  limitations_md: string;
  reviewed_engine_ref: string;
}
export interface KnowledgeDetail {
  release_id: string;
  release_label?: string;
  term: Omit<KnowledgeTermSummary, 'aliases'> & { body_md: string; content_hash: string };
  relations: { term_id: string; title_zh: string; title_en: string; relation_type: string }[];
  sources: { source_id: string; title: string; author: string; url: string; published_at: string | null; verified_at: string | null; section_anchor: string; locator: string }[];
  bindings: KnowledgeBinding[];
}
