import { expect, test, type Page } from '@playwright/test';
import type { ResearchSummary, ResearchDetail } from '../src/types/research';

// Synthetic read-only reports; no financial database or valuation service is consulted.
const report: ResearchSummary = {
  contract: 'alphalake-research-v1', id: 'test-report', title: '合成公司研究', summary: '用于阅读交付测试。',
  kind: 'initial_coverage', status: 'complete', tickers: ['SZSE:300866'], published_at: '2026-10-18T01:00:00Z',
  research_date: '2026-10-17', financial_period: '2026-06-30', information_as_of: '2026-10-17T00:00:00Z',
  share_date: '2026-06-30', run_ids: ['synthetic-run'], limitations: ['条件价值，不是当前目标价。'], previous_id: null, report_file: 'report',
};
const file = (id: string, label: string, path: string, media_type: string, available = true) => ({
  id, label, path, media_type, available, size_bytes: available ? 123 : null, sha256: 'a'.repeat(64), url: `/api/research/test-report/files/${id}`,
});
const files = [file('report', '研究正文', 'research/test-report/report.md', 'text/plain'),
  file('assumptions', '年度假设', 'research/test-report/assumptions.md', 'text/plain'),
  file('pdf', '原文PDF', 'cninfo/objects/source.pdf', 'application/pdf'),
  file('chart', '测试图表', 'research/test-report/chart.png', 'image/png'),
  file('missing', '缺失附件', 'research/test-report/missing.csv', 'text/csv', false)];
const markdown = `## 核心结论

条件计算 $100 / 1.1$。

| 情景 | 第一年 | 第二年 | 第三年 | 第四年 | 第五年 | 第六年 | 第七年 | 第八年 | 第九年 |
|---|---|---|---|---|---|---|---|---|---|
| 合成 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |

[查看年度条件](assumptions.md)

[核对PDF](../../cninfo/objects/source.pdf#page=12)

[公式词条](/knowledge/wacc)

[另一份报告](/research/other-report)

[研究目录](/research?kind=initial_coverage)

[冒充报告的文件](/research/other-report/private.txt)

[越界报告路径](/research/../private.txt)

![本地图表](chart.png)

![跟踪图片](https://example.invalid/tracking.png)

[未登记文件](../../private.txt)

[危险链接](javascript:alert(1))

<script>window.researchInjected=true</script>`;

async function mock(page: Page, total = 1, tickers = report.tickers) {
  const shownReport = { ...report, tickers };
  const unexpected: string[] = [];
  await page.route(/^http:\/\/127\.0\.0\.1:4173\/api\//, async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/knowledge/index') return route.fulfill({ json: { status: 'ready', release_id: 'test', schema_version: 1, terms: [] } });
    if (url.pathname.startsWith('/api/admin/')) return route.fulfill({ json: { configured: false, admin: false } });
    if (url.pathname === '/api/research') {
      const matches = (!url.searchParams.get('q') || url.searchParams.get('q') === '300866') && (!url.searchParams.get('kind') || url.searchParams.get('kind') === report.kind);
      return route.fulfill({ json: { contract: report.contract, reports: matches ? [shownReport] : [], total: matches ? total : 0, offset: Number(url.searchParams.get('offset') ?? 0), limit: 20, issues: [] } });
    }
    if (url.pathname === '/api/research/test-report') {
      const id = url.searchParams.get('document') || 'report';
      const document = files.find(f => f.id === id)!;
      const detail: ResearchDetail = { report: shownReport, files, document: { ...document, body_md: id === 'report' ? markdown + '\n\n[本期宏观](/macro/cn-macro-test)\n\n[伪装宏观文件](/macro/cn-macro-test/private.txt)' : '## 年度条件明细\n\n不把条件当作事实。' } };
      return route.fulfill({ json: detail });
    }
    if (url.pathname.endsWith('/files/chart')) return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==', 'base64') });
    if (url.pathname.startsWith('/api/research/')) return route.fulfill({ status: 404, json: { detail: { reason: '研究报告不存在' } } });
    unexpected.push(url.pathname);
    return route.fulfill({ status: 500, json: { detail: 'unexpected request' } });
  });
  return unexpected;
}

test('research is independent of company loading; search, category, pagination and back restore the requested list', async ({ page }) => {
  const unexpected = await mock(page, 21);
  const unrelatedPages: string[] = [];
  page.on('request', request => {
    if (/\/src\/(pages\/|components\/OnboardingWizard)/.test(request.url())) unrelatedPages.push(request.url());
  });
  await page.goto('/research');
  await expect(page.getByRole('heading', { name: '研究报告', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: report.title })).toBeVisible();
  await page.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/offset=20/);
  await expect(page.getByText('第2页')).toBeVisible();
  await page.getByRole('button', { name: '上一页' }).click();
  await page.getByRole('searchbox', { name: '搜索研究' }).fill('不存在');
  await expect(page.getByText('没有匹配的研究，请调整搜索条件。')).toBeVisible();
  await page.getByRole('searchbox', { name: '搜索研究' }).fill('300866');
  await expect(page.getByRole('link', { name: report.title })).toBeVisible();
  await page.getByLabel('研究类型').selectOption('thesis');
  await expect(page.getByText('没有匹配的研究，请调整搜索条件。')).toBeVisible();
  await page.goBack();
  await expect(page.getByRole('link', { name: report.title })).toBeVisible();
  await page.getByRole('link', { name: report.title }).click();
  await expect(page.getByRole('heading', { name: '核心结论' })).toBeVisible();
  expect(unrelatedPages).toEqual([]);
  expect(unexpected).toEqual([]);
});

