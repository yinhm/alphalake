// 用已安装的TypeScript/React运行真实组件，核验prepared TTM不会伪装成FY0。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
for (const ext of ['.ts', '.tsx']) {
  require.extensions[ext] = (module, filename) => module._compile(ts.transpileModule(
    fs.readFileSync(filename, 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true, target: ts.ScriptTarget.ES2022,
    }}).outputText, filename);
}
const Worksheet = require('../src/pages/TrailingTwelveMonth.tsx').default;
const { elapsedQuarters, quarterlyForDisplay } = require('../src/lib/baseYear.ts');
const financials = { fiscal_year: 2025, revenues: 11, ebit: 2 };
const inputs = { ticker: 'SZSE:300866', raw_financials: [{ ...financials, revenues: 10 }],
  period_date_10k: '2025-12-31', period_date_10q: '2026-06-30',
  quarterly_financials: [], quarters_since_10k: 0,
  prepared_ttm: { period_end: '2026-06-30', financials, provenance: {
    formula: 'FY + current_YTD - prior_same_YTD',
    components: JSON.stringify({ revenues: [
      { period: '2025-12-31', value: '10000000' },
      { period: '2026-06-30', value: '4000000' },
      { period: '2025-06-30', value: '3000000' },
    ]}), quarterly_display: JSON.stringify([{ revenues: null }]),
  }}};
assert.equal(elapsedQuarters(inputs), 2);
assert.equal(elapsedQuarters({ ...inputs, period_date_10k: null }), null);
assert.equal(quarterlyForDisplay(inputs)[0].revenues, null);
const html = renderToStaticMarkup(React.createElement(Worksheet, { data: { inputs, ltm_financials: financials }}));
assert(html.includes('FY + current_YTD - prior_same_YTD'));
assert(html.includes('Current YTD') && html.includes('Prior YTD'));
assert(html.includes('+4') && html.includes('-3'));
assert(!html.includes('no new quarters') && !html.includes('Insufficient quarterly data'));
console.log('prepared TTM worksheet: passed');
