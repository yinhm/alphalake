export interface MacroPoint {
  period_start: string;
  period_end: string;
  value: string | null;
  value_status: 'reported' | 'not_reported';
  release_id: number;
  source_locator: string;
}
export interface MacroSeries {
  series_code: string;
  name_zh: string;
  source: 'nbs' | 'pbc' | 'mof';
  dataset: string;
  geography: string;
  frequency: 'monthly' | 'quarterly';
  period_basis: string;
  unit: string;
  price_basis: string;
  seasonal_adjustment: string;
  definition: string;
  points: MacroPoint[];
}
export interface MacroRelease {
  release_id: number;
  artifact_id: number;
  source: string;
  dataset: string;
  url: string;
  sha256: string;
  source_published_at: string | null;
  publication_precision: string;
  first_acquired_at: string;
  recorded_at: string;
}
export interface MacroPublication {
  contract: string;
  id: string;
  published_at: string;
  previous_id: string | null;
  content_hash: string;
  snapshot: { contract: string; exported_at: string; vintage: string; series: MacroSeries[]; releases: MacroRelease[] };
  commentary: {
    title: string;
    summary: string;
    author: string;
    reviewed_at: string;
    sections: { title: string; body: string; evidence: { series_code: string; period_end: string; value: string | null }[] }[];
    limitations: string[];
  };
}
export interface MacroCatalog {
  contract: string;
  current_id: string | null;
  publications: {
    id: string; title: string; summary: string; published_at: string; exported_at: string;
    content_hash: string; sha256: string; series_count: number; observation_count: number; latest_statistical_period: string;
  }[];
}
