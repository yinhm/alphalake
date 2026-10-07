// 真实共享组件：避免税前利润率×新增倍率冒充税后存量ROIC。
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
const { firstProjectedROIC } = require('../src/lib/reverseChecks.ts');
const Strip = require('../src/components/ClosedLoopStrip.tsx').default;
const data = {
  inputs: { valuation_assumptions: { target_operating_margin: .1, sales_to_capital_high: 2 },
    industry_data: { roic: .12, sales_to_capital: 2 } },
  cashflow: { historical_roic_avg_5yr: .1, historical_s_c_avg_5yr: 2 },
  cost_of_capital: { wacc: .08 },
  dcf: { implied_roic_projections: [.15], implied_roic_terminal: .12, terminal_roic: .12, terminal_wacc: .08 },
};
assert.equal(firstProjectedROIC(data), .15);
assert.equal(firstProjectedROIC({ ...data, dcf: { implied_roic_projections: [null] } }), null);
assert.equal(firstProjectedROIC({ ...data, dcf: { implied_roic_projections: [0] } }), 0);
const html = renderToStaticMarkup(React.createElement(Strip, { data }));
assert(html.includes('15.0%') && !html.includes('20.0%'));
assert(html.includes('First-year projected ROIC') && html.includes('Historical stock turnover'));
assert(!html.includes('Required Sales/Capital') && !html.includes('should converge to WACC'));
assert(html.includes('持续优势需经营依据') && html.includes('12.0%'));
console.log('story return diagnostics: passed');
