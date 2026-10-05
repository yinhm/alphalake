/** 展示引擎实际基期及标签；不从另一期间补齐缺项。 */

import type { ValuationResponse, RawFinancials } from '../types/valuation';
import { baseYear } from './baseYear';

export type VintageSource = 'LTM' | '10-K';

export interface FieldWithVintage<T> {
  value: T | null | undefined;
  vintage: VintageSource | null;
}

export function getField<K extends keyof RawFinancials>(
  data: ValuationResponse,
  key: K,
): FieldWithVintage<RawFinancials[K]> {
  const value = baseYear(data)?.[key] ?? null;
  return { value, vintage: value == null ? null
    : data.inputs.prepared_ttm || data.inputs.quarters_since_10k ? 'LTM' : '10-K' };
}
