// 本地可重复浏览器验收：复用已安装playwright-core与Chromium，不新增应用依赖。
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { mkdtempSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { chromium } = require(process.env.ALPHALAKE_PLAYWRIGHT_MODULE || 'playwright-core');
const root = path.resolve(__dirname, '../../..');

(async () => {
  assert(process.env.US_CN_HK_DB_PATH, 'US_CN_HK_DB_PATH must select a rebuilt v2 snapshot containing Anker and Moutai');
  const temporary = mkdtempSync(path.join(tmpdir(), 'alphalake-web-smoke-'));
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  const server = spawn(process.env.ALPHALAKE_TEST_PYTHON || 'python3', ['-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: root, env: { ...process.env, PYTHONPATH: 'valuation/backend', ALPHALAKE_VALUATION_RUN_DIR: path.join(temporary, 'runs') }, stdio: ['ignore', 'ignore', 'pipe'],
  });
  let logs = ''; server.stderr.on('data', chunk => { logs += chunk; });
  let browser;
  try {
    let ready = false;
    for (let i = 0; i < 60; i++) {
      if (server.exitCode != null) throw new Error(logs);
      try { ready = (await fetch(`http://127.0.0.1:${port}/health`)).ok; } catch { /* 服务正在启动 */ }
      if (ready) break;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    assert(ready, logs);
    browser = await chromium.launch({ headless: true, executablePath: process.env.ALPHALAKE_CHROMIUM_PATH, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${port}`);
    for (const code of ['300866', '600519', '002032']) {
      await page.getByPlaceholder('Ticker or company name…').fill(code);
      await page.getByRole('button', { name: 'Search', exact: true }).click();
      await page.getByRole('button', { name: 'Continue →' }).click();
      const run = page.getByRole('button', { name: '→ Value from Database' });
      await run.waitFor();
      assert.equal(await page.getByLabel('估值政策文件').count(), 0);
      const sent = page.waitForRequest(r => r.url().endsWith('/api/valuation/from-database') && r.method() === 'POST');
      await run.click();
      assert.deepEqual(Object.keys((await sent).postDataJSON()).sort(), ['risk_free_rate', 'ticker']);
      await page.getByText(/原生估值数据尚未达标/).waitFor();
      await page.goto(`http://127.0.0.1:${port}`);
    }
    // 原内置示例验证原界面仍能工作，不冒充TDX公司验收。
    const demoResponse = page.waitForResponse(r => r.url().endsWith('/api/valuation') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Or try the demo data' }).click();
    const demo = await demoResponse;
    assert.equal(demo.status(), 200, (await demo.text()) + logs);
    const baseline = await demo.json();
    const updated = await page.request.patch(`http://127.0.0.1:${port}/api/valuation/${baseline.id}`, {
      data: { overrides: { 'valuation_assumptions.revenue_growth_next_year': 0.09 } },
    });
    assert.equal(updated.status(), 200, await updated.text());
    const changed = await updated.json();
    assert.notEqual(changed.final.value_per_share, baseline.final.value_per_share);
    assert.match(changed.source_metadata['valuation_assumptions.revenue_growth_next_year'], /User override/);
    assert.equal((await page.request.post(`http://127.0.0.1:${port}/api/valuation/${baseline.id}/sensitivity`)).status(), 200);
    assert.equal((await page.request.get(`http://127.0.0.1:${port}/api/valuation/${baseline.id}/export/full-workbook`)).status(), 200);
    await page.getByRole('button', { name: 'New Valuation' }).waitFor();
    assert(await page.locator('a[href="/wacc"]').count() > 0);
    assert(await page.locator('a[href="/stories"]').count() > 0);
    assert.equal(await page.getByRole('heading', { name: /条件估值：/ }).count(), 0);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ original_request: true, no_policy_ui: true, real_tdx_blocked: 3, original_demo_navigation: true, original_patch: true, sensitivity: true, workbook_export: true, browser_errors: errors }));
  } finally {
    if (browser) await browser.close();
    server.kill();
    if (server.exitCode === null) await new Promise(resolve => server.once('exit', resolve));
    rmSync(temporary, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
