// 本地可重复浏览器验收：复用已安装playwright-core与Chromium，不新增应用依赖。
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { mkdtempSync, rmSync, readFileSync } = require('node:fs');
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
    async function select(code) {
      await page.getByPlaceholder('Ticker or company name…').fill(code);
      await page.getByRole('button', { name: 'Search', exact: true }).click();
      await page.getByRole('button', { name: 'Continue →' }).click();
      await page.getByLabel('估值政策文件').waitFor();
    }
    await select('300866');
    const run = page.getByRole('button', { name: '→ Value from Database' });
    assert(await run.isDisabled());
    await page.getByLabel('估值政策文件').setInputFiles(path.join(root, 'valuation/examples/sqlite-anker-2026H1.json'));
    await run.click();
    await page.getByRole('heading', { name: /条件估值：/ }).waitFor();
    assert.equal(await page.getByRole('heading', { name: /条件估值：/ }).count(), 1);
    await page.getByText('资产负债表（', { exact: false }).waitFor();
    assert((await page.locator('body').innerText()).includes('预测现金流（非报表事实）'));
    if (process.env.ALPHALAKE_SCREENSHOT) await page.screenshot({ path: process.env.ALPHALAKE_SCREENSHOT });
    await page.getByRole('button', { name: 'New Valuation' }).click();
    await select('600519');
    await page.getByLabel('估值政策文件').setInputFiles(path.join(root, 'valuation/examples/sqlite-anker-2026H1.json'));
    await page.getByRole('alert').filter({ hasText: '政策公司或报告期' }).waitFor();
    assert(await page.getByRole('button', { name: '→ Value from Database' }).isDisabled());
    const policy = JSON.parse(readFileSync(path.join(root, 'valuation/examples/sqlite-anker-2026H1.json')));
    policy.code = '600519'; // 诊断：普通非金融模型必须拒绝茅台的金融业务。
    await page.getByLabel('估值政策文件').setInputFiles({ name: 'boundary-probe.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(policy)) });
    await page.getByRole('button', { name: '→ Value from Database' }).click();
    await page.getByText(/financial operations require separate model/).waitFor();
    assert.equal(await page.getByRole('heading', { name: /条件估值：/ }).count(), 0);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ search: true, explicit_policy: true, valuation: true, statements: true, mismatched_policy_rejected: true, financial_scope_rejected: true, browser_errors: errors }));
  } finally {
    if (browser) await browser.close();
    server.kill();
    if (server.exitCode === null) await new Promise(resolve => server.once('exit', resolve));
    rmSync(temporary, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
