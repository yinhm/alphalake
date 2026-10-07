import { expect, test, type Locator, type Page } from '@playwright/test';
import type { KnowledgeDetail, KnowledgeIndex, KnowledgeTermSummary } from '../src/knowledge/types';

// Synthetic publication, deliberately not a shipped glossary or a statement of valuation method.
const terms: KnowledgeTermSummary[] = [
  { term_id: 'wacc', title_zh: '测试资本成本', title_en: 'WACC', kind: '测试概念', category: '合成样本', summary: '用于验证交互的资本成本简释。', aliases: ['Capital cost'] },
  { term_id: 'capital-a', title_zh: '测试资本甲', title_en: 'Capital A', kind: '测试概念', category: '合成样本', summary: '用于验证歧义的第一种资本。', aliases: ['资本'] },
  { term_id: 'capital-b', title_zh: '测试资本乙', title_en: 'Capital B', kind: '测试概念', category: '合成样本', summary: '用于验证歧义的第二种资本。', aliases: ['资本'] },
  { term_id: 'revenue', title_zh: '测试收入', title_en: 'Revenue', kind: '测试概念', category: '合成样本', summary: '测试标签自动关联。', aliases: ['Revenues'] },
  { term_id: 'ebitda', title_zh: '测试息税折旧摊销前利润', title_en: 'EBITDA', kind: '测试概念', category: '合成样本', summary: '测试表头自动关联。', aliases: [] },
  ...Array.from({ length: 12 }, (_, i) => ({ term_id: `extra-${i}`, title_zh: `扩展词条${i}`, title_en: `Extra ${i}`, kind: '测试概念', category: '合成样本', summary: '用于验证目录限制不影响全库检索。', aliases: [`extra-alias-${i}`] })),
  ...[
    ['learning-roadmap', '测试学习路线'], ['lesson-financials', '测试第一课'],
    ['learning-practice', '测试案例练习'], ['forecast-assumptions', '测试预测专题'],
  ].map(([term_id, title_zh]) => ({ term_id, title_zh, title_en: '', kind: '测试课程', category: '合成样本', summary: '仅用于验证已发布内容的首页导航。', aliases: [] })),
];
const API_ROUTE = /^http:\/\/127\.0\.0\.1:4173\/api\//;
const index: KnowledgeIndex = { status: 'ready', release_id: 'fixture-r1', schema_version: 1, terms };

function detail(termId: string, release = 'fixture-r1'): KnowledgeDetail | null {
  const term = terms.find(item => item.term_id === termId);
  if (!term) return null;
  return {
    release_id: release,
    term: { ...term, body_md: termId === 'wacc'
      ? '仅为浏览器测试文本。公式 $x^2$。\n\n## 基本解释\n\n[关联定义](/knowledge/capital-a)\n\n[危险链接](javascript:alert(1))\n\n<script>window.knowledgeInjected=true</script>\n\n![远程图片](https://example.invalid/tracker.png)\n\n## 自己试一试\n\n测试问题。\n\n## 答案与解析\n\n先区分输入和结果。\n\n## 继续阅读\n\n课程结尾。'
      : '这是合成的关联词条正文。', content_hash: `fixture-${termId}` },
    bindings: termId === 'wacc' ? [{ binding_id: 'wacc.current', term_id: termId, page_key: 'fixture', field_path: 'cost_of_capital.wacc', context_key: 'fixture', usage_md: '读取合成估值结果中的原值。', limitations_md: '仅为测试。', reviewed_engine_ref: 'fixture' }] : [],
    relations: termId === 'wacc' ? [{ term_id: 'capital-b', title_zh: '测试资本乙', title_en: 'Capital B', relation_type: '下一课' }] : [],
    sources: [{ source_id: 'fixture-source', title: '合成来源', author: '测试', url: 'https://example.org/reference', published_at: null, verified_at: '2026-01-01', section_anchor: 'definition', locator: '测试章节' }],
  };
}

async function mockKnowledge(page: Page, state: 'ready' | 'missing' | 'invalid' = 'ready') {
  await page.route(API_ROUTE, async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/knowledge/index') {
      if (state === 'invalid') return route.fulfill({ status: 503, json: { detail: 'invalid fixture database' } });
      return route.fulfill({ json: state === 'missing' ? { status: 'unavailable', release_id: null, schema_version: 1, terms: [] } : index });
    }
    const match = /^\/api\/knowledge\/terms\/([^/]+)$/.exec(url.pathname);
    if (match) {
      const result = detail(decodeURIComponent(match[1]));
      return route.fulfill({ status: result ? 200 : 404, json: result ?? { detail: 'not found' } });
    }
    return route.fulfill({ json: { admin: false, configured: false } });
  });
}

