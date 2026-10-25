// HQ check on Pascal's UI (2026-10-25, verification only): for the three showcase buildings, is the
// high-quality model offered and shown, and does the browser preview (standard) still work?
//   PW_MODULE=<playwright-core> ARINAV_URL=... node scripts/e2e/hq_check.mjs [--out dir]
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_MODULE || 'playwright-core');
const args = process.argv.slice(2);
const OUT = args.includes('--out') ? args[args.indexOf('--out') + 1] : 'docs/core-final/e2e';
const BASE = process.env.ARINAV_URL || 'http://localhost:4321';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const SHOWCASE = ['proc-factory-dev-003', 'proc-campus-dev-001', 'swiss-1689'];
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
await page.route('**/api/pipeline-runs', (r) => (r.request().method() === 'POST' ? r.fulfill({ status: 503, body: '{}' }) : r.continue()));
await page.goto(BASE + '?profile=showcase', { waitUntil: 'networkidle' });
await page.addStyleTag({ content: 'astro-dev-toolbar{display:none!important}' });
const results = [];
for (const id of SHOWCASE) {
  const row = { id };
  try {
    if (await page.getByRole('button', { name: 'Show panel' }).count()) await page.getByRole('button', { name: 'Show panel' }).click({ force: true });
    if (await page.getByRole('tab', { name: 'Location' }).count()) await page.getByRole('tab', { name: 'Location' }).click();
    await page.selectOption('#building-select', id);
    await page.waitForTimeout(6000);
    const hq = page.getByRole('button', { name: 'High quality' });
    const preview = page.getByRole('button', { name: 'Preview' });
    row.hq_offered = (await hq.count()) > 0 && (await hq.isEnabled());
    row.hq_badge = await page.locator('.topbar').innerText().then((t) => /\bHQ\b/.test(t));
    if (row.hq_offered && (await hq.getAttribute('aria-pressed')) !== 'true') {
      await hq.click();
      await page.waitForTimeout(5000);
    }
    row.hq_shown = row.hq_offered && (await hq.getAttribute('aria-pressed')) === 'true';
    await page.screenshot({ path: join(OUT, `HQ-${id}-hq-1440.png`) });
    if (await preview.count()) {
      await preview.click();
      await page.waitForTimeout(3000);
      row.preview_shown = (await preview.getAttribute('aria-pressed')) === 'true';
      await page.screenshot({ path: join(OUT, `HQ-${id}-preview-1440.png`) });
      if (row.hq_offered) await hq.click();
      await page.waitForTimeout(2500);
    }
  } catch (e) {
    row.error = String(e).slice(0, 300);
  }
  results.push(row);
  console.log(JSON.stringify(row));
}
writeFileSync(join(OUT, 'hq_check.json'), JSON.stringify({ base: BASE, results, page_errors: errors.slice(0, 5) }, null, 1));
await browser.close();
