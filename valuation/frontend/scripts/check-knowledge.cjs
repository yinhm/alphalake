// Synthetic content only: verify release consistency and selection boundaries without a glossary database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const { JSDOM } = require('jsdom');
for (const ext of ['.ts', '.tsx']) {
  require.extensions[ext] = (module, filename) => module._compile(ts.transpileModule(
    fs.readFileSync(filename, 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true, target: ts.ScriptTarget.ES2022,
    }}).outputText, filename);
}
const { matchTerms, normalizeAlias, readSelection, searchTerms } = require('../src/knowledge/matching.ts');
const { KnowledgeClient } = require('../src/knowledge/client.ts');
const terms = [
  { term_id: 'wacc', title_zh: '测试资本成本', title_en: 'WACC', aliases: ['资本', 'Cost of capital'] },
  { term_id: 'capital', title_zh: '测试资本', title_en: 'Capital', aliases: ['资本'] },
];
assert.equal(normalizeAlias(' ＷＡＣＣ  '), 'wacc');
assert.deepEqual(matchTerms('ACC', terms, 'wacc').map(x => x.term_id), ['wacc']);
assert.deepEqual(matchTerms('ACC', terms), []);
assert.equal(matchTerms('资本', terms).length, 2);
assert.equal(matchTerms('  COST  of CAPITAL ', terms)[0].term_id, 'wacc');
assert.equal(matchTerms('WACC', terms, 'unknown').length, 0);

// An exact term must remain reachable within the ten-row directory even if
// many earlier entries contain the same word in their category or title.
const searchSample = [
  ...Array.from({ length: 12 }, (_, i) => ({
    term_id: `topic-${i}`, title_zh: `资本主题${i}`, title_en: '',
    category: '增长与再投资', aliases: ['Net Reinvestment'],
  })),
  { term_id: 'reinvestment', title_zh: '再投资', title_en: 'Reinvestment', category: '增长与再投资', aliases: ['投入'] },
];
for (const query of ['再投资', ' REINVESTMENT ', '投入']) {
  assert.equal(searchTerms(query, searchSample)[0].term_id, 'reinvestment');
}
assert.equal(searchTerms('再投资', searchSample).length, 13);
assert.deepEqual(searchTerms('', searchSample), searchSample);
assert.equal(searchSample[0].term_id, 'topic-0', 'search must not reorder its input index');
assert.equal(searchTerms('不存在的术语', searchSample).length, 0);
assert.equal(searchTerms('资本', terms).length, 2, 'ambiguous exact aliases remain candidates');

const dom = new JSDOM('<main data-knowledge-scope><p><span data-term-id="wacc" data-binding-id="wacc.current">WACC</span></p><p id="plain">WACC</p><p id="number">12.5%</p><p id="editor" contenteditable="true">WACC</p><p><span id="across">WACC</span><input></p><table><tbody><tr><td id="cell1">WACC</td><td id="cell2">Capital</td><td data-knowledge-ignore id="editable">WACC</td></tr></tbody></table></main><aside id="outside">WACC</aside>');
const document = dom.window.document;
dom.window.Range.prototype.getBoundingClientRect = () => ({ top: 0, left: 0, bottom: 10, right: 30, width: 30, height: 10 });
function selection(selector, start = 0, end, endSelector) {
  const startNode = document.querySelector(selector).firstChild;
  const endNode = endSelector ? document.querySelector(endSelector).firstChild : startNode;
  const range = document.createRange();
  range.setStart(startNode, start);
  range.setEnd(endNode, end ?? endNode.length);
  const selected = dom.window.getSelection();
  selected.removeAllRanges(); selected.addRange(range);
  return selected;
}
let selected = selection('[data-term-id]', 1, 4);
assert.equal(readSelection(selected).termId, 'wacc');
assert.equal(readSelection(selected).bindingId, 'wacc.current');
assert.equal(selected.toString(), 'ACC', 'reading must preserve the actual text selection');
assert.equal(readSelection(selection('#plain')).text, 'WACC');
for (const ignored of ['#number', '#editor', '#editable', '#outside']) assert.equal(readSelection(selection(ignored)), null, ignored);
assert.equal(readSelection(selection('#cell1', 0, 7, '#cell2')), null);
assert.equal(readSelection(selection('[data-term-id]', 0, 4, '#plain')), null);

