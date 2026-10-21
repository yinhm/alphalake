import { expect, test, type Page } from '@playwright/test';
import type { MacroPublication, MacroSeries } from '../src/types/macro';

function series(code: string, name: string, values: (string | null)[], unit = 'fraction'): MacroSeries {
  return { series_code: code, name_zh: name, source: 'nbs', dataset: 'macro_cpi_v1', geography: 'CN', frequency: 'monthly',
    period_basis: 'month', unit, price_basis: 'price_change', seasonal_adjustment: 'provider_unspecified', definition: '合成测试数据，不是实际宏观数值。',
    points: values.map((value, i) => ({ period_start: `2026-0${i + 7}-01`, period_end: `2026-0${i + 7}-${i === 2 ? 30 : 31}`, value,
      value_status: value === null ? 'not_reported' : 'reported', release_id: 1, source_locator: 'synthetic/row' })) };
}

const publication: MacroPublication = {
  contract: 'alphalake-macro-page-v1', id: 'macro-current', previous_id: 'macro-older', published_at: '2026-10-21T04:00:00Z', content_hash: 'a'.repeat(64),
  commentary: { title: '合成宏观观察', summary: '生产改善，需求仍有分化。', author: '测试', reviewed_at: '2026-10-21T03:00:00Z', limitations: ['合成数据；历史修订不代表严格PIT。'],
    sections: [{ title: '订单的变化', body: '**制造业扩张**，未知不能补零。<script>window.macroInjected=true</script>\n\n[官方原文](https://www.stats.gov.cn/sj/zxfb/synthetic.html)\n\n[危险链接](javascript:alert(1))\n\n[研究专题](/research/macro-investment-synthetic) [不允许的本地链接](/admin)\n\n|变量|核对|\n|---|---|\n|收入|订单与客户|',
      evidence: [{ series_code: 'cn_pmi_manufacturing', period_end: '2026-09-30', value: '50.1' }] }] },
  snapshot: { contract: 'alphalake-macro-snapshot-v1', exported_at: '2026-10-21T03:30:00Z', vintage: 'latest_source_vintage_not_historical_pit',
    releases: [{ release_id: 1, artifact_id: 1, source: 'nbs', dataset: 'macro_cpi_v1', url: 'https://www.stats.gov.cn/sj/zxfb/synthetic.html', sha256: 'b'.repeat(64),
      publication_precision: 'unknown', source_published_at: null, first_acquired_at: '2026-10-21T02:00:00Z', recorded_at: '2026-10-21T02:00:00Z' }],
    series: [
      { ...series('cn_gdp_real_yoy', '国内生产总值实际同比', ['0.043', '0.045']), frequency: 'quarterly', period_basis: 'quarter',
        points: [{ period_start: '2026-04-01', period_end: '2026-06-30', value: '0.043', value_status: 'reported', release_id: 1, source_locator: 'synthetic/gdp1' },
          { period_start: '2026-07-01', period_end: '2026-09-30', value: '0.045', value_status: 'reported', release_id: 1, source_locator: 'synthetic/gdp2' }] },
      series('cn_pmi_manufacturing', '制造业PMI', ['49.2', '49.8', '50.1'], 'index_points'),
      series('cn_pmi_production', '生产指数', ['49.9', '50.4', '51.7'], 'index_points'),
      series('cn_pmi_new_orders', '新订单指数', ['48.5', '50.6', '50.5'], 'index_points'),
      series('cn_cpi_yoy', '居民消费价格同比', ['0', '-0.001', null]),
      series('cn_ppi_yoy', '工业生产者出厂价格同比', ['0.03', '0.038', '0.05']),
      series('cn_retail_sales_yoy', '社会消费品零售同比', ['0.01', '0.004', '0.011']),
      series('cn_industrial_value_added_yoy', '工业增加值同比', ['0.05', '0.052', '0.055']),
      series('cn_afre_stock_yoy', '社融存量同比', ['0.074', '0.072', '0.071']),
      series('cn_property_sales_yoy', '商品房销售额累计同比', [null, '-0.13', '-0.139']),
      series('cn_afre_rmb_loans_flow', '实体人民币贷款当月增量', ['-400600000000', '0', '1273400000000'], 'CNY'),
      series('cn_industrial_value_added_mom_sa', '工业增加值季调环比', ['0.0011', '0.0054', '0.0061']),
      series('cn_retail_sales_mom_sa', '社会零售季调环比', ['0.0011', '-0.0016', '0.0035']),
      series('cn_ppi_input_yoy', '工业购进价格同比', ['0.04', '0.05', '0.069']),
      series('cn_fixed_asset_investment_ytd_yoy', '固定资产投资累计同比', ['-0.06', '-0.07', '-0.072']),
      series('cn_private_investment_ytd_yoy', '民间投资累计同比', ['-0.08', '-0.09', '-0.104']),
      series('cn_equipment_investment_ytd_yoy', '设备购置累计同比', ['0.09', '0.09', '0.097']),
      series('cn_property_investment_yoy', '房地产投资累计同比', ['-0.19', '-0.199', '-0.206']),
      series('cn_afre_rmb_loans_stock_yoy', '实体贷款余额同比', ['0.052', '0.05', '0.048']),
      series('cn_afre_government_bonds_stock_yoy', '政府债券余额同比', ['0.14', '0.135', '0.137']),
      series('cn_afre_flow', '社融当月增量', ['1406800000000', '1658100000000', '3547600000000'], 'CNY'),
      ...['cn_pmi_input_price','cn_pmi_output_price','cn_pmi_export_orders'].map(code => series(code, code, ['49', '50', '51'], 'index_points')),
      ...['cn_property_sales_area_yoy','cn_property_new_starts_yoy','cn_property_completions_yoy','cn_property_developer_funding_yoy'].map(code => series(code, code, ['-0.1', '-0.2', '-0.3'])),
      ...['cn_capacity_industrial','cn_capacity_manufacturing','cn_capacity_mining','cn_capacity_utilities'].map(code => series(code, code, ['0.7', '0.71', '0.73'])),
      series('cn_afre_government_bonds_flow', '政府债券当月增量', ['1317600000000', '1009700000000', '1536200000000'], 'CNY'),
    ] },
};