test('failed lazy page offers a full resource reload instead of retrying a rejected import', async ({page}) => {
  await mock(page);
  await page.route('**/api/macro', route => route.fulfill({json:{current_id:null,publications:[]}}));
  await page.route('**/src/macro/MacroPage.tsx*', route => route.abort());
  await page.goto('/macro');
  await expect(page.getByRole('heading', {name:'页面资源未能加载'})).toBeVisible();
  await expect(page.getByRole('button', {name:'刷新页面版本'})).toBeVisible();
  await expect(page.getByText('刷新前请先保存尚未保存的输入。', {exact:false})).toBeVisible();
  await page.unroute('**/src/macro/MacroPage.tsx*');
  await page.getByRole('button', {name:'刷新页面版本'}).click();
  await expect(page.getByRole('heading', {name:'宏观概览',exact:true})).toBeVisible();
  await expect(page.getByRole('heading', {name:'页面资源未能加载'})).not.toBeVisible();
});

test('detail renders tables, formulas, registered links and attachments safely, including direct refresh', async ({ page }) => {
  const unexpected = await mock(page);
  let externalImages = 0;
  await page.route('https://example.invalid/**', route => { externalImages++; return route.abort(); });
  await page.goto('/research/test-report');
  await expect(page.getByRole('heading', { name: report.title, exact: true })).toBeVisible();
  const article = page.getByRole('article', { name: '研究正文' });
  await expect(article.getByRole('table')).toHaveCount(1);
  await expect(article.locator('.katex')).toBeVisible();
  await expect(article.getByRole('link', { name: '核对PDF' })).toHaveAttribute('href', '/api/research/test-report/files/pdf#page=12');
  await expect(article.getByRole('link', { name: '公式词条' })).toHaveAttribute('href', '/knowledge/wacc');
  await expect(article.getByRole('link', { name: '另一份报告' })).toHaveAttribute('href', '/research/other-report');
  await expect(article.getByRole('link', { name: '本期宏观' })).toHaveAttribute('href', '/macro/cn-macro-test');
  await expect(article.getByRole('link', { name: '伪装宏观文件' })).toHaveCount(0);
  await expect(article.getByRole('link', { name: '研究目录' })).toHaveAttribute('href', '/research?kind=initial_coverage');
  await expect(article.getByRole('link', { name: '冒充报告的文件' })).toHaveCount(0);
  await expect(article.getByRole('link', { name: '越界报告路径' })).toHaveCount(0);
  await expect(article.getByRole('img')).toHaveCount(1);
  await expect(article.getByRole('link', { name: '危险链接' })).toHaveCount(0);
  await expect(article.getByRole('link', { name: '未登记文件' })).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).researchInjected)).toBeUndefined();
  expect(externalImages).toBe(0);
  await page.getByText('附件与来源（5项）', { exact: true }).click();
  await expect(page.getByText('缺失附件（文件不可用）')).toBeVisible();
  await expect(page.getByRole('link', { name: '原文PDF', exact: true })).toHaveAttribute('href', '/api/research/test-report/files/pdf?download=true');
  await article.getByRole('link', { name: '查看年度条件' }).click();
  await expect(page).toHaveURL(/\?document=assumptions$/);
  await expect(page.getByRole('heading', { name: '年度条件明细' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: '年度条件明细' })).toBeVisible();
  await page.getByRole('navigation', { name: '报告与附文' }).getByRole('link', { name: '研究正文' }).click();
  await expect(page.getByRole('heading', { name: '核心结论' })).toBeVisible();
  expect(unexpected).toEqual([]);
});

test('mobile report keeps wide tables within the reading area and unknown reports show an explicit error', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mock(page);
  await page.goto('/research/test-report');
  await expect(page.getByRole('heading', { name: '核心结论' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await page.getByRole('table').evaluate(table => table.scrollWidth > table.clientWidth)).toBe(true);
  await page.goto('/research/missing');
  await expect(page.getByRole('alert')).toContainText('研究报告不存在');
  await page.getByRole('button', { name: '重试读取' }).click();
  await expect(page.getByRole('alert')).toContainText('研究报告不存在');
  await page.getByRole('link', { name: '返回研究目录' }).click();
  await expect(page.getByRole('link', { name: report.title })).toBeVisible();
});

test('large research scopes collapse in both catalogue and detail without losing securities', async ({ page }) => {
  const tickers = Array.from({ length: 520 }, (_, i) => `SHSE:${600000 + i}`);
  await mock(page, 1, tickers);
  await page.goto('/research');
  const card = page.locator('.research-cards article');
  await expect(card.getByText('520只证券（展开）', { exact: true })).toBeVisible();
  await expect(card.locator('details')).not.toHaveAttribute('open');
  await page.getByRole('link', { name: report.title }).click();
  const scope = page.locator('.research-facts details');
  await expect(scope).not.toHaveAttribute('open');
  await expect(page.getByRole('heading', { name: '核心结论' })).toBeInViewport();
  await scope.locator('summary').click();
  await expect(scope).toContainText(tickers[0]);
  await expect(scope).toContainText(tickers[519]);
  await scope.locator('summary').click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