const { safeKnowledgeUrl } = require('../src/knowledge/urls.ts');
for (const url of ['javascript:alert(1)', 'data:text/html,test', '//other.test', '/admin', '/knowledge-bad']) assert.equal(safeKnowledgeUrl(url), undefined);
for (const url of ['https://example.test/source', '/knowledge/wacc', '/knowledge', '#formula']) assert.equal(safeKnowledgeUrl(url), url);
const { resolveLiveValue } = require('../src/knowledge/liveValues.ts');
const valueData = {
  inputs: { reporting_currency: 'CNY', macro_inputs: { country_risk_premium: 0 }, valuation_assumptions: { cost_of_capital_stable_override: null } },
  cost_of_capital: { wacc: 0.1 }, cashflow: { fcff: -250 }, final: { value_per_share: 25 }, dcf: { terminal_value_firm: 2000 },
};
assert.equal(resolveLiveValue(null, 'cost_of_capital.wacc'), null);
for (const path of ['inputs.ticker', '__proto__', 'constructor']) assert.equal(resolveLiveValue(valueData, path), null);
assert.equal(resolveLiveValue(valueData, 'cost_of_capital.wacc').value, '10.00%');
assert.equal(resolveLiveValue({ ...valueData, cost_of_capital: { wacc: 0.125 } }, 'cost_of_capital.wacc').value, '12.50%');
assert.equal(resolveLiveValue(valueData, 'inputs.macro_inputs.country_risk_premium').value, '0.00%');
assert.equal(resolveLiveValue(valueData, 'inputs.valuation_assumptions.cost_of_capital_stable_override').value, '未提供');
assert.equal(resolveLiveValue(valueData, 'cashflow.fcff').value, '-250 百万 CNY');
assert.equal(resolveLiveValue(valueData, 'final.value_per_share').value, '25 CNY/股');
assert.equal(resolveLiveValue(valueData, 'dcf.terminal_value_firm').value, '2,000 百万 CNY');
for (const wacc of [null, undefined, NaN, Infinity]) assert.equal(resolveLiveValue({ ...valueData, cost_of_capital: { wacc } }, 'cost_of_capital.wacc').value, '未提供');
assert.equal(resolveLiveValue({ ...valueData, cost_of_capital: undefined }, 'cost_of_capital.wacc').value, '未提供');

async function checkClient() {
  const calls = [];
  let release = 'r1';
  let mutateDuringDetail = false;
  const detail = (id, current) => ({ release_id: current, term: { term_id: id, summary: current }, relations: [], bindings: [], sources: [] });
  const observed = [];
  const client = new KnowledgeClient(async (url, options) => {
    calls.push(String(url));
    if (url === '/api/knowledge/index') {
      if (options?.headers?.['If-None-Match'] === release) return new Response(null, { status: 304 });
      return Response.json({ status: 'ready', release_id: release, schema_version: 1, terms }, { headers: { ETag: release } });
    }
    if (mutateDuringDetail) { mutateDuringDetail = false; release = 'r2'; return Response.json({}, { status: 409 }); }
    return Response.json(detail('wacc', release));
  });
  const unsubscribe = client.subscribe(index => observed.push(index.release_id));
  assert.equal((await client.getTerm('wacc')).release_id, 'r1');
  await client.getTerm('wacc');
  assert.equal(calls.filter(url => url.includes('/terms/')).length, 1, 'detail should be cached within a release');
  release = 'r0';
  mutateDuringDetail = true;
  assert.equal((await client.getTerm('wacc')).release_id, 'r2');
  assert.deepEqual(observed, ['r1', 'r0', 'r2'], 'all refreshes must notify the provider');
  release = 'r3';
  assert.equal((await client.getTerm('wacc')).term.summary, 'r3');
  unsubscribe();
  const missing = new KnowledgeClient(async () => Response.json({ status: 'unavailable', release_id: null, schema_version: 1, terms: [] }));
  assert.equal((await missing.getIndex()).status, 'unavailable');
  await assert.rejects(missing.getTerm('wacc'), /尚未发布/);
  const bad = new KnowledgeClient(async () => Response.json({ schema_version: 2, terms: [] }));
  await assert.rejects(bad.getIndex(), /格式不受支持/);
  let staleCalls = 0;
  const stale = new KnowledgeClient(async url => {
    if (String(url).includes('/terms/')) { staleCalls++; return new Response(null, { status: 409 }); }
    return Response.json({ status: 'ready', release_id: 'r1', schema_version: 1, terms });
  });
  await assert.rejects(stale.getTerm('wacc'));
  assert.equal(staleCalls, 2, 'only one retry after a stale release is allowed');
}
checkClient().then(() => console.log('knowledge matching, selection boundaries and release cache: passed')).catch(error => { console.error(error); process.exitCode = 1; });