async function selectText(locator: Locator, start = 0, end?: number) {
  await locator.evaluate((element, offsets) => {
    const node = element.firstChild;
    if (!node || node.nodeType !== Node.TEXT_NODE) throw new Error('Selection fixture must start with a text node');
    const range = document.createRange();
    range.setStart(node, offsets.start);
    range.setEnd(node, offsets.end ?? node.textContent!.length);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    element.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
  }, { start, end });
}

async function openHarness(page: Page) {
  await mockKnowledge(page);
  await page.goto('/tests/fixtures/knowledge.html');
  await expect(page.getByRole('checkbox', { name: '划词解释' })).toBeChecked();
  await expect(page.getByTestId('knowledge-status')).toHaveText('ready');
}

const preview = (page: Page) => page.getByRole('region', { name: '选中文字的词条解释' });
const panel = (page: Page) => page.getByRole('complementary', { name: '估值词条解释' });

test('read-only financial labels resolve exact unique aliases, with explicit IDs taking priority', async ({ page }) => {
  await openHarness(page);
  await expect(page.getByTestId('financial-labels').getByRole('button')).toHaveCount(2);
  const revenue = page.getByTestId('financial-labels').locator('[data-term-id="revenue"]');
  const ebitda = page.getByTestId('financial-labels').locator('[data-term-id="ebitda"]');
  await expect(revenue.getByRole('button')).toHaveCSS('opacity', '0');
  const beforeHover = await revenue.boundingBox();
  await revenue.hover();
  await expect(revenue.getByRole('button')).toHaveCSS('opacity', '1');
  await expect(ebitda.getByRole('button')).toHaveCSS('opacity', '0');
  expect(await revenue.boundingBox()).toEqual(beforeHover);
  await revenue.getByRole('button').click();
  await expect(panel(page).getByRole('heading', { name: '测试收入', exact: true })).toBeVisible();
  await ebitda.hover();
  await ebitda.getByRole('button').click();
  await expect(panel(page).getByRole('heading', { name: '测试息税折旧摊销前利润', exact: true })).toBeVisible();
  await expect(page.getByTestId('ambiguous-label').getByRole('button')).toHaveCount(0);
  await expect(page.getByTestId('financial-value').getByRole('button')).toHaveCount(0);
  await expect(page.getByTestId('editable-row').getByRole('button')).toHaveCount(0);
  await expect(page.getByTestId('explicit-label').locator('[data-term-id="wacc"]')).toHaveCount(1);
});

test('reading outline, hidden answers and course navigation preserve content', async ({ page }) => {
  await mockKnowledge(page);
  await page.goto('/knowledge/wacc');
  const article = page.locator('.knowledge-page-article');
  await expect(article.getByText('先区分输入和结果。', { exact: true })).not.toBeVisible();
  await article.getByText('本文目录', { exact: true }).click();
  await article.getByRole('navigation', { name: '本文目录' }).getByRole('button', { name: '答案与解析', exact: true }).click();
  await expect(article.getByText('先区分输入和结果。', { exact: true })).toBeVisible();
  await expect(article.locator('.knowledge-answer > div')).toBeFocused();
  await article.getByText('展开答案与解析', { exact: true }).click();
  await page.reload();
  await expect(article.locator('.knowledge-answer')).toBeVisible();
  await expect(article.getByText('先区分输入和结果。', { exact: true })).not.toBeVisible();
  await expect(page.getByRole('button', { name: /收藏|打印|保存PDF/ })).toHaveCount(0);
  await expect(page.getByRole('checkbox', { name: '只看收藏' })).toHaveCount(0);
  await article.getByRole('navigation', { name: '课程导航' }).getByRole('button', { name: '下一课：测试资本乙' }).click();
  await expect(page).toHaveURL(/\/knowledge\/capital-b$/);
});

