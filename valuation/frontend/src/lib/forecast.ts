import type { ValuationResponse } from '../types/valuation';

/** Read the calculation's resolved values; never invent a display default. */
export function forecastValue(data: ValuationResponse, name: string): number | null {
  return data.dcf?.forecast_parameters?.[name]?.value ?? null;
}
