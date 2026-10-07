import type { ValuationResponse } from '../types/valuation';

/** Read the engine's matched NOPAT/opening-capital result; no second return formula. */
export function firstProjectedROIC(data: ValuationResponse): number | null {
  const value = data.dcf?.implied_roic_projections?.[0];
  return value == null || !Number.isFinite(value) ? null : value;
}

/**
 * Factual gap statement between an actual computed value and a reference.
 * Unit is 'pp' for percentages (displayed with × 100) or '×' for ratios.
 * Returns a signed string like '+6pp' or '-0.5×', or '—' if either side missing.
 */
export function gapStatement(
  actual: number | null | undefined,
  reference: number | null | undefined,
  unit: 'pp' | '×',
): string {
  if (actual == null || reference == null) return '—';
  const delta = actual - reference;
  const sign = delta >= 0 ? '+' : '';
  if (unit === 'pp') {
    return `${sign}${(delta * 100).toFixed(1)}pp`;
  }
  return `${sign}${delta.toFixed(2)}×`;
}