test('current context displays returned values and provenance, updates company and clears without a session', async ({ page }) => {
  await openHarness(page);
  const help = page.locator('[data-binding-id="wacc.current"]').getByRole('button');
  await help.focus();
  await help.press('Enter');
  await panel(page).getByText('本次输入、计算分支与来源', { exact: true }).click();
  await expect(panel(page)).toContainText('10.00%');
  await expect(panel(page)).toContainText('1.25');
  await expect(panel(page)).toContainText('原始合成来源');
  await expect(panel(page)).toContainText('SYNTH:FIRST');
  await expect(panel(page)).toContainText('standard-test-snapshot');
  await page.getByRole('button', { name: '更新合成估值' }).click();
  await expect(panel(page)).toContainText('12.00%');
  await expect(panel(page)).toContainText('SYNTH:SECOND');
  await expect(panel(page)).toContainText('用户合成覆盖');
  await expect(panel(page)).not.toContainText('SYNTH:FIRST');
  await page.getByRole('button', { name: '清除合成估值' }).click();
  await expect(panel(page)).toContainText('选择公司后');
  await expect(panel(page)).not.toContainText('12.00%');
});

test('standalone glossary works without a valuation, searches aliases and follows safe crosslinks', async ({ page }) => {
  await mockKnowledge(page);
  await page.goto('/knowledge');
  const list = page.locator('.knowledge-directory-list li');
  const search = page.getByRole('searchbox', { name: '搜索词条' });
  await expect(list).toHaveCount(10);
  await search.fill('合成样本');
  await expect(list).toHaveCount(10);
  await search.fill('extra-alias-11');
  await expect(list).toHaveCount(1);
  await list.getByRole('link').click();
  await expect(page.getByRole('heading', { name: '扩展词条11', exact: true })).toBeVisible();
  await page.goto('/knowledge/extra-11');
  await expect(list).toHaveCount(10);
  await expect(page.getByRole('heading', { name: '扩展词条11', exact: true })).toBeVisible();
  await search.fill('不存在的词条');
  await expect(page.getByRole('status').filter({ hasText: '没有匹配的词条' })).toHaveText('没有匹配的词条，请尝试其他名称。');
  await page.getByRole('searchbox', { name: '搜索词条' }).fill('capital cost');
  await page.locator('.knowledge-directory-list').getByRole('link', { name: /测试资本成本/ }).click();
  await expect(page).toHaveURL(/\/knowledge\/wacc$/);
  await expect(page.getByRole('heading', { name: '测试资本成本' })).toBeVisible();
  await expect(page.locator('.katex').first()).toBeVisible();
  await expect(page.getByRole('link', { name: '合成来源' })).toHaveAttribute('rel', 'noopener noreferrer');
  await expect(page.locator('.knowledge-article img, .knowledge-article script')).toHaveCount(0);
  await expect(page.getByRole('link', { name: '危险链接' })).toHaveCount(0);
  await expect(page.getByText('危险链接', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: '关联定义' }).click();
  await expect(page).toHaveURL(/\/knowledge\/capital-a$/);
  await expect(page.getByRole('heading', { name: '测试资本甲' })).toBeVisible();
});