async function mock(page: Page, empty = false) {
  const unexpected: string[] = [];
  const entry = (id: string) => ({ id, title: id === 'macro-current' ? publication.commentary.title : '历史宏观观察', summary: publication.commentary.summary,
    published_at: publication.published_at, exported_at: publication.snapshot.exported_at, content_hash: publication.content_hash, sha256: 'c'.repeat(64),
    series_count: 11, observation_count: 32, latest_statistical_period: '2026-09-30' });
  await page.route(/^http:\/\/127\.0\.0\.1:4173\/api\//, async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/knowledge/index') return route.fulfill({ json: { status: 'ready', release_id: 'test', schema_version: 1, terms: [] } });
    if (url.pathname.startsWith('/api/admin/')) return route.fulfill({ json: { configured: false, admin: false } });
    if (url.pathname === '/api/macro') return route.fulfill({ json: { contract: 'alphalake-macro-index-v1', current_id: empty ? null : publication.id, publications: empty ? [] : [entry(publication.id), entry('macro-older')] } });
    if (url.pathname === '/api/macro/latest' || url.pathname === '/api/macro/publications/macro-current') return route.fulfill({ json: publication });
    if (url.pathname === '/api/macro/publications/macro-older') return route.fulfill({ json: { ...publication, id: 'macro-older', previous_id: null, commentary: { ...publication.commentary, title: '历史宏观观察', summary: '已冻结的旧期解读。' } } });
    if (url.pathname === '/api/macro/compare') return route.fulfill({ json: { contract: 'alphalake-macro-comparison-v1',
      before: { id: 'macro-older', content_hash: 'b'.repeat(64) }, after: { id: 'macro-current', content_hash: publication.content_hash },
      total: 0, summary: {}, changes: [], commentary_changed: true } });
    if (url.pathname.startsWith('/api/macro/')) return route.fulfill({ status: 404, json: { detail: { reason: '宏观期次不存在' } } });
    unexpected.push(url.pathname);
    return route.fulfill({ status: 500, json: { detail: 'unexpected request' } });
  });
  return unexpected;
}

test('macro works without company loading; charts, missing values and evidence are readable', async ({ page }) => {
  const unexpected = await mock(page);
  await page.goto('/macro');
  await expect(page.getByRole('heading', { name: '合成宏观观察', exact: true })).toBeVisible();
  const cards = page.getByRole('region', { name: '重要宏观指标' });
  await expect(cards.getByText('50.1点', { exact: true })).toBeVisible();
  await expect(cards.getByText('较上期同口径 +0.30点', { exact: true })).toBeVisible();
  await expect(cards.getByText('较上期同口径 +0.20个百分点', { exact: true })).toBeVisible();
  await expect(cards.getByText('未列报', { exact: true })).toBeVisible();
  await expect(page.locator('.macro-charts .recharts-wrapper')).toHaveCount(11);
  await expect(page.locator('.macro-charts').getByText('CPI', { exact: true })).toBeVisible();
  await expect(page.locator('.macro-charts').getByText('PPI', { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: '地产链的不同环节', exact: true }).getByText('0', { exact: true })).toBeVisible();
  const notes = page.getByRole('region', { name: '数据点评' });
  await notes.getByText('核对支撑数据（1项）', { exact: true }).click();
  await expect(notes.getByRole('link', { name: '原文', exact: true })).toHaveAttribute('href', publication.snapshot.releases[0].url);
  await expect(notes.getByRole('link', { name: '危险链接' })).toHaveCount(0);
  await expect(notes.getByRole('link', { name: '研究专题', exact: true })).toHaveAttribute('href', '/research/macro-investment-synthetic');
  await expect(notes.getByRole('link', { name: '不允许的本地链接' })).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).macroInjected)).toBeUndefined();
  await notes.getByRole('button', { name: '指标历史', exact: true }).click();
  await expect(page.locator('.macro-selected').getByRole('heading', { name: '制造业PMI · 历史与定义', exact: true })).toBeVisible();
  expect(unexpected).toEqual([]);
});

