export const RESEARCH_KINDS = {
  initial_coverage: '初始覆盖',
  thesis: '经营论点',
  independent_dcf: '独立DCF',
  narrative_batch: '批量故事与估值',
  research_note: '研究专题',
};
export type ResearchKind = keyof typeof RESEARCH_KINDS;

export interface ResearchSummary {
  contract: 'alphalake-research-v1';
  id: string;
  title: string;
  summary: string;
  kind: ResearchKind;
  status: 'complete' | 'partial' | 'archived';
  tickers: string[];
  published_at: string;
  research_date: string | null;
  financial_period: string | null;
  information_as_of: string | null;
  share_date: string | null;
  run_ids: string[];
  limitations: string[];
  previous_id: string | null;
  report_file: string;
}

export interface ResearchFile {
  id: string;
  label: string;
  path: string;
  sha256: string;
  media_type: string;
  size_bytes: number | null;
  available: boolean;
  url: string;
}

export interface ResearchCatalog {
  contract: 'alphalake-research-v1';
  total: number;
  offset: number;
  limit: number;
  reports: ResearchSummary[];
  issues: { id: string; code: string; message: string }[];
}

export interface ResearchDetail {
  report: ResearchSummary;
  files: ResearchFile[];
  document: { id: string; label: string; path: string; body_md: string };
}