test('knowledge home offers published learning, category and topic links while keeping search independent', async ({ page }) => {
  await mockKnowledge(page);
  await page.goto('/knowledge');
  await expect(page.getByRole('heading', { name: '估值知识库', exact: true })).toBeVisible();
  const home = page.locator('.knowledge-home');
  const learning = home.getByRole('region', { name: '估值自学路线' });
  await expect(learning.getByRole('link', { name: '测试学习路线' })).toBeVisible();
  // The publication intentionally omits the other lessons and most topic entries.
  await expect(home.locator('a[href="/knowledge/lesson-risk"]')).toHaveCount(0);
  await expect(home.locator('a[href="/knowledge/currency-wacc"]')).toHaveCount(0);
  const lesson = learning.getByRole('link', { name: '测试第一课' });
  await lesson.focus();
  await lesson.press('Enter');
  await expect(page).toHaveURL(/\/knowledge\/lesson-financials$/);
  await expect(page.getByRole('heading', { name: '测试第一课', exact: true })).toBeVisible();
  await expect(home).toHaveCount(0);
  await page.getByRole('link', { name: '← 知识库首页', exact: true }).click();
  await expect(learning).toBeVisible();
  await home.getByRole('navigation', { name: '风险与折现' }).getByRole('link', { name: '测试资本成本', exact: true }).click();
  await expect(page.getByRole('heading', { name: '测试资本成本', exact: true })).toBeVisible();
  await page.getByRole('link', { name: '← 知识库首页', exact: true }).click();
  await home.getByRole('region', { name: '专题与实践' }).getByRole('link', { name: '测试预测专题' }).click();
  await expect(page.getByRole('heading', { name: '测试预测专题', exact: true })).toBeVisible();
  await page.getByRole('link', { name: '← 知识库首页', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(learning).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole('link', { name: '已有问题？直接搜索词条 →' }).click();
  const search = page.getByRole('searchbox', { name: '搜索词条' });
  await expect(search).toBeFocused();
  await expect(page.locator('.knowledge-directory-list li')).toHaveCount(10);
  await search.fill('extra-alias-11');
  await expect(page.locator('.knowledge-directory-list li')).toHaveCount(1);
  await page.locator('.knowledge-directory-list li').getByRole('link').click();
  await expect(page.getByRole('heading', { name: '扩展词条11', exact: true })).toBeVisible();
});

test('unpublished and invalid databases have distinct recoverable states', async ({ page }) => {
  await mockKnowledge(page, 'missing');
  await page.goto('/knowledge/wacc');
  await expect(page.getByRole('heading', { name: '词条库尚未发布' })).toBeVisible();
  await expect(page.locator('.knowledge-home')).toHaveCount(0);
  await page.unroute(API_ROUTE);
  await mockKnowledge(page, 'invalid');
  await page.goto('/knowledge');
  await expect(page.getByText('词条库暂时无法读取，请稍后重试。', { exact: true })).toBeVisible();
  await page.unroute(API_ROUTE);
  await mockKnowledge(page);
  await page.getByRole('button', { name: '重试读取词条库', exact: true }).click();
  await expect(page.getByRole('searchbox', { name: '搜索词条' })).toBeVisible();
  await expect(page.locator('.knowledge-directory-list').getByRole('link', { name: /测试资本成本/ })).toBeVisible();
});

test('a partial annotated label opens its exact term without replacing selection or stealing focus', async ({ page }, testInfo) => {
  await openHarness(page);
  await page.getByTestId('focus-anchor').focus();
  await selectText(page.locator('[data-term-id="wacc"][data-binding-id="wacc.current"]'), 1, 4);
  await expect(preview(page)).toContainText('用于验证交互的资本成本简释。');
  await expect(page.getByTestId('focus-anchor')).toBeFocused();
  expect(await page.evaluate(() => window.getSelection()?.toString())).toBe('ACC');
  await preview(page).getByRole('button', { name: '展开词条' }).click();
  await expect(panel(page).getByRole('heading', { name: '测试资本成本' })).toBeVisible();
  await expect(panel(page)).toContainText('10.00%');
  await page.getByRole('button', { name: '更新合成估值' }).click();
  await expect(panel(page)).toContainText('12.00%');
  if (process.env.KNOWLEDGE_CAPTURE) await page.screenshot({ path: testInfo.outputPath('wide-panel.png'), fullPage: true });
  await panel(page).getByRole('button', { name: '关闭词条解释' }).click();
  await expect(panel(page)).toHaveCount(0);
});

test('unmarked aliases normalize text and ambiguous aliases require an explicit choice', async ({ page }) => {
  await openHarness(page);
  await selectText(page.getByTestId('alias'));
  await expect(preview(page)).toContainText('测试资本成本');
  await selectText(page.getByTestId('ambiguous'));
  await expect(preview(page)).toContainText('请选择要了解的概念');
  await expect(preview(page).getByRole('button', { name: /测试资本甲/ })).toBeVisible();
  await preview(page).getByRole('button', { name: /测试资本乙/ }).click();
  await expect(panel(page).getByRole('heading', { name: '测试资本乙' })).toBeVisible();
});

test('editing, ignored areas, numbers and ranges across cells do not trigger explanations', async ({ page }) => {
  await openHarness(page);
  for (const id of ['ignored', 'editable-text', 'numeric', 'long-selection']) {
    await selectText(page.getByTestId(id));
    await expect(preview(page)).toHaveCount(0);
  }
  await selectText(page.getByTestId('editable-row').locator('td'));
  await expect(preview(page)).toHaveCount(0);
  await page.getByTestId('two-cells').evaluate(row => {
    const range = document.createRange();
    range.setStart(row.children[0].firstChild!, 0);
    range.setEnd(row.children[1].firstChild!, 2);
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    row.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
  });
  await expect(preview(page)).toHaveCount(0);
  const ordinaryInput = page.getByRole('textbox', { name: '普通输入' });
  await ordinaryInput.evaluate(element => (element as HTMLInputElement).select());
  await ordinaryInput.dispatchEvent('pointerup');
  await expect(preview(page)).toHaveCount(0);
  await page.getByTestId('editable-row').locator('td').dblclick();
  const editor = page.getByTestId('editable-row').getByRole('textbox');
  await expect(editor).toBeFocused();
  await editor.fill('Edited fixture');
  await editor.press('Enter');
  await expect(page.getByTestId('committed-cell')).toHaveText('Edited fixture');
  await expect(preview(page)).toHaveCount(0);
});

test('selection preference persists while explicit keyboard help remains available', async ({ page }) => {
  await openHarness(page);
  await page.getByRole('checkbox', { name: '划词解释' }).uncheck();
  await page.reload();
  await expect(page.getByRole('checkbox', { name: '划词解释' })).not.toBeChecked();
  await selectText(page.locator('[data-term-id="wacc"][data-binding-id="wacc.current"]'), 1, 4);
  await expect(preview(page)).toHaveCount(0);
  const help = page.locator('[data-binding-id="wacc.current"]').getByRole('button');
  await expect(help).toHaveCSS('opacity', '0');
  await help.focus();
  await expect(help).toHaveCSS('opacity', '1');
  await help.press('Enter');
  await expect(panel(page).getByRole('heading', { name: '测试资本成本' })).toBeVisible();
});

test('narrow screens use a modal with keyboard containment and return focus on Escape', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openHarness(page);
  const help = page.locator('[data-binding-id="wacc.current"]').getByRole('button');
  await help.focus();
  await help.press('Enter');
  const dialog = page.getByRole('dialog', { name: '估值词条解释' });
  await expect(dialog.getByRole('heading', { name: '测试资本成本' })).toBeVisible();
  await expect.poll(() => dialog.evaluate(element => element.contains(document.activeElement))).toBe(true);
  for (let i = 0; i < 9; i++) {
    await page.keyboard.press('Tab');
    await expect.poll(() => dialog.evaluate(element => element.contains(document.activeElement))).toBe(true);
  }
  expect(await dialog.evaluate(element => element.getBoundingClientRect().width)).toBeLessThanOrEqual(390);
  if (process.env.KNOWLEDGE_CAPTURE) await page.screenshot({ path: testInfo.outputPath('narrow-dialog.png'), fullPage: true });
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(help).toBeFocused();
});

test('touch users can open term help without hovering', async ({ browser }) => {
  const context = await browser.newContext({ baseURL: 'http://127.0.0.1:4173', viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  try {
    const page = await context.newPage();
    await openHarness(page);
    const help = page.locator('[data-binding-id="wacc.current"]').getByRole('button');
    await expect(help).toHaveCSS('opacity', '1');
    await help.tap();
    await expect(page.getByRole('dialog', { name: '估值词条解释' }).getByRole('heading', { name: '测试资本成本' })).toBeVisible();
  } finally { await context.close(); }
});

test('a newly published release refreshes the open article and selection summaries together', async ({ page }) => {
  let release = 'fixture-r1';
  await page.route('**/api/knowledge/**', async route => {
    const url = new URL(route.request().url());
    const updated = release === 'fixture-r2';
    if (url.pathname.endsWith('/index')) return route.fulfill({ json: { ...index, release_id: release,
      terms: terms.map(term => ({ ...term, summary: updated ? '新版合成简释。' : term.summary })) } });
    const result = detail('wacc', release)!;
    if (updated) { result.term.summary = '新版合成简释。'; result.term.body_md = '新版合成正文。'; }
    return route.fulfill({ json: result });
  });
  await page.goto('/tests/fixtures/knowledge.html');
  await expect(page.getByTestId('knowledge-status')).toHaveText('ready');
  await page.locator('[data-binding-id="wacc.current"]').hover();
  await page.locator('[data-binding-id="wacc.current"]').getByRole('button').click();
  await expect(panel(page)).toContainText('仅为浏览器测试文本。');
  release = 'fixture-r2';
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(panel(page)).toContainText('新版合成正文。');
  await expect(panel(page)).toContainText('新版合成简释。');
  await panel(page).getByRole('button', { name: '关闭词条解释' }).click();
  await selectText(page.locator('[data-term-id="wacc"][data-binding-id="wacc.current"]'), 1, 4);
  await expect(preview(page)).toContainText('新版合成简释。');
  await expect(preview(page)).not.toContainText('用于验证交互的资本成本简释。');
});