test('publication comparison separates revisions from new missing observations', async ({ page }) => {
  await mock(page);
  await page.route(/\/api\/macro\/compare\?/, route => route.fulfill({ json: {
    contract: 'alphalake-macro-comparison-v1', before: { id: 'macro-older' }, after: { id: 'macro-current' },
    commentary_changed: true, total: 4, summary: { new_period: 1, value_revised: 2, definition_changed: 1 },
    changes: [
      { series_code: 'cn_cpi_yoy', name_zh: '居民消费价格同比', unit: 'fraction', kind: 'value_revised', period_end: '2026-08-31',
        before: { value: '0', source_url: publication.snapshot.releases[0].url }, after: { value: '0.002', source_url: publication.snapshot.releases[0].url }, delta: '0.002', changed_fields: [] },
      { series_code: 'cn_cpi_yoy', name_zh: '居民消费价格同比', unit: 'fraction', kind: 'new_period', period_end: '2026-09-30', before: null,
        after: { value: null, source_url: publication.snapshot.releases[0].url }, delta: null, changed_fields: [] },
      { series_code: 'cn_pmi_manufacturing', name_zh: '制造业PMI', unit: 'index_points', kind: 'definition_changed', period_end: null,
        before: null, after: null, delta: null, changed_fields: ['seasonal_adjustment'] },
      { series_code: 'cn_afre_flow', name_zh: '社融', unit: 'CNY', kind: 'value_revised', period_end: '2026-08-31',
        before: { value: '0', source_url: publication.snapshot.releases[0].url }, after: { value: '0.000000000001', source_url: publication.snapshot.releases[0].url }, delta: '0.000000000001', changed_fields: [] },
    ],
  } }));
  await page.goto('/macro');
  const compared = page.getByRole('region', { name: '本期变化' });
  await expect(compared.getByRole('cell', { name: '0.2个百分点', exact: true })).toBeVisible();
  await expect(compared.getByRole('cell', { name: '未列报', exact: true })).toBeVisible();
  await expect(compared.getByRole('cell', { name: '0.0%', exact: true })).toBeVisible();
  await expect(compared.getByText('口径：seasonal_adjustment', { exact: true })).toBeVisible();
  await expect(compared.getByRole('cell', { name: '1.000e-24万亿元', exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: '宏观比较基准' }).selectOption('');
  await expect(compared.getByText('选择基准后比较两个固定发布。', { exact: true })).toBeVisible();
});

test('indicator search shows exact zero, negatives, missing and complete source periods', async ({ page }) => {
  await mock(page);
  await page.goto('/macro');
  const search = page.getByRole('textbox', { name: '搜索宏观指标' });
  await expect(page.getByRole('region', { name: '指标检索' }).locator('tbody tr')).toHaveCount(10);
  await search.fill('居民消费价格');
  await page.getByRole('button', { name: '居民消费价格同比', exact: true }).click();
  const selected = page.locator('.macro-selected');
  await expect(selected.getByRole('cell', { name: '0.0%', exact: true })).toBeVisible();
  await expect(selected.getByRole('cell', { name: '-0.1%', exact: true })).toBeVisible();
  await expect(selected.getByRole('cell', { name: '未列报', exact: true })).toBeVisible();
  await selected.getByRole('button', { name: '收起指标' }).click();
  await search.fill('人民币贷款当月');
  await page.getByRole('button', { name: '实体人民币贷款当月增量', exact: true }).click();
  await expect(page.locator('.macro-selected').getByRole('cell', { name: '-0.4万亿元', exact: true })).toBeVisible();
  await search.fill('不存在的指标');
  await expect(page.getByText('没有匹配的标准指标。', { exact: true })).toBeVisible();
});

test('archive, immutable page direct reload and mobile layout work', async ({ page }) => {
  await mock(page);
  await page.goto('/macro/history');
  await expect(page.getByRole('heading', { name: '宏观历史发布', exact: true })).toBeVisible();
  await page.getByRole('link', { name: '历史宏观观察', exact: true }).click();
  await expect(page.getByText('已冻结的旧期解读。', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: '历史宏观观察', exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.locator('.macro-charts .recharts-wrapper')).toHaveCount(11);
  await page.goto('/macro/missing');
  await expect(page.getByRole('alert')).toContainText('宏观期次不存在');
  await page.getByRole('button', { name: '重试读取' }).click();
  await expect(page.getByRole('alert')).toContainText('宏观期次不存在');
});

test('empty macro catalogue is explicit and does not request a valuation', async ({ page }) => {
  const unexpected = await mock(page, true);
  await page.goto('/macro');
  await expect(page.getByText('尚未发布宏观阅读页。数据入库后，需要核对点评并发布阅读快照。', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: '历史发布', exact: true }).click();
  await expect(page.getByText('尚无已发布宏观页面。', { exact: true })).toBeVisible();
  expect(unexpected).toEqual([]);
});
