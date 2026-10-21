export interface ScreenDataset {
  id: string; title: string; source_report_id: string; report_url: string; csv_url: string;
  research_date: string | null; financial_period: string; information_as_of: string;
  research_frozen_at: string; company_evidence_acquired_at: string;
  share_dates: Record<string, number>; quote_dates: Record<string, number>;
  condition_type: string; sqlite_sha256: string; reference_snapshot_id: string;
  runtime_stages: Record<string, string>; build_version: string; built_at: string;
  limitations: string[]; content_hash: string;
}

export interface ScreenRow {
  ticker: string; name: string; reference_industry: string | null; admission: string;
  financial_admission: string | null; missing_fields: string[]; reason: string;
  report_id: string; url: string; mode: string | null;
  condition_value_cny_per_share: number | null; close_cny: number | null; close_date: string | null;
  descriptive_difference_pct: number | null; price_comparison_status: string; price_comparison_reason: string;
  financial_period: string; share_date: string | null; information_as_of: string; research_frozen_at: string;
  run_id: string; assumptions: Record<string, number | null>; assumption_missing_reasons: Record<string, string>;
  review_flags: string[]; comparison_display_status: string; comparison_display_reason: string;
  model_source: { path: string; sha256: string };
}

export interface ScreenCatalog {
  contract: 'alphalake-valuation-results-v1'; current: string | null;
  datasets: (ScreenDataset & { universe_count: number })[];
}

export interface ScreenResults {
  contract: 'alphalake-valuation-results-v1'; dataset: ScreenDataset;
  universe_count: number; filtered_count: number; admission_counts: Record<string, number>;
  labels: Record<'admission' | 'flag' | 'comparison', Record<string, string>>;
  facets: Record<'admission' | 'industry' | 'flag' | 'comparison', Record<string, number>>;
  range_unknown_counts: Record<string, number>; offset: number; limit: number; rows: ScreenRow[];
  issues: { code: string; message: string }[];
}
