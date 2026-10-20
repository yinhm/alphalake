/** Visible percentage fields use percentage points: 0.5 and 0.5% both mean 0.005. */
export function parsePercent(raw: string): number | null {
  const text = raw.trim().replace(/%$/, '').trim().replace(/,/g, '');
  if (!text) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value / 100 : null;
}
